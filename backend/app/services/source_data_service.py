from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
import re
import time
from sqlalchemy.orm import Session
from app.core.logging import get_logger
from app.models.models import SysOperationLog, SysDataSource, SysDomain, generate_id

logger = get_logger(__name__)


class SourceDataService:
    # Oracle Thin 驱动在一次抓取数千列的宽行时可能出现 DPY-5003。
    # 样例仅用于页面/LLM 辅助，不应阻断完整字段元数据的读取。
    MAX_SAMPLE_COLUMNS = 60
    MAX_COLUMNS_FOR_ROW_SAMPLE = 200

    UNSUPPORTED_MAPPING_TYPES = {
        "BLOB",
        "CLOB",
        "NCLOB",
        "BFILE",
        "LONG",
        "LONG RAW",
        "XMLTYPE",
    }
    MAPPING_EXCLUDED_TABLE_PREFIXES = ("SYS_",)

    def __init__(self, db: Session):
        self.db = db

    def _log_operation(self, op_type: str, target: str, detail: str):
        """记录操作日志"""
        log = SysOperationLog(
            log_id=generate_id("op"),
            operation_type=op_type,
            operation_target=target,
            operation_detail=detail
        )
        self.db.add(log)
        self.db.commit()

    def _normalize_sql(self, sql: str) -> str:
        return " ".join((sql or "").split())

    def _is_retryable_remote_error(self, exc: Exception) -> bool:
        message = str(exc or "").upper()
        retryable_tokens = [
            "DPY-4011",
            "DPY-1001",
            "NOT CONNECTED",
            "CLOSED THE CONNECTION",
            "CONNECTION WAS CLOSED",
            "ORA-03113",
            "ORA-03114",
            "ORA-03135",
        ]
        return any(token in message for token in retryable_tokens)

    def _close_remote_resources(self, cursor=None, connection=None):
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if connection:
                connection.close()
        except Exception:
            pass

    def _run_with_remote_retry(self, source: SysDataSource, action_label: str, action, max_attempts: int = 2):
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            connection = None
            cursor = None
            try:
                connection = self._connect_to_oracle(source)
                cursor = connection.cursor()
                return action(connection, cursor)
            except Exception as exc:
                last_exc = exc
                should_retry = attempt < max_attempts and self._is_retryable_remote_error(exc)
                logger.warning(
                    "Remote action failed: action=%s source=%s attempt=%s/%s retry=%s error=%s",
                    action_label,
                    source.source_name,
                    attempt,
                    max_attempts,
                    should_retry,
                    str(exc),
                )
                if not should_retry:
                    raise
                time.sleep(0.5)
            finally:
                self._close_remote_resources(cursor, connection)

        if last_exc:
            raise last_exc

    def _execute_remote_sql(self, cursor, source: SysDataSource, sql: str, params: Optional[Dict[str, Any]] = None):
        logger.info(
            "REMOTE SQL execute: source=%s schema=%s sql=%s",
            source.source_name,
            source.schema_name or source.username,
            self._normalize_sql(sql),
        )
        logger.debug("REMOTE SQL params: %s", params or {})
        try:
            cursor.execute(sql, params or {})
        except Exception as exc:
            logger.exception(
                "REMOTE SQL failed: source=%s sql=%s error=%s",
                source.source_name,
                self._normalize_sql(sql),
                str(exc),
            )
            raise

    def _fetchall_logged(self, cursor, source: SysDataSource, label: str) -> List[Any]:
        rows = cursor.fetchall()
        preview = [
            [self._normalize_cell_value(value) for value in row]
            for row in rows[:5]
        ]
        logger.info(
            "REMOTE SQL result: source=%s label=%s row_count=%s preview=%s",
            source.source_name,
            label,
            len(rows),
            preview,
        )
        return rows

    def _fetchone_logged(self, cursor, source: SysDataSource, label: str):
        row = cursor.fetchone()
        preview = [self._normalize_cell_value(value) for value in row] if row else None
        logger.info(
            "REMOTE SQL result: source=%s label=%s row=%s",
            source.source_name,
            label,
            preview,
        )
        return row

    def get_available_data_sources(self, domain_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取已启用的数据源配置列表"""
        logger.info("Load available data sources: domain_id=%s", domain_id)
        query = (
            self.db.query(SysDataSource, SysDomain.domain_name)
            .outerjoin(SysDomain, SysDomain.domain_id == SysDataSource.business_domain_id)
            .filter(SysDataSource.is_active == "Y")
        )
        if domain_id:
            query = query.filter(SysDataSource.business_domain_id == domain_id)
        sources = query.order_by(SysDataSource.is_default.desc(), SysDataSource.created_at.desc()).all()
        return [
            {
                "source_id": source.source_id,
                "source_name": source.source_name,
                "source_desc": source.source_desc,
                "db_type": source.db_type,
                "schema_name": source.schema_name,
                "username": source.username,
                "host": source.host,
                "port": source.port,
                "service_name": source.service_name,
                "sid": source.sid,
                "business_domain_id": source.business_domain_id,
                "business_domain_name": domain_name,
                "is_default": source.is_default,
                "connection_status": source.connection_status,
            }
            for source, domain_name in sources
        ]

    def get_source_schemas(self, source_id: str) -> Dict[str, Any]:
        """获取数据源可用 schema 列表"""
        logger.info("Load source schemas: source_id=%s", source_id)
        source = self._get_data_source(source_id)
        schemas: List[str] = []

        def action(_connection, cursor):
            nonlocal schemas
            self._execute_remote_sql(cursor, source, "SELECT USER FROM DUAL")
            connected_user = self._fetchone_logged(cursor, source, "connected_user")[0]

            try:
                self._execute_remote_sql(cursor, source, "SELECT USERNAME FROM ALL_USERS ORDER BY USERNAME")
                schemas = [row[0] for row in self._fetchall_logged(cursor, source, "all_users") if row and row[0]]
            except Exception:
                schemas = []

            preferred = [
                (source.schema_name or "").upper(),
                source.username.upper() if source.username else "",
                connected_user.upper() if connected_user else "",
            ]
            for schema_name in preferred:
                if schema_name and schema_name not in schemas:
                    schemas.insert(0, schema_name)

            return {
                "connected_user": connected_user,
                "default_schema": (source.schema_name or connected_user or source.username or "").upper(),
                "schemas": schemas,
            }

        try:
            return self._run_with_remote_retry(source, f"get_source_schemas:{source_id}", action)
        finally:
            logger.info("Loaded schemas: source_id=%s schema_count=%s", source_id, len(schemas))

    def get_remote_tables(
        self,
        source_id: str,
        schema: Optional[str] = None,
        prefix: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按数据源和 schema 获取表列表"""
        logger.info(
            "Load remote tables: source_id=%s schema=%s prefix=%s search=%s",
            source_id,
            schema,
            prefix,
            search,
        )
        source = self._get_data_source(source_id)
        tables = []
        schema_name = ""

        def action(_connection, cursor):
            nonlocal tables, schema_name
            self._execute_remote_sql(cursor, source, "SELECT USER FROM DUAL")
            connected_user = self._fetchone_logged(cursor, source, "connected_user")[0]
            schema_name = (schema or source.schema_name or connected_user or source.username).upper()

            conditions = ["t.OWNER = :owner"]
            params: Dict[str, Any] = {"owner": schema_name}

            if prefix:
                conditions.append("t.TABLE_NAME LIKE :prefix")
                params["prefix"] = f"{prefix.upper()}%"

            if search:
                conditions.append("t.TABLE_NAME LIKE :search")
                params["search"] = f"%{search.upper()}%"

            where_clause = " AND ".join(conditions)
            sql = f"""
                SELECT
                    t.OWNER,
                    t.TABLE_NAME,
                    c.COMMENTS,
                    t.NUM_ROWS
                FROM ALL_TABLES t
                LEFT JOIN ALL_TAB_COMMENTS c
                    ON c.OWNER = t.OWNER
                   AND c.TABLE_NAME = t.TABLE_NAME
                   AND c.TABLE_TYPE = 'TABLE'
                WHERE {where_clause}
                ORDER BY t.TABLE_NAME
            """
            self._execute_remote_sql(cursor, source, sql, params)
            rows = self._fetchall_logged(cursor, source, f"remote_tables:{schema_name}")

            tables = [
                {
                    "owner": row[0],
                    "table_name": row[1],
                    "comments": self._normalize_cell_value(row[2]),
                    "num_rows": self._normalize_cell_value(row[3]) or 0,
                }
                for row in rows
            ]

            return {
                "schema": schema_name,
                "connected_user": connected_user,
                "tables": tables,
            }

        try:
            return self._run_with_remote_retry(source, f"get_remote_tables:{source_id}:{schema or ''}", action)
        finally:
            logger.info("Loaded remote tables: source_id=%s schema=%s table_count=%s", source_id, schema_name, len(tables))
            logger.debug("Remote tables preview: %s", tables[:5])

    def get_remote_table_detail(
        self,
        source_id: str,
        table_name: str,
        schema: Optional[str] = None,
        sample_limit: int = 10,
    ) -> Dict[str, Any]:
        """获取外部数据源表结构和样例数据"""
        logger.info(
            "Load remote table detail: source_id=%s schema=%s table=%s sample_limit=%s",
            source_id,
            schema,
            table_name,
            sample_limit,
        )
        source = self._get_data_source(source_id)
        columns = []
        preview_rows = []
        preview_sql = ""

        def action(_connection, cursor):
            nonlocal columns, preview_rows, preview_sql
            self._execute_remote_sql(cursor, source, "SELECT USER FROM DUAL")
            connected_user = self._fetchone_logged(cursor, source, "connected_user")[0]
            owner = (schema or source.schema_name or connected_user or source.username).upper()

            column_sql = """
                SELECT
                    c.OWNER,
                    c.TABLE_NAME,
                    c.COLUMN_NAME,
                    c.DATA_TYPE || CASE
                        WHEN c.DATA_TYPE IN ('VARCHAR2','VARCHAR','CHAR','NCHAR','NVARCHAR2','RAW') THEN '(' || c.DATA_LENGTH || ')'
                        WHEN c.DATA_TYPE = 'NUMBER' AND c.DATA_PRECISION IS NOT NULL AND c.DATA_SCALE IS NOT NULL THEN '(' || c.DATA_PRECISION || ',' || c.DATA_SCALE || ')'
                        WHEN c.DATA_TYPE = 'NUMBER' AND c.DATA_PRECISION IS NOT NULL THEN '(' || c.DATA_PRECISION || ')'
                        ELSE ''
                    END AS DATA_TYPE,
                    CASE WHEN c.NULLABLE = 'Y' THEN 'Y' ELSE 'N' END AS NULLABLE,
                    c.DATA_DEFAULT,
                    c.COLUMN_ID,
                    com.COMMENTS
                FROM ALL_TAB_COLUMNS c
                LEFT JOIN ALL_COL_COMMENTS com
                    ON com.OWNER = c.OWNER
                   AND com.TABLE_NAME = c.TABLE_NAME
                   AND com.COLUMN_NAME = c.COLUMN_NAME
                WHERE c.OWNER = :owner
                  AND c.TABLE_NAME = :table_name
                ORDER BY c.COLUMN_ID
            """
            self._execute_remote_sql(cursor, source, column_sql, {"owner": owner, "table_name": table_name.upper()})
            column_rows = self._fetchall_logged(cursor, source, f"table_columns:{owner}.{table_name.upper()}")

            if not column_rows:
                raise ValueError(f"未找到表 {owner}.{table_name}")

            actual_owner = column_rows[0][0]
            actual_table_name = column_rows[0][1]
            columns = [
                {
                    "column_name": row[2],
                    "data_type": row[3],
                    "nullable": row[4],
                    "default_value": self._normalize_cell_value(row[5]),
                    "column_id": row[6],
                    "comments": self._normalize_cell_value(row[7]),
                    "mapping_supported": self._is_mapping_supported_type(row[3]),
                    "mapping_excluded_reason": None if self._is_mapping_supported_type(row[3]) else "LOB/CLOB/BLOB/XMLTYPE/LONG 等大对象字段不参与属性映射",
                }
                for row in column_rows
            ]

            self._execute_remote_sql(
                cursor,
                source,
                """
                SELECT COMMENTS
                FROM ALL_TAB_COMMENTS
                WHERE OWNER = :owner
                  AND TABLE_NAME = :table_name
                  AND TABLE_TYPE = 'TABLE'
                """,
                {"owner": actual_owner, "table_name": actual_table_name},
            )
            table_comment_row = self._fetchone_logged(cursor, source, f"table_comment:{actual_owner}.{actual_table_name}")
            table_comment = self._normalize_cell_value(table_comment_row[0]) if table_comment_row else None

            preview_columns = []
            if len(columns) > self.MAX_COLUMNS_FOR_ROW_SAMPLE:
                logger.info(
                    "Skip row sample for wide table: source=%s table=%s.%s column_count=%s limit=%s",
                    source.source_name,
                    actual_owner,
                    actual_table_name,
                    len(columns),
                    self.MAX_COLUMNS_FOR_ROW_SAMPLE,
                )
            else:
                preview_source_columns = [
                    item["column_name"]
                    for item in columns
                    if item["mapping_supported"]
                ][:self.MAX_SAMPLE_COLUMNS]
                if not preview_source_columns:
                    preview_source_columns = [item["column_name"] for item in columns[:self.MAX_SAMPLE_COLUMNS]]
                safe_owner = self._quote_identifier(actual_owner)
                safe_table_name = self._quote_identifier(actual_table_name)
                projection = ", ".join(self._quote_identifier(name) for name in preview_source_columns)
                preview_sql = f"""
                    SELECT {projection}
                    FROM {safe_owner}.{safe_table_name}
                    FETCH FIRST {max(1, min(sample_limit, 100))} ROWS ONLY
                """
                try:
                    self._execute_remote_sql(cursor, source, preview_sql)
                    preview_columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    preview_rows = [
                        {
                            preview_columns[idx]: self._normalize_cell_value(value)
                            for idx, value in enumerate(row)
                        }
                        for row in self._fetchall_logged(cursor, source, f"table_preview:{actual_owner}.{actual_table_name}")
                    ]
                except Exception as exc:
                    logger.warning(
                        "Skip unavailable table sample: source=%s table=%s.%s error=%s",
                        source.source_name,
                        actual_owner,
                        actual_table_name,
                        exc,
                    )
                    preview_columns = []
                    preview_rows = []

            return {
                "owner": actual_owner,
                "table_name": actual_table_name,
                "table_comment": table_comment,
                "columns": columns,
                "sample_columns": preview_columns,
                "sample_rows": preview_rows,
                "sample_limit": sample_limit,
                "preview_sql": preview_sql.strip(),
            }

        try:
            return self._run_with_remote_retry(source, f"get_remote_table_detail:{source_id}:{table_name}", action)
        finally:
            logger.info(
                "Loaded table detail: source_id=%s table=%s columns=%s sample_rows=%s",
                source_id,
                table_name,
                len(columns),
                len(preview_rows),
            )
            logger.debug("Table detail sample preview: %s", preview_rows[:3])

    def get_remote_object_metadata(
        self,
        source_id: str,
        object_names: List[str],
        schema: Optional[str] = None,
    ) -> Dict[str, Any]:
        """批量读取目标 Oracle 中已部署表/视图的实际对象、列和主键信息。"""
        source = self._get_data_source(source_id)
        normalized_names = []
        for name in object_names or []:
            normalized = str(name or "").strip().upper()
            if normalized and normalized not in normalized_names:
                normalized_names.append(normalized)
        if not normalized_names:
            return {"source_id": source_id, "schema": schema or source.schema_name, "objects": []}

        def action(_connection, cursor):
            self._execute_remote_sql(cursor, source, "SELECT USER FROM DUAL")
            connected_user = self._fetchone_logged(cursor, source, "connected_user")[0]
            owner = (schema or source.schema_name or connected_user or source.username).upper()
            binds = {"owner": owner, **{f"name_{index}": name for index, name in enumerate(normalized_names)}}
            in_clause = ", ".join(f":name_{index}" for index in range(len(normalized_names)))
            self._execute_remote_sql(
                cursor,
                source,
                f"""
                SELECT OBJECT_NAME, OBJECT_TYPE
                FROM ALL_OBJECTS
                WHERE OWNER = :owner
                  AND OBJECT_NAME IN ({in_clause})
                  AND OBJECT_TYPE IN ('TABLE', 'VIEW')
                """,
                binds,
            )
            object_rows = self._fetchall_logged(cursor, source, f"ontology_objects:{owner}")
            object_types = {row[0]: row[1] for row in object_rows}
            if not object_types:
                return {"source_id": source_id, "source_name": source.source_name, "schema": owner, "objects": []}

            actual_names = list(object_types.keys())
            actual_binds = {"owner": owner, **{f"object_{index}": name for index, name in enumerate(actual_names)}}
            actual_in_clause = ", ".join(f":object_{index}" for index in range(len(actual_names)))
            self._execute_remote_sql(
                cursor,
                source,
                f"""
                SELECT c.TABLE_NAME, c.COLUMN_NAME,
                       c.DATA_TYPE || CASE
                           WHEN c.DATA_TYPE IN ('VARCHAR2','VARCHAR','CHAR','NCHAR','NVARCHAR2','RAW') THEN '(' || c.DATA_LENGTH || ')'
                           WHEN c.DATA_TYPE = 'NUMBER' AND c.DATA_PRECISION IS NOT NULL AND c.DATA_SCALE IS NOT NULL THEN '(' || c.DATA_PRECISION || ',' || c.DATA_SCALE || ')'
                           WHEN c.DATA_TYPE = 'NUMBER' AND c.DATA_PRECISION IS NOT NULL THEN '(' || c.DATA_PRECISION || ')'
                           ELSE ''
                       END AS DATA_TYPE,
                       c.NULLABLE, c.COLUMN_ID, com.COMMENTS
                FROM ALL_TAB_COLUMNS c
                LEFT JOIN ALL_COL_COMMENTS com
                  ON com.OWNER = c.OWNER AND com.TABLE_NAME = c.TABLE_NAME AND com.COLUMN_NAME = c.COLUMN_NAME
                WHERE c.OWNER = :owner
                  AND c.TABLE_NAME IN ({actual_in_clause})
                ORDER BY c.TABLE_NAME, c.COLUMN_ID
                """,
                actual_binds,
            )
            column_rows = self._fetchall_logged(cursor, source, f"ontology_columns:{owner}")

            self._execute_remote_sql(
                cursor,
                source,
                f"""
                SELECT cc.TABLE_NAME, cc.COLUMN_NAME
                FROM ALL_CONSTRAINTS con
                JOIN ALL_CONS_COLUMNS cc
                  ON cc.OWNER = con.OWNER AND cc.CONSTRAINT_NAME = con.CONSTRAINT_NAME
                WHERE con.OWNER = :owner
                  AND con.CONSTRAINT_TYPE = 'P'
                  AND cc.TABLE_NAME IN ({actual_in_clause})
                """,
                actual_binds,
            )
            primary_key_rows = self._fetchall_logged(cursor, source, f"ontology_primary_keys:{owner}")
            primary_keys = {(row[0], row[1]) for row in primary_key_rows}
            columns_by_object: Dict[str, List[Dict[str, Any]]] = {name: [] for name in actual_names}
            for row in column_rows:
                columns_by_object.setdefault(row[0], []).append({
                    "property_name": row[1],
                    "property_display_name": self._normalize_cell_value(row[5]),
                    "data_type": row[2],
                    "is_nullable": "Y" if row[3] == "Y" else "N",
                    "order_num": row[4],
                    "is_primary_key": "Y" if (row[0], row[1]) in primary_keys else "N",
                    "source_mark": "ACTUAL",
                })
            return {
                "source_id": source_id,
                "source_name": source.source_name,
                "schema": owner,
                "objects": [
                    {"object_name": name, "object_type": object_types[name], "columns": columns_by_object.get(name, [])}
                    for name in actual_names
                ],
            }

        return self._run_with_remote_retry(source, f"get_remote_ontology_objects:{source_id}", action)

    def get_remote_property_graphs(self, source_id: str, schema: Optional[str] = None) -> Dict[str, Any]:
        """读取 Oracle 数据源中真实存在的 Property Graph（Graph View）对象。"""
        source = self._get_data_source(source_id)
        if (source.db_type or "").lower() != "oracle":
            raise ValueError("属性图对象仅支持 Oracle 数据源")

        def action(_connection, cursor):
            self._execute_remote_sql(cursor, source, "SELECT USER FROM DUAL")
            connected_user = self._fetchone_logged(cursor, source, "property_graph_connected_user")[0]
            owner = (schema or source.schema_name or connected_user or source.username).upper()
            self._execute_remote_sql(
                cursor,
                source,
                """
                SELECT OWNER, GRAPH_NAME
                FROM ALL_PROPERTY_GRAPHS
                WHERE OWNER = :owner
                ORDER BY GRAPH_NAME
                """,
                {"owner": owner},
            )
            rows = self._fetchall_logged(cursor, source, f"property_graphs:{owner}")
            return {
                "source_id": source.source_id,
                "source_name": source.source_name,
                "schema": owner,
                "graphs": [{"owner": row[0], "graph_name": row[1]} for row in rows],
            }

        return self._run_with_remote_retry(source, f"get_remote_property_graphs:{source_id}", action)

    def get_remote_property_graph_topology(
        self,
        source_id: str,
        graph_name: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> Dict[str, Any]:
        """读取 Oracle SQL Property Graph 的真实顶点、边和底层对象字段。

        该结果完全来自 Oracle 的 ALL_PG_* 数据字典视图，不依赖平台本体、
        数据映射或 DDL 记录。
        """
        source = self._get_data_source(source_id)
        if (source.db_type or "").lower() != "oracle":
            raise ValueError("属性图对象仅支持 Oracle 数据源")

        requested_graph = str(graph_name or "").strip().upper()

        def action(_connection, cursor):
            self._execute_remote_sql(cursor, source, "SELECT USER FROM DUAL")
            connected_user = self._fetchone_logged(cursor, source, "property_graph_topology_connected_user")[0]
            owner = (schema or source.schema_name or connected_user or source.username).upper()
            self._execute_remote_sql(
                cursor,
                source,
                """
                SELECT GRAPH_NAME
                FROM ALL_PROPERTY_GRAPHS
                WHERE OWNER = :owner
                ORDER BY GRAPH_NAME
                """,
                {"owner": owner},
            )
            graph_rows = self._fetchall_logged(cursor, source, f"property_graph_topology_graphs:{owner}")
            graph_names = [row[0] for row in graph_rows]
            if not graph_names:
                return {
                    "source_id": source.source_id,
                    "source_name": source.source_name,
                    "schema": owner,
                    "graphs": [],
                    "graph_name": None,
                    "nodes": [],
                    "edges": [],
                }
            if requested_graph and requested_graph not in graph_names:
                raise ValueError(f"Property Graph 不存在或当前用户无权读取: {requested_graph}")
            selected_graph = requested_graph or graph_names[0]
            binds = {"owner": owner, "graph_name": selected_graph}

            self._execute_remote_sql(
                cursor,
                source,
                """
                SELECT e.ELEMENT_NAME, e.ELEMENT_KIND, e.OBJECT_OWNER, e.OBJECT_NAME,
                       o.OBJECT_TYPE, tc.COMMENTS
                FROM ALL_PG_ELEMENTS e
                LEFT JOIN ALL_OBJECTS o
                  ON o.OWNER = e.OBJECT_OWNER
                 AND o.OBJECT_NAME = e.OBJECT_NAME
                 AND o.OBJECT_TYPE IN ('TABLE', 'VIEW')
                LEFT JOIN ALL_TAB_COMMENTS tc
                  ON tc.OWNER = e.OBJECT_OWNER AND tc.TABLE_NAME = e.OBJECT_NAME
                WHERE e.OWNER = :owner AND e.GRAPH_NAME = :graph_name
                ORDER BY e.ELEMENT_KIND, e.ELEMENT_NAME
                """,
                binds,
            )
            element_rows = self._fetchall_logged(cursor, source, f"property_graph_elements:{owner}:{selected_graph}")

            self._execute_remote_sql(
                cursor,
                source,
                """
                SELECT ELEMENT_NAME, LABEL_NAME
                FROM ALL_PG_ELEMENT_LABELS
                WHERE OWNER = :owner AND GRAPH_NAME = :graph_name
                ORDER BY ELEMENT_NAME, LABEL_NAME
                """,
                binds,
            )
            label_rows = self._fetchall_logged(cursor, source, f"property_graph_labels:{owner}:{selected_graph}")

            self._execute_remote_sql(
                cursor,
                source,
                """
                SELECT ELEMENT_NAME, COLUMN_NAME
                FROM ALL_PG_KEYS
                WHERE OWNER = :owner AND GRAPH_NAME = :graph_name
                ORDER BY ELEMENT_NAME, COLUMN_NAME
                """,
                binds,
            )
            key_rows = self._fetchall_logged(cursor, source, f"property_graph_keys:{owner}:{selected_graph}")

            self._execute_remote_sql(
                cursor,
                source,
                """
                SELECT e.ELEMENT_NAME, c.COLUMN_NAME,
                       c.DATA_TYPE || CASE
                           WHEN c.DATA_TYPE IN ('VARCHAR2','VARCHAR','CHAR','NCHAR','NVARCHAR2','RAW') THEN '(' || c.DATA_LENGTH || ')'
                           WHEN c.DATA_TYPE = 'NUMBER' AND c.DATA_PRECISION IS NOT NULL AND c.DATA_SCALE IS NOT NULL THEN '(' || c.DATA_PRECISION || ',' || c.DATA_SCALE || ')'
                           WHEN c.DATA_TYPE = 'NUMBER' AND c.DATA_PRECISION IS NOT NULL THEN '(' || c.DATA_PRECISION || ')'
                           ELSE ''
                       END AS DATA_TYPE,
                       c.NULLABLE, c.COLUMN_ID, cc.COMMENTS
                FROM ALL_PG_ELEMENTS e
                JOIN ALL_TAB_COLUMNS c
                  ON c.OWNER = e.OBJECT_OWNER AND c.TABLE_NAME = e.OBJECT_NAME
                LEFT JOIN ALL_COL_COMMENTS cc
                  ON cc.OWNER = c.OWNER AND cc.TABLE_NAME = c.TABLE_NAME AND cc.COLUMN_NAME = c.COLUMN_NAME
                WHERE e.OWNER = :owner AND e.GRAPH_NAME = :graph_name
                  AND e.ELEMENT_KIND = 'VERTEX'
                ORDER BY e.ELEMENT_NAME, c.COLUMN_ID
                """,
                binds,
            )
            column_rows = self._fetchall_logged(cursor, source, f"property_graph_columns:{owner}:{selected_graph}")

            self._execute_remote_sql(
                cursor,
                source,
                """
                SELECT EDGE_TAB_NAME, VERTEX_TAB_NAME, EDGE_END, EDGE_COL_NAME, VERTEX_COL_NAME
                FROM ALL_PG_EDGE_RELATIONSHIPS
                WHERE OWNER = :owner AND GRAPH_NAME = :graph_name
                ORDER BY EDGE_TAB_NAME, EDGE_END, EDGE_COL_NAME
                """,
                binds,
            )
            edge_rows = self._fetchall_logged(cursor, source, f"property_graph_edges:{owner}:{selected_graph}")

            return self._assemble_property_graph_topology(
                source=source,
                owner=owner,
                graph_names=graph_names,
                graph_name=selected_graph,
                element_rows=element_rows,
                label_rows=label_rows,
                key_rows=key_rows,
                column_rows=column_rows,
                edge_rows=edge_rows,
            )

        return self._run_with_remote_retry(source, f"get_remote_property_graph_topology:{source_id}", action)

    @staticmethod
    def _assemble_property_graph_topology(
        source: SysDataSource,
        owner: str,
        graph_names: List[str],
        graph_name: str,
        element_rows: List[Any],
        label_rows: List[Any],
        key_rows: List[Any],
        column_rows: List[Any],
        edge_rows: List[Any],
    ) -> Dict[str, Any]:
        labels_by_element: Dict[str, List[str]] = {}
        for element_name, label_name in label_rows:
            labels_by_element.setdefault(element_name, []).append(label_name)
        keys_by_element: Dict[str, set] = {}
        for element_name, column_name in key_rows:
            keys_by_element.setdefault(element_name, set()).add(column_name)
        columns_by_element: Dict[str, List[Dict[str, Any]]] = {}
        for element_name, column_name, data_type, nullable, order_num, comments in column_rows:
            columns_by_element.setdefault(element_name, []).append({
                "property_name": column_name,
                "property_display_name": comments,
                "data_type": data_type,
                "is_nullable": "Y" if nullable == "Y" else "N",
                "order_num": order_num,
                "is_primary_key": "Y" if column_name in keys_by_element.get(element_name, set()) else "N",
                "source_mark": "PROPERTY_GRAPH",
            })

        vertex_ids: Dict[str, str] = {}
        nodes: List[Dict[str, Any]] = []
        edge_labels: Dict[str, str] = {}
        for element_name, element_kind, object_owner, object_name, object_type, comments in element_rows:
            label = (labels_by_element.get(element_name) or [element_name])[0]
            if element_kind == "VERTEX":
                node_id = f"{graph_name}:VERTEX:{element_name}"
                vertex_ids[element_name] = node_id
                nodes.append({
                    "id": node_id,
                    "name": element_name,
                    "displayName": label,
                    "desc": comments,
                    "buildType": object_type or "TABLE",
                    "tableName": f"{object_owner}.{object_name}",
                    "status": "DEPLOYED",
                    "icon": None,
                    "color": None,
                    "properties": columns_by_element.get(element_name, []),
                    "mappingInfo": {"graph_name": graph_name, "element_name": element_name},
                })
            elif element_kind == "EDGE":
                edge_labels[element_name] = label

        edge_ends: Dict[str, Dict[str, Dict[str, List[Dict[str, str]]]]] = {}
        for edge_tab, vertex_tab, edge_end, edge_col, vertex_col in edge_rows:
            edge_ends.setdefault(edge_tab, {}).setdefault(edge_end, {}).setdefault(vertex_tab, []).append({
                "vertex": vertex_tab,
                "edge_column": edge_col,
                "vertex_column": vertex_col,
            })
        edges: List[Dict[str, Any]] = []
        for edge_tab, ends in edge_ends.items():
            for source_vertex, source_columns in ends.get("SOURCE", {}).items():
                for destination_vertex, destination_columns in ends.get("DESTINATION", {}).items():
                    source_id = vertex_ids.get(source_vertex)
                    target_id = vertex_ids.get(destination_vertex)
                    if not source_id or not target_id:
                        continue
                    source_desc = ", ".join(f"{item['edge_column']} → {source_vertex}.{item['vertex_column']}" for item in source_columns)
                    destination_desc = ", ".join(f"{item['edge_column']} → {destination_vertex}.{item['vertex_column']}" for item in destination_columns)
                    edges.append({
                        "id": f"{graph_name}:EDGE:{edge_tab}:{source_vertex}:{destination_vertex}",
                        "source": source_id,
                        "target": target_id,
                        "name": edge_labels.get(edge_tab, edge_tab),
                        "type": "PROPERTY_GRAPH_EDGE",
                        "desc": f"{edge_tab}: {source_desc}; {destination_desc}",
                        "relationTableName": edge_tab,
                        "relationObjectType": "EDGE",
                        "mappingData": {"graph_name": graph_name, "edge_element": edge_tab},
                    })
        return {
            "source_id": source.source_id,
            "source_name": source.source_name,
            "schema": owner,
            "graphs": [{"owner": owner, "graph_name": name} for name in graph_names],
            "graph_name": graph_name,
            "nodes": nodes,
            "edges": edges,
        }

    def get_remote_tables_metadata_for_mapping(
        self,
        source_id: Optional[str],
        domain_id: Optional[str],
        schema: Optional[str],
        entity_keywords: Optional[List[str]] = None,
        sample_limit: int = 3,
    ) -> Dict[str, Any]:
        """为 LLM 自动映射读取远程数据源表元数据"""
        logger.info(
            "Load metadata for mapping: source_id=%s domain_id=%s schema=%s sample_limit=%s keywords=%s",
            source_id,
            domain_id,
            schema,
            sample_limit,
            entity_keywords,
        )
        source = self._get_data_source(source_id) if source_id else self._get_default_data_source(domain_id)
        if not source:
            raise ValueError("未配置可用于自动映射的启用数据源")

        table_result = self.get_remote_table_catalog_for_mapping(
            source_id=source.source_id,
            domain_id=domain_id,
            schema=schema,
            entity_keywords=entity_keywords,
        )
        tables = table_result.get("tables", []) or []
        if not tables:
            raise ValueError("当前数据源下没有可用于映射的表")

        return self.get_remote_tables_metadata_by_names(
            source_id=source.source_id,
            schema=table_result.get("schema") or schema or source.schema_name,
            tables=tables,
            sample_limit=sample_limit,
            entity_keywords=entity_keywords,
            source_name=source.source_name,
        )

    def get_remote_table_catalog_for_mapping(
        self,
        source_id: Optional[str],
        domain_id: Optional[str],
        schema: Optional[str],
        entity_keywords: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        source = self._get_data_source(source_id) if source_id else self._get_default_data_source(domain_id)
        if not source:
            raise ValueError("未配置可用于自动映射的启用数据源")

        table_result = self.get_remote_tables(
            source_id=source.source_id,
            schema=schema,
        )
        raw_tables = table_result.get("tables", []) or []
        tables = [
            table for table in raw_tables
            if self._is_mapping_candidate_table(table.get("table_name"))
        ]
        logger.info(
            "Filter mapping candidate tables: source_id=%s raw_count=%s filtered_count=%s excluded_count=%s",
            source.source_id,
            len(raw_tables),
            len(tables),
            len(raw_tables) - len(tables),
        )
        ranked_tables = self._rank_tables_for_mapping(tables, entity_keywords or [])
        logger.info(
            "Loaded mapping table catalog: source_id=%s ranked_catalog_count=%s preview_tables=%s",
            source.source_id,
            len(ranked_tables),
            [item.get("table_name") for item in ranked_tables[:30]],
        )
        return {
            "source_id": source.source_id,
            "source_name": source.source_name,
            "schema": table_result.get("schema") or schema or source.schema_name,
            "tables": ranked_tables,
        }

    def get_remote_tables_metadata_by_names(
        self,
        source_id: str,
        schema: Optional[str],
        tables: List[Dict[str, Any]],
        sample_limit: int = 3,
        entity_keywords: Optional[List[str]] = None,
        source_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        metadata_tables = []
        for table in tables:
            try:
                detail = self.get_remote_table_detail(
                    source_id=source_id,
                    table_name=table["table_name"],
                    schema=table.get("owner") or schema,
                    sample_limit=max(1, min(sample_limit, 5)),
                )
            except Exception:
                continue

            supported_columns = [column for column in detail.get("columns", []) if column.get("mapping_supported", True)]
            supported_column_names = {column["column_name"] for column in supported_columns}
            supported_sample_rows = [
                {
                    key: value
                    for key, value in row.items()
                    if key in supported_column_names
                }
                for row in detail.get("sample_rows", [])[: max(1, min(sample_limit, 5))]
            ]
            if not supported_columns:
                continue

            metadata_tables.append({
                "owner": detail.get("owner"),
                "table_name": detail.get("table_name"),
                "comments": detail.get("table_comment") or table.get("comments"),
                "num_rows": table.get("num_rows"),
                "columns": supported_columns,
                "sample_rows": supported_sample_rows,
            })

        if not metadata_tables:
            raise ValueError("未能读取数据源表结构，无法执行自动映射")

        metadata_tables = self._rank_tables_for_mapping(metadata_tables, entity_keywords or [])
        result = {
            "source_id": source_id,
            "source_name": source_name or self._get_data_source(source_id).source_name,
            "schema": schema,
            "tables": metadata_tables,
        }
        logger.info(
            "Loaded metadata by selected table names: source_id=%s selected_tables=%s",
            source_id,
            len(result["tables"]),
        )
        logger.info(
            "Selected mapping metadata table preview: %s",
            [
                {
                    "table_name": item.get("table_name"),
                    "column_count": len(item.get("columns", []) or []),
                    "sample_row_count": len(item.get("sample_rows", []) or []),
                }
                for item in result["tables"][:5]
            ],
        )
        return result

    def preview_remote_select_sql(
        self,
        source_id: str,
        sql: str,
        schema: Optional[str] = None,
        sample_limit: int = 5,
    ) -> Dict[str, Any]:
        source = self._get_data_source(source_id)
        normalized_sql = (sql or "").strip().rstrip(";")
        if not normalized_sql:
            raise ValueError("SQL 不能为空")
        upper_sql = normalized_sql.upper()
        if not (upper_sql.startswith("SELECT") or upper_sql.startswith("WITH")):
            raise ValueError("只允许预览 SELECT / WITH 查询")
        blocked_tokens = [
            " INSERT ",
            " UPDATE ",
            " DELETE ",
            " MERGE ",
            " DROP ",
            " ALTER ",
            " TRUNCATE ",
            " GRANT ",
            " REVOKE ",
            " COMMENT ",
            " EXECUTE ",
            " BEGIN ",
            " DECLARE ",
        ]
        padded_sql = f" {upper_sql} "
        if any(token in padded_sql for token in blocked_tokens):
            raise ValueError("edge_sql 预览只允许只读查询")

        def action(_connection, cursor):
            self._execute_remote_sql(cursor, source, "SELECT USER FROM DUAL")
            connected_user = self._fetchone_logged(cursor, source, "connected_user")[0]
            owner = (schema or source.schema_name or connected_user or source.username).upper()
            if owner:
                self._execute_remote_sql(cursor, source, f'ALTER SESSION SET CURRENT_SCHEMA = "{owner}"')

            preview_sql = f"""
                SELECT *
                FROM (
                    {normalized_sql}
                )
                FETCH FIRST {max(1, min(sample_limit, 20))} ROWS ONLY
            """
            self._execute_remote_sql(cursor, source, preview_sql)
            preview_columns = [desc[0] for desc in cursor.description] if cursor.description else []
            preview_rows = [
                {
                    preview_columns[idx]: self._normalize_cell_value(value)
                    for idx, value in enumerate(row)
                }
                for row in self._fetchall_logged(cursor, source, "edge_sql_preview")
            ]
            required_columns = ["EDGE_ID", "SOURCE_ID", "TARGET_ID"]
            missing_columns = [column for column in required_columns if column not in [name.upper() for name in preview_columns]]
            return {
                "schema": owner,
                "columns": preview_columns,
                "rows": preview_rows,
                "valid": len(missing_columns) == 0,
                "missing_columns": missing_columns,
                "required_columns": required_columns,
            }

        return self._run_with_remote_retry(source, f"preview_remote_select_sql:{source_id}", action)

    def profile_remote_join(
        self,
        source_id: str,
        source_table: str,
        source_column: str,
        target_table: str,
        target_column: str,
        schema: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return real-data evidence for one simple source-to-target join."""
        identifiers = [source_table, source_column, target_table, target_column]
        if not all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]{0,127}", (item or "").strip()) for item in identifiers):
            raise ValueError("关联表名和字段名只能使用 Oracle 标识符")
        source = self._get_data_source(source_id)
        src_table, src_column, dst_table, dst_column = [item.strip().upper() for item in identifiers]

        def action(_connection, cursor):
            self._execute_remote_sql(cursor, source, "SELECT USER FROM DUAL")
            connected_user = self._fetchone_logged(cursor, source, "join_profile_connected_user")[0]
            owner = (schema or source.schema_name or connected_user or source.username).upper()
            if owner:
                self._execute_remote_sql(cursor, source, f'ALTER SESSION SET CURRENT_SCHEMA = "{owner}"')
            profile_sql = f"""
                SELECT
                    (SELECT COUNT(*) FROM {src_table} src WHERE src.{src_column} IS NOT NULL) AS SOURCE_NON_NULL_COUNT,
                    (SELECT COUNT(*) FROM {dst_table} dst WHERE dst.{dst_column} IS NOT NULL) AS TARGET_NON_NULL_COUNT,
                    (SELECT COUNT(*) FROM {src_table} src JOIN {dst_table} dst ON src.{src_column} = dst.{dst_column}) AS MATCHED_COUNT,
                    (SELECT COUNT(*) FROM {src_table} src WHERE src.{src_column} IS NOT NULL AND EXISTS (SELECT 1 FROM {dst_table} dst WHERE dst.{dst_column} = src.{src_column})) AS MATCHED_SOURCE_RECORD_COUNT,
                    (SELECT COUNT(*) FROM {dst_table} dst WHERE dst.{dst_column} IS NOT NULL AND EXISTS (SELECT 1 FROM {src_table} src WHERE src.{src_column} = dst.{dst_column})) AS MATCHED_TARGET_RECORD_COUNT,
                    (SELECT COUNT(DISTINCT src.{src_column}) FROM {src_table} src JOIN {dst_table} dst ON src.{src_column} = dst.{dst_column}) AS MATCHED_SOURCE_KEY_COUNT,
                    (SELECT COUNT(DISTINCT dst.{dst_column}) FROM {src_table} src JOIN {dst_table} dst ON src.{src_column} = dst.{dst_column}) AS MATCHED_TARGET_KEY_COUNT
                FROM dual
            """
            self._execute_remote_sql(cursor, source, profile_sql)
            row = self._fetchone_logged(cursor, source, "join_profile") or (0, 0, 0, 0, 0, 0, 0)
            source_count, target_count, matched, matched_source_records, matched_target_records, matched_source_keys, matched_target_keys = [int(value or 0) for value in row]
            return {
                "source_non_null_count": source_count,
                "target_non_null_count": target_count,
                "matched_count": matched,
                "matched_source_record_count": matched_source_records,
                "matched_target_record_count": matched_target_records,
                "matched_source_key_count": matched_source_keys,
                "matched_target_key_count": matched_target_keys,
                "source_coverage": round(matched_source_records / source_count, 4) if source_count else 0,
                "target_coverage": round(matched_target_records / target_count, 4) if target_count else 0,
                "valid": matched > 0,
                "schema": owner,
            }

        return self._run_with_remote_retry(source, f"profile_remote_join:{source_id}:{src_table}:{dst_table}", action)

    def execute_remote_graph_query(
        self,
        source_id: str,
        graph_sql: str,
        schema: Optional[str] = None,
        row_limit: int = 200,
    ) -> Dict[str, Any]:
        """执行只读 Oracle GRAPH_TABLE 查询，并返回可用于图形展示的行数据。"""
        source = self._get_data_source(source_id)
        normalized_sql = (graph_sql or "").strip().rstrip(";")
        upper_sql = normalized_sql.upper()
        if not normalized_sql:
            raise ValueError("Graph SQL 不能为空")
        if not (upper_sql.startswith("SELECT") or upper_sql.startswith("WITH")):
            raise ValueError("只允许执行 SELECT / WITH Graph SQL")
        if "GRAPH_TABLE" not in upper_sql:
            raise ValueError("仅允许执行包含 GRAPH_TABLE 的 Oracle Graph SQL")
        blocked_tokens = [" INSERT ", " UPDATE ", " DELETE ", " MERGE ", " DROP ", " ALTER ", " TRUNCATE ", " GRANT ", " REVOKE ", " COMMENT ", " EXECUTE ", " BEGIN ", " DECLARE "]
        if any(token in f" {upper_sql} " for token in blocked_tokens):
            raise ValueError("Graph SQL 仅允许只读查询")

        def action(_connection, cursor):
            self._execute_remote_sql(cursor, source, "SELECT USER FROM DUAL")
            connected_user = self._fetchone_logged(cursor, source, "graph_query_connected_user")[0]
            owner = (schema or source.schema_name or connected_user or source.username).upper()
            if owner:
                self._execute_remote_sql(cursor, source, f'ALTER SESSION SET CURRENT_SCHEMA = "{owner}"')
            query_sql = f"SELECT * FROM (\n{normalized_sql}\n) FETCH FIRST {max(1, min(row_limit, 1000))} ROWS ONLY"
            self._execute_remote_sql(cursor, source, query_sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = [
                {columns[index]: self._normalize_cell_value(value) for index, value in enumerate(row)}
                for row in self._fetchall_logged(cursor, source, "graph_query")
            ]
            upper_columns = {column.upper() for column in columns}
            return {
                "source_id": source_id,
                "source_name": source.source_name,
                "schema": owner,
                "columns": columns,
                "rows": rows,
                "graph_ready": {"SOURCE_ID", "TARGET_ID"}.issubset(upper_columns),
                "graph_hint": "查询结果包含 SOURCE_ID、TARGET_ID 时可自动渲染为关系图。",
            }

        return self._run_with_remote_retry(source, f"execute_remote_graph_query:{source_id}", action)

    def get_remote_property_graph_instances(
        self,
        source_id: str,
        graph_name: str,
        node_id: str,
        property_name: Optional[str] = None,
        operator: str = "contains",
        value: Optional[str] = None,
        row_limit: int = 50,
    ) -> Dict[str, Any]:
        """Query deployed graph backing tables and return an instance subgraph.

        Object and column identifiers are selected exclusively from Property Graph
        metadata; only condition values are supplied by the caller as binds.
        """
        source = self._get_data_source(source_id)
        topology = self.get_remote_property_graph_topology(source_id, graph_name, source.schema_name)
        node = next((item for item in (topology.get("nodes") or []) if item.get("id") == node_id), None)
        if not node:
            raise ValueError("所选本体节点不属于当前 Property Graph")
        table_name = str(node.get("tableName") or "")
        if "." not in table_name:
            raise ValueError("无法识别本体节点的底层对象表")
        owner, raw_table_name = table_name.rsplit(".", 1)
        columns = node.get("properties") or []
        column_names = {str(item.get("property_name") or "").upper() for item in columns}
        key_column = next(
            (str(item.get("property_name") or "").upper() for item in columns if item.get("is_primary_key") == "Y"),
            "",
        )
        if not key_column:
            raise ValueError("所选本体节点未配置主键，无法展示实例关系")
        selected_column = str(property_name or "").upper()
        if selected_column and selected_column not in column_names:
            raise ValueError("查询条件字段不属于所选本体节点")
        normalized_operator = str(operator or "contains").lower()
        if normalized_operator not in {"equals", "contains", "greater_than", "less_than"}:
            raise ValueError("不支持的查询条件操作符")
        limit = max(1, min(int(row_limit or 50), 100))
        safe_owner = self._quote_identifier(owner)
        safe_table = self._quote_identifier(raw_table_name)
        where_sql = ""
        binds: Dict[str, Any] = {}
        if selected_column and str(value or "").strip():
            safe_column = self._quote_identifier(selected_column)
            if normalized_operator == "equals":
                where_sql = f" WHERE {safe_column} = :condition_value"
                binds["condition_value"] = value
            elif normalized_operator == "contains":
                where_sql = f" WHERE UPPER(TO_CHAR({safe_column})) LIKE UPPER(:condition_value)"
                binds["condition_value"] = f"%{value}%"
            elif normalized_operator == "greater_than":
                where_sql = f" WHERE {safe_column} > :condition_value"
                binds["condition_value"] = value
            else:
                where_sql = f" WHERE {safe_column} < :condition_value"
                binds["condition_value"] = value

        def action(_connection, cursor):
            self._execute_remote_sql(cursor, source, f"SELECT * FROM {safe_owner}.{safe_table}{where_sql} FETCH FIRST {limit} ROWS ONLY", binds)
            result_columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = [
                {result_columns[index]: self._normalize_cell_value(cell) for index, cell in enumerate(row)}
                for row in self._fetchall_logged(cursor, source, "property_graph_instances")
            ]
            key_values = [row.get(key_column) for row in rows if row.get(key_column) is not None]
            instance_nodes = [
                {
                    "id": f"{node_id}:{key_value}", "node_id": node_id,
                    "label": node.get("displayName") or node.get("name"),
                    "instance_label": self._instance_label(row, key_column), "properties": row,
                    "selected": True,
                }
                for row, key_value in ((row, row.get(key_column)) for row in rows)
                if key_value is not None
            ]
            instance_edges: List[Dict[str, Any]] = []
            instance_index = {item["id"] for item in instance_nodes}
            node_by_id = {item.get("id"): item for item in (topology.get("nodes") or [])}
            if key_values:
                bind_names = []
                edge_binds: Dict[str, Any] = {}
                for index, key_value in enumerate(key_values):
                    bind_name = f"key_{index}"
                    bind_names.append(f":{bind_name}")
                    edge_binds[bind_name] = key_value
                for edge in topology.get("edges") or []:
                    is_source = edge.get("source") == node_id
                    is_target = edge.get("target") == node_id
                    if not (is_source or is_target):
                        continue
                    edge_table = str(edge.get("relationTableName") or "")
                    if not edge_table:
                        continue
                    safe_edge_table = self._quote_identifier(edge_table.rsplit(".", 1)[-1])
                    key_side = "SOURCE_ID" if is_source else "TARGET_ID"
                    self._execute_remote_sql(
                        cursor, source,
                        f"SELECT SOURCE_ID, TARGET_ID FROM {safe_owner}.{safe_edge_table} WHERE {key_side} IN ({', '.join(bind_names)}) FETCH FIRST {limit * 3} ROWS ONLY",
                        edge_binds,
                    )
                    for source_id, target_id in self._fetchall_logged(cursor, source, "property_graph_instance_edges"):
                        local_value, remote_value = (source_id, target_id) if is_source else (target_id, source_id)
                        local_id = f"{node_id}:{local_value}"
                        remote_node_id = edge.get("target") if is_source else edge.get("source")
                        remote_id = f"{remote_node_id}:{remote_value}"
                        if local_id not in instance_index:
                            continue
                        if remote_id not in instance_index:
                            remote_node = node_by_id.get(remote_node_id) or {}
                            instance_nodes.append({
                                "id": remote_id, "node_id": remote_node_id,
                                "label": remote_node.get("displayName") or remote_node.get("name") or "关联节点",
                                "instance_label": str(remote_value), "properties": {}, "selected": False,
                            })
                            instance_index.add(remote_id)
                        instance_edges.append({
                            "id": f"{edge.get('id')}:{source_id}:{target_id}", "source": f"{edge.get('source')}:{source_id}",
                            "target": f"{edge.get('target')}:{target_id}", "edge_id": edge.get("id"), "label": edge.get("name") or "关联",
                        })
            return {"graph_name": topology.get("graph_name"), "node": node, "rows": rows, "nodes": instance_nodes, "edges": instance_edges}

        return self._run_with_remote_retry(source, f"get_property_graph_instances:{source_id}:{graph_name}", action)

    def get_remote_property_graph_instance_lineage(
        self, source_id: str, graph_name: str, node_id: str, instance_key: str, max_depth: int = 12,
    ) -> Dict[str, Any]:
        """Expand one instance across all reachable upstream/downstream graph entities."""
        source = self._get_data_source(source_id)
        topology = self.get_remote_property_graph_topology(source_id, graph_name, source.schema_name)
        nodes_by_id = {item.get("id"): item for item in topology.get("nodes") or []}
        if node_id not in nodes_by_id:
            raise ValueError("所选实例不属于当前 Property Graph")
        depth_limit = max(1, min(int(max_depth or 12), 20))

        def key_column(node: Dict[str, Any]) -> str:
            return next((str(item.get("property_name") or "").upper() for item in node.get("properties") or [] if item.get("is_primary_key") == "Y"), "")

        def action(_connection, cursor):
            pending = [(node_id, instance_key, 0)]
            visited = set()
            result_nodes: List[Dict[str, Any]] = []
            result_edges: List[Dict[str, Any]] = []
            node_ids = set()
            edge_ids = set()
            while pending and len(visited) < 400:
                current_node_id, current_key, depth = pending.pop(0)
                visit_key = (current_node_id, str(current_key))
                if visit_key in visited:
                    continue
                visited.add(visit_key)
                current_node = nodes_by_id.get(current_node_id) or {}
                current_table = str(current_node.get("tableName") or "")
                current_pk = key_column(current_node)
                if not current_pk or "." not in current_table:
                    continue
                owner, table = current_table.rsplit(".", 1)
                self._execute_remote_sql(
                    cursor, source,
                    f"SELECT * FROM {self._quote_identifier(owner)}.{self._quote_identifier(table)} WHERE {self._quote_identifier(current_pk)} = :instance_key FETCH FIRST 1 ROWS ONLY",
                    {"instance_key": current_key},
                )
                column_names = [desc[0] for desc in cursor.description] if cursor.description else []
                row = self._fetchone_logged(cursor, source, "property_graph_lineage_node")
                properties = {column_names[index]: self._normalize_cell_value(value) for index, value in enumerate(row)} if row else {}
                visual_id = f"{current_node_id}:{current_key}"
                if visual_id not in node_ids:
                    result_nodes.append({"id": visual_id, "node_id": current_node_id, "label": current_node.get("displayName") or current_node.get("name"), "instance_label": self._instance_label(properties, current_pk), "properties": properties, "selected": current_node_id == node_id and str(current_key) == str(instance_key)})
                    node_ids.add(visual_id)
                if depth >= depth_limit:
                    continue
                for edge in topology.get("edges") or []:
                    is_source = edge.get("source") == current_node_id
                    is_target = edge.get("target") == current_node_id
                    if not (is_source or is_target):
                        continue
                    edge_table = str(edge.get("relationTableName") or "")
                    if not edge_table:
                        continue
                    side = "SOURCE_ID" if is_source else "TARGET_ID"
                    self._execute_remote_sql(cursor, source, f"SELECT SOURCE_ID, TARGET_ID FROM {self._quote_identifier(owner)}.{self._quote_identifier(edge_table.rsplit('.', 1)[-1])} WHERE {side} = :instance_key FETCH FIRST 100 ROWS ONLY", {"instance_key": current_key})
                    for source_key, target_key in self._fetchall_logged(cursor, source, "property_graph_lineage_edges"):
                        next_node_id, next_key = (edge.get("target"), target_key) if is_source else (edge.get("source"), source_key)
                        relation_id = f"{edge.get('id')}:{source_key}:{target_key}"
                        if relation_id not in edge_ids:
                            result_edges.append({"id": relation_id, "edge_id": edge.get("id"), "source": f"{edge.get('source')}:{source_key}", "target": f"{edge.get('target')}:{target_key}", "label": edge.get("name") or "关联"})
                            edge_ids.add(relation_id)
                        if (next_node_id, str(next_key)) not in visited:
                            pending.append((next_node_id, next_key, depth + 1))
            return {"graph_name": topology.get("graph_name"), "nodes": result_nodes, "edges": result_edges, "max_depth": depth_limit}
        return self._run_with_remote_retry(source, f"get_property_graph_lineage:{source_id}:{graph_name}", action)

    @staticmethod
    def _instance_label(row: Dict[str, Any], key_column: str) -> str:
        preferred = ["NAME", "CODE", "NO", "NUMBER", "ID"]
        for column_name, cell in row.items():
            if column_name.upper() != key_column and any(token in column_name.upper() for token in preferred) and cell is not None:
                return str(cell)
        return str(row.get(key_column) or "实例")

    async def generate_remote_table_comment_suggestions(
        self,
        source_id: str,
        table_name: str,
        schema: Optional[str] = None,
        sample_limit: int = 5,
        primary_model_config_id: Optional[str] = None,
        verifier_model_config_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """调用大模型为表和字段补全缺失描述"""
        table_detail = self.get_remote_table_detail(
            source_id=source_id,
            table_name=table_name,
            schema=schema,
            sample_limit=sample_limit,
        )

        from app.services.llm_service import LLMService

        llm_service = LLMService(self.db)
        llm_result = await llm_service.generate_data_object_comments(
            table_detail,
            primary_config_id=primary_model_config_id,
            verifier_config_id=verifier_model_config_id,
        )
        column_suggestions = {
            item["column_name"]: item.get("comment", "")
            for item in llm_result.get("columns", [])
            if item.get("column_name")
        }

        enriched_columns = []
        for column in table_detail.get("columns", []):
            current_comment = column.get("comments") or ""
            suggested_comment = column_suggestions.get(column["column_name"], "")
            enriched_columns.append({
                **column,
                "current_comment": current_comment,
                "suggested_comment": suggested_comment,
                "final_comment": current_comment or suggested_comment,
            })

        current_table_comment = table_detail.get("table_comment") or ""
        suggested_table_comment = llm_result.get("table_comment", "") if not current_table_comment else ""

        return {
            **table_detail,
            "current_table_comment": current_table_comment,
            "suggested_table_comment": suggested_table_comment,
            "final_table_comment": current_table_comment or suggested_table_comment,
            "columns": enriched_columns,
            "generation_mode": llm_result.get("generation_mode", "llm"),
            "verification_mode": llm_result.get("verification_mode", ""),
            "primary_model": llm_result.get("primary_model"),
            "verifier_model": llm_result.get("verifier_model"),
        }

    def save_remote_table_comments(
        self,
        source_id: str,
        table_name: str,
        schema: Optional[str],
        table_comment: Optional[str],
        column_comments: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """将表和字段 comments 保存到外部 Oracle 数据源"""
        table_detail = self.get_remote_table_detail(
            source_id=source_id,
            table_name=table_name,
            schema=schema,
            sample_limit=5,
        )

        source = self._get_data_source(source_id)
        connection = self._connect_to_oracle(source)
        cursor = connection.cursor()

        actual_owner = table_detail["owner"]
        actual_table_name = table_detail["table_name"]
        valid_columns = {column["column_name"] for column in table_detail.get("columns", [])}

        try:
            if table_comment is not None:
                cursor.execute(
                    self._build_comment_sql(
                        object_type="TABLE",
                        owner=actual_owner,
                        table_name=actual_table_name,
                        comment=table_comment,
                    )
                )

            for item in column_comments:
                column_name = item.get("column_name")
                if not column_name or column_name not in valid_columns:
                    continue
                cursor.execute(
                    self._build_comment_sql(
                        object_type="COLUMN",
                        owner=actual_owner,
                        table_name=actual_table_name,
                        column_name=column_name,
                        comment=item.get("comments", ""),
                    )
                )

            connection.commit()
            self._log_operation(
                "SAVE_REMOTE_COMMENTS",
                f"{actual_owner}.{actual_table_name}",
                "保存数据对象表/列 comments 到外部数据库",
            )
            return self.get_remote_table_detail(
                source_id=source_id,
                table_name=actual_table_name,
                schema=actual_owner,
                sample_limit=5,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _get_data_source(self, source_id: str) -> SysDataSource:
        source = self.db.query(SysDataSource).filter(SysDataSource.source_id == source_id).first()
        if not source:
            raise ValueError("数据源不存在")
        return source

    def _get_default_data_source(self, domain_id: Optional[str] = None) -> Optional[SysDataSource]:
        query = self.db.query(SysDataSource).filter(SysDataSource.is_active == "Y")
        if domain_id:
            query = query.filter(SysDataSource.business_domain_id == domain_id)
        return query.order_by(SysDataSource.is_default.desc(), SysDataSource.created_at.desc()).first()

    def _score_table_for_mapping(
        self,
        table: Dict[str, Any],
        entity_keywords: List[str],
    ) -> tuple[int, int]:
        keywords = [keyword.upper() for keyword in entity_keywords if keyword]
        if not keywords:
            return (0, int(table.get("num_rows") or 0))

        table_name = (table.get("table_name") or "").upper()
        comments = (table.get("comments") or "").upper()
        text_blob = f"{table_name} {comments}"
        hit_score = sum(1 for keyword in keywords if keyword in text_blob)
        prefix_score = sum(2 for keyword in keywords if table_name.startswith(keyword))

        column_score = 0
        for column in table.get("columns", []) or []:
            column_name = (column.get("column_name") or "").upper()
            column_comments = (column.get("comments") or "").upper()
            column_text = f"{column_name} {column_comments}"
            for keyword in keywords:
                if keyword and keyword in column_text:
                    column_score += 2

        return (prefix_score + hit_score + column_score, int(table.get("num_rows") or 0))

    def _rank_tables_for_mapping(
        self,
        tables: List[Dict[str, Any]],
        entity_keywords: List[str],
    ) -> List[Dict[str, Any]]:
        if not entity_keywords:
            return tables
        return sorted(tables, key=lambda table: self._score_table_for_mapping(table, entity_keywords), reverse=True)

    def build_entity_keywords(
        self,
        entity_name: str,
        entity_display_name: Optional[str],
        entity_desc: Optional[str],
        property_keywords: Optional[List[str]] = None,
    ) -> List[str]:
        raw_parts = [entity_name or "", entity_display_name or "", entity_desc or "", *(property_keywords or [])]
        keywords: List[str] = []
        seen = set()
        for part in raw_parts:
            for token in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", part):
                normalized = token.strip()
                if len(normalized) < 2:
                    continue
                key = normalized.upper()
                if key not in seen:
                    seen.add(key)
                    keywords.append(normalized)
        return keywords

    def _connect_to_oracle(self, source: SysDataSource):
        import base64
        import oracledb

        password = base64.b64decode(source.password_enc.encode()).decode()
        dsn_candidates = []
        if source.service_name:
            dsn_candidates.append(f"{source.host}:{source.port}/{source.service_name}")
            dsn_candidates.append(
                f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST={source.host})(PORT={source.port}))"
                f"(CONNECT_DATA=(SERVICE_NAME={source.service_name})))"
            )
        elif source.sid:
            dsn_candidates.append(f"{source.host}:{source.port}/{source.sid}")
            dsn_candidates.append(
                f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST={source.host})(PORT={source.port}))"
                f"(CONNECT_DATA=(SID={source.sid})))"
            )
        else:
            dsn_candidates.append(f"{source.host}:{source.port}")

        last_exc = None
        for index, dsn in enumerate(dsn_candidates, start=1):
            try:
                logger.info(
                    "Connect to Oracle source: source_name=%s host=%s port=%s service_or_sid=%s user=%s attempt=%s",
                    source.source_name,
                    source.host,
                    source.port,
                    source.service_name or source.sid or "",
                    source.username,
                    index,
                )
                return oracledb.connect(user=source.username, password=password, dsn=dsn)
            except Exception as exc:
                last_exc = exc
                logger.exception(
                    "Connect to Oracle source failed: source_name=%s attempt=%s",
                    source.source_name,
                    index,
                )
                time.sleep(0.5)

        raise ValueError(
            f"连接数据源失败: {source.source_name} ({source.host}:{source.port}/{source.service_name or source.sid or ''}) - {str(last_exc)}"
        ) from last_exc

    def _quote_identifier(self, identifier: str) -> str:
        return f'"{identifier.replace(chr(34), chr(34) * 2)}"'

    def _escape_sql_string(self, value: str) -> str:
        return value.replace("'", "''")

    def _build_comment_sql(
        self,
        object_type: str,
        owner: str,
        table_name: str,
        comment: Optional[str],
        column_name: Optional[str] = None,
    ) -> str:
        safe_owner = self._quote_identifier(owner)
        safe_table_name = self._quote_identifier(table_name)
        safe_comment = self._escape_sql_string(comment or "")
        if object_type == "TABLE":
            return f"COMMENT ON TABLE {safe_owner}.{safe_table_name} IS '{safe_comment}'"

        safe_column_name = self._quote_identifier(column_name or "")
        return f"COMMENT ON COLUMN {safe_owner}.{safe_table_name}.{safe_column_name} IS '{safe_comment}'"

    def _normalize_cell_value(self, value: Any) -> Any:
        if hasattr(value, "read"):
            try:
                lob_value = value.read()
                return self._normalize_cell_value(lob_value)
            except Exception:
                return None
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="seconds")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value) if value.as_tuple().exponent < 0 else int(value)
        if isinstance(value, bytes):
            return value.hex()
        return value

    def _is_mapping_supported_type(self, data_type: Optional[str]) -> bool:
        normalized = (data_type or "").upper().strip()
        if not normalized:
            return True
        base_type = normalized.split("(", 1)[0].strip()
        return base_type not in self.UNSUPPORTED_MAPPING_TYPES

    def _is_mapping_candidate_table(self, table_name: Optional[str]) -> bool:
        normalized = (table_name or "").upper().strip()
        if not normalized:
            return False
        return not any(normalized.startswith(prefix) for prefix in self.MAPPING_EXCLUDED_TABLE_PREFIXES)
