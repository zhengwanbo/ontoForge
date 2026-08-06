from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
import re
import time
from sqlalchemy.orm import Session
from sqlalchemy import or_
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
            query = query.filter(
                or_(
                    SysDataSource.business_domain_id == domain_id,
                    SysDataSource.business_domain_id.is_(None),
                )
            )
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

        def action(_connection, cursor):
            nonlocal columns, preview_rows
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
            query = query.filter(
                or_(
                    SysDataSource.business_domain_id == domain_id,
                    SysDataSource.business_domain_id.is_(None),
                )
            )
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
