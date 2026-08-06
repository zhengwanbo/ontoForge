import json
import re
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.models.models import SysDataSource, SysOntologyBlueprint, SysOntologyEntity, SysOntologyProperty, SysOntologyRelation, SysDomain
from app.services.llm_service import LLMService
from app.services.source_data_service import SourceDataService


class DDLService:
    def __init__(self, db: Session):
        self.db = db

    async def generate_ddl(
        self,
        domain: SysDomain,
        entities: List[SysOntologyEntity],
        relations: List[SysOntologyRelation]
    ) -> Dict:
        """生成DDL"""
        unnamed_relations = [
            relation.relation_name or relation.relation_id
            for relation in relations
            if not (relation.relation_table_name or "").strip().upper().startswith("ONTO_EDGE_")
        ]
        if unnamed_relations:
            raise ValueError(
                "请先在数据映射管理中为每条关系填写唯一的英文边表名（如 BELONGS_TO）；"
                f"尚未完成：{', '.join(unnamed_relations)}"
            )
        blueprint_package = self._load_latest_blueprint(domain.domain_id)
        template_statements = self._generate_template_ddl(domain, entities, relations)

        # Try LLM generation first
        llm_service = LLMService(self.db)
        llm_result = await llm_service.generate_ddl_prompt(domain, entities, relations, blueprint_package=blueprint_package)

        # Parse DDL from LLM response or generate template DDL
        ddl_statements = self._parse_ddl_from_response(llm_result)
        ddl_statements = self._filter_to_required_object_views(ddl_statements, entities)
        ddl_statements = self._filter_unconfirmed_semantic_views(ddl_statements, blueprint_package)

        ddl_statements = self._merge_statements(template_statements, ddl_statements)

        semantic_statements = self._generate_semantic_layer_ddl(domain, blueprint_package, entities, relations)
        ddl_statements = self._merge_statements(ddl_statements, semantic_statements)
        # 重建前先只清理本次将要创建的本体对象；绝不删除业务源表。
        recreate_view_names = {
            (item.get("name") or "").strip().upper()
            for item in ddl_statements
            if (item.get("type") or "").strip().lower() == "create_view"
        }
        ddl_statements = (
            self._generate_obsolete_semantic_view_cleanup(
                blueprint_package,
                entities,
                relations,
                exclude_view_names=recreate_view_names,
            )
            + self._generate_recreate_cleanup_ddl(ddl_statements)
            + ddl_statements
        )

        return {
            "domain_id": domain.domain_id,
            "domain_name": domain.domain_name,
            "ddl_statements": ddl_statements,
            "full_ddl": "\n\n".join([s["sql"] for s in ddl_statements]),
            "entity_count": len(entities),
            "relation_count": len(relations),
            "blueprint_id": blueprint_package.get("blueprint_id") if blueprint_package else None,
            "blueprint_version": blueprint_package.get("blueprint_version") if blueprint_package else None,
        }

    def _filter_to_required_object_views(
        self,
        statements: List[Dict[str, Any]],
        entities: List[SysOntologyEntity],
    ) -> List[Dict[str, Any]]:
        """仅保留明确作为本体节点的 VIEW，排除 LLM 附带的分析/边视图。"""
        required_view_names = {
            (entity.table_name or f"ONTO_NODE_{entity.entity_name.upper()}_V").upper()
            for entity in entities
            if (entity.build_type or "").upper() == "VIEW"
        }
        filtered: List[Dict[str, Any]] = []
        for item in statements:
            statement_type = (item.get("type") or "").strip().lower()
            name = (item.get("name") or "").strip().upper()
            base_name = name.split(".", 1)[0]
            # 属性图必须由本服务基于实际会创建的节点/边表生成。
            # LLM 响应常引用被过滤掉的 VW_* 视图或并不存在的属性列。
            if statement_type == "create_graph":
                continue
            # 表/列注释由模板基于当前实体映射生成。LLM 附带的注释可能
            # 引用未被 CTAS 投影的列，且会与模板注释重复。
            if statement_type in {"comment_table", "comment_column"}:
                continue
            if statement_type == "create_view" and base_name not in required_view_names:
                continue
            filtered.append(item)
        return filtered

    def _filter_unconfirmed_semantic_views(
        self,
        statements: List[Dict[str, Any]],
        blueprint_package: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not blueprint_package:
            return statements
        semantic_plan = self._extract_semantic_plan(blueprint_package)
        deployment_design = semantic_plan.get("deployment_design") or {}
        blocked_names = {
            (item.get("view_name") or "").strip().upper()
            for item in (deployment_design.get("semantic_views") or [])
            if not item.get("deploy")
        }
        edge_blocked_names = {
            (item.get("view_name") or "").strip().upper()
            for item in (deployment_design.get("edge_views") or [])
            if not item.get("deploy")
        }
        if not blocked_names and not edge_blocked_names:
            return statements

        filtered: List[Dict[str, Any]] = []
        for item in statements:
            stmt_type = (item.get("type") or "").strip().lower()
            stmt_name = (item.get("name") or "").strip().upper()
            base_name = stmt_name.split(".", 1)[0]
            if stmt_type in {"create_view", "comment_table", "comment_column"} and (base_name in blocked_names or base_name in edge_blocked_names):
                continue
            filtered.append(item)
        return filtered

    def _merge_statements(self, primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged = list(primary)
        existing_keys = {
            (
                (item.get("type") or "").strip().lower(),
                (item.get("name") or "").strip().upper(),
            )
            for item in primary
        }
        for item in secondary:
            key = (
                (item.get("type") or "").strip().lower(),
                (item.get("name") or "").strip().upper(),
            )
            if key in existing_keys:
                continue
            merged.append(item)
            existing_keys.add(key)
        return merged

    def _generate_recreate_cleanup_ddl(self, statements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """为生成的目标对象生成可重复执行的清理语句，顺序为图、视图、表。"""
        names_by_type: Dict[str, List[str]] = {
            "create_graph": [],
            "create_view": [],
            "create_table": [],
        }
        for item in statements:
            statement_type = (item.get("type") or "").strip().lower()
            name = (item.get("name") or "").strip().upper()
            if statement_type in names_by_type and self._is_safe_ddl_identifier(name):
                if name not in names_by_type[statement_type]:
                    names_by_type[statement_type].append(name)

        cleanup: List[Dict[str, Any]] = []
        for name in names_by_type["create_graph"]:
            cleanup.append({"type": "drop_graph", "name": name, "sql": f"DROP PROPERTY GRAPH {name};"})
        for name in names_by_type["create_view"]:
            cleanup.append({"type": "drop_view", "name": name, "sql": f"DROP VIEW {name};"})
        for name in names_by_type["create_table"]:
            cleanup.append({"type": "drop_table", "name": name, "sql": f"DROP TABLE {name} CASCADE CONSTRAINTS PURGE;"})
        return cleanup

    def _generate_obsolete_semantic_view_cleanup(
        self,
        blueprint_package: Optional[Dict[str, Any]],
        entities: List[SysOntologyEntity],
        relations: List[SysOntologyRelation],
        exclude_view_names: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """清理同一 Blueprint 遗留的非节点语义/边视图。"""
        semantic_plan = self._extract_semantic_plan(blueprint_package or {})
        deployment_design = semantic_plan.get("deployment_design") or {}
        required_view_names = {
            (entity.table_name or f"ONTO_NODE_{entity.entity_name.upper()}_V").upper()
            for entity in entities
            if (entity.build_type or "").upper() == "VIEW"
        }
        excluded = {name.upper() for name in (exclude_view_names or set())}
        view_names = []
        for item in (deployment_design.get("semantic_views") or []) + (deployment_design.get("edge_views") or []):
            name = (item.get("view_name") or "").strip().upper()
            if (
                name
                and name not in required_view_names
                and name not in excluded
                and self._is_safe_ddl_identifier(name)
                and name not in view_names
            ):
                view_names.append(name)
        # 清理旧版由 relation.edge_sql 自动产生的边视图。它们不再参与部署，
        # 但需要在本次重建前移除，避免遗留对象造成误判。
        for relation in relations:
            name = self._build_relation_edge_view_name(relation).upper()
            if name not in excluded and self._is_safe_ddl_identifier(name) and name not in view_names:
                view_names.append(name)
        return [
            {
                "type": "drop_view",
                "name": name,
                "sql": f"DROP VIEW {name};",
            }
            for name in view_names
        ]

    def _is_safe_ddl_identifier(self, name: str) -> bool:
        return bool(re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", name or ""))

    def _ensure_blueprint_storage(self) -> None:
        SysOntologyBlueprint.__table__.create(bind=self.db.bind, checkfirst=True)

    def _load_latest_blueprint(self, domain_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_blueprint_storage()
        latest = (
            self.db.query(SysOntologyBlueprint)
            .filter(SysOntologyBlueprint.domain_id == domain_id)
            .order_by(SysOntologyBlueprint.version_no.desc(), SysOntologyBlueprint.created_at.desc())
            .first()
        )
        if not latest or not latest.blueprint_json:
            return None
        try:
            payload = json.loads(latest.blueprint_json)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        payload["blueprint_id"] = latest.blueprint_id
        payload["blueprint_version"] = latest.version_no
        payload["blueprint_status"] = latest.status
        return payload

    def _parse_ddl_from_response(self, response: str) -> List[Dict]:
        """从LLM响应中解析DDL语句"""
        statements = []
        # Try to find SQL blocks
        sql_pattern = r'(CREATE\s+(TABLE|VIEW|OR\s+REPLACE\s+VIEW|(?:OR\s+REPLACE\s+)?PROPERTY\s+GRAPH)[^;]+;|COMMENT\s+ON\s+(TABLE|COLUMN)[^;]+;)'
        matches = re.findall(sql_pattern, response, re.IGNORECASE | re.DOTALL)

        for match in matches:
            sql = match[0].strip()
            # Determine type
            if "CREATE TABLE" in sql.upper():
                stmt_type = "create_table"
            elif "CREATE VIEW" in sql.upper() or "CREATE OR REPLACE VIEW" in sql.upper():
                stmt_type = "create_view"
            elif "PROPERTY GRAPH" in sql.upper():
                stmt_type = "create_graph"
            elif "COMMENT ON TABLE" in sql.upper():
                stmt_type = "comment_table"
            elif "COMMENT ON COLUMN" in sql.upper():
                stmt_type = "comment_column"
            else:
                stmt_type = "other"

            statements.append({
                "type": stmt_type,
                "sql": sql,
                "name": self._extract_object_name(sql, stmt_type)
            })

        return statements

    def _generate_template_ddl(
        self,
        domain: SysDomain,
        entities: List[SysOntologyEntity],
        relations: List[SysOntologyRelation]
    ) -> List[Dict]:
        """生成模板DDL"""
        statements = []

        for entity in entities:
            if entity.build_type == "TABLE":
                sql = self._generate_table_ddl(entity)
                statements.append({
                    "type": "create_table",
                    "sql": sql,
                    "name": entity.table_name
                })
            elif entity.build_type == "VIEW":
                sql = self._generate_view_ddl(entity)
                statements.append({
                    "type": "create_view",
                    "sql": sql,
                    "name": entity.table_name
                })

            # Generate Comments DDL
            comment_sql = self._generate_comments_ddl(entity)
            for cs in comment_sql:
                statements.append(cs)

        # Generate relation tables for MANY_TO_MANY
        for relation in relations:
            sql = self._generate_relation_table_ddl(relation, entities)
            if sql:
                statements.append({
                    "type": "create_table",
                    "sql": sql,
                    "name": self._resolve_relation_storage_name(relation)
                })

        return statements

    def _generate_semantic_layer_ddl(
        self,
        domain: SysDomain,
        blueprint_package: Optional[Dict[str, Any]],
        entities: List[SysOntologyEntity],
        relations: List[SysOntologyRelation],
    ) -> List[Dict]:
        blueprint_package = blueprint_package or {}
        semantic_plan = self._extract_semantic_plan(blueprint_package)
        deployment_design = semantic_plan.get("deployment_design") or {}
        statements: List[Dict[str, Any]] = []

        for view in deployment_design.get("semantic_views") or []:
            if not view.get("deploy"):
                continue
            sql = self._generate_semantic_view_ddl(blueprint_package, view)
            if not sql:
                continue
            statements.append({
                "type": "create_view",
                "sql": sql,
                "name": (view.get("view_name") or "").strip().upper(),
            })

        for edge_view in deployment_design.get("edge_views") or []:
            # 边视图依赖多个标准化视图的列契约，未确认的候选 SQL 不能安全
            # 执行（例如原表投影中不存在 TEST_RUN_ID / PROCESS_EVENT_ID）。
            if not edge_view.get("deploy") or not edge_view.get("sql_confirmed"):
                continue
            sql = self._generate_edge_view_ddl(edge_view)
            if not sql:
                continue
            statements.append({
                "type": "create_view",
                "sql": sql,
                "name": (edge_view.get("view_name") or "").strip().upper(),
            })

        # 关系边已经由 _generate_template_ddl() 生成可入图的 ONTO_EDGE_* 表。
        # 历史 edge_sql 视图经常只有 SOURCE_ID/TARGET_ID，且中文关系名会被
        # 统一清洗成 EDGE，导致多个关系争用 ONTO_EDGE_EDGE_V。它们不能作为
        # Oracle Property Graph 的可靠边表，因此不再自动部署。

        graph_sql = self._generate_property_graph_ddl(
            domain=domain,
            blueprint_package=blueprint_package,
            entities=entities,
            relations=relations,
        )
        if graph_sql:
            statements.append({
                "type": "create_graph",
                "sql": graph_sql,
                "name": (deployment_design.get("property_graph") or {}).get("graph_name") or f"{domain.domain_id}_PG",
            })
        return statements

    def _generate_edge_view_ddl(self, edge_view: Dict[str, Any]) -> str:
        view_name = (edge_view.get("view_name") or "").strip()
        sql_body = (edge_view.get("sql") or "").strip().rstrip(";")
        purpose = (edge_view.get("purpose") or "关系边视图").replace("'", "''")
        if not view_name or not sql_body:
            return ""
        return f"""-- 关系边视图: {view_name}
CREATE OR REPLACE VIEW {view_name} AS
{sql_body};

COMMENT ON TABLE {view_name} IS '{purpose}';"""

    def _generate_relation_edge_view_ddls(self, relations: List[SysOntologyRelation]) -> List[Dict[str, Any]]:
        statements: List[Dict[str, Any]] = []
        for relation in relations:
            edge_sql = (relation_mapping.edge_sql or "").strip() if relation_mapping else ""
            if not edge_sql:
                continue
            view_name = self._build_relation_edge_view_name(relation)
            safe_relation_desc = (relation.relation_desc or relation.relation_name or "关系边视图").replace("'", "''")
            sql = f"""-- 关系边视图: {relation.relation_name or relation.relation_id}
CREATE OR REPLACE VIEW {view_name} AS
{edge_sql};

COMMENT ON TABLE {view_name} IS '{safe_relation_desc}';"""
            statements.append({
                "type": "create_view",
                "sql": sql,
                "name": view_name,
            })
        return statements

    def _generate_semantic_view_ddl(self, blueprint_package: Dict[str, Any], view: Dict[str, Any]) -> str:
        view_name = (view.get("view_name") or "").strip()
        explicit_sql = (view.get("sql") or "").strip().rstrip(";")
        sql_confirmed = bool(view.get("sql_confirmed"))
        source_tables = [str(item).strip().upper() for item in (view.get("source_tables") or []) if str(item).strip()]
        source_role = (view.get("source_role") or "other").strip().lower()
        purpose = (view.get("purpose") or "语义视图").replace("'", "''")
        if not view_name:
            return ""

        # LLM-produced view SQL is a design proposal.  It is frequently based
        # on inferred columns that are absent from the selected Oracle schema;
        # use it only after the mapping flow explicitly confirms it.  Until
        # then emit a safe projection from the selected source table.
        if explicit_sql and sql_confirmed:
            return f"""-- 语义视图: {view_name}
CREATE OR REPLACE VIEW {view_name} AS
{explicit_sql};

COMMENT ON TABLE {view_name} IS '{purpose}';"""

        if source_tables:
            select_columns = self._build_semantic_view_columns(
                source_id=blueprint_package.get("source_id"),
                schema=blueprint_package.get("schema"),
                table_name=source_tables[0],
                source_role=source_role,
            )
            sql = f"""-- 语义视图: {view_name}
CREATE OR REPLACE VIEW {view_name} AS
SELECT
    {select_columns}
FROM {source_tables[0]};

COMMENT ON TABLE {view_name} IS '{purpose}';"""
            return sql

        sql = f"""-- 派生语义视图: {view_name}
CREATE OR REPLACE VIEW {view_name} AS
SELECT
    CAST(NULL AS VARCHAR2(100)) AS semantic_id,
    CAST(NULL AS VARCHAR2(1000)) AS semantic_desc
FROM dual
WHERE 1 = 0;

COMMENT ON TABLE {view_name} IS '{purpose}';"""
        return sql

    def _build_semantic_view_columns(
        self,
        source_id: Optional[str],
        schema: Optional[str],
        table_name: str,
        source_role: str,
    ) -> str:
        source_service = SourceDataService(self.db)
        try:
            if not source_id:
                raise ValueError("missing source_id")
            detail = source_service.get_remote_table_detail(
                source_id=source_id,
                table_name=table_name,
                schema=schema,
                sample_limit=1,
            )
            columns = detail.get("columns") or []
        except Exception:
            columns = []

        prioritized = []
        role_keywords = {
            "entity_master": ["ID", "MODEL", "LOT", "BARCODE", "VCM", "MODULE", "SENSOR", "LENS"],
            "process_history": ["ID", "TIME", "MC_ID", "LINE", "OP_ID", "LOT", "CRR", "STATION"],
            "measurement": ["ID", "PASS_FAIL", "RESULT", "DCKEY", "SOCKET", "SFR", "VALUE", "METRIC"],
            "rule_catalog": ["FAMILY", "METRIC", "DB_NAME", "LSL", "USL", "RULE"],
            "case_library": ["CASE", "ROOT", "CAUSE", "ACTION", "MODEL", "SUMMARY"],
            "event_log": ["ID", "TIME", "EVENT", "ALARM", "LEVEL", "STATUS"],
        }
        keywords = role_keywords.get(source_role, ["ID", "NAME", "TYPE"])

        for column in columns[:200]:
            column_name = (column.get("column_name") or "").upper()
            if any(keyword in column_name for keyword in keywords):
                prioritized.append(column_name)
        if not prioritized:
            prioritized = [
                (column.get("column_name") or "").upper()
                for column in columns[:12]
                if (column.get("column_name") or "").strip()
            ]
        prioritized = prioritized[:16]
        if not prioritized:
            return "*"
        return ",\n    ".join(prioritized)

    def _generate_property_graph_ddl(
        self,
        domain: SysDomain,
        blueprint_package: Dict[str, Any],
        entities: List[SysOntologyEntity],
        relations: List[SysOntologyRelation],
    ) -> str:
        semantic_plan = self._extract_semantic_plan(blueprint_package)
        deployment_design = semantic_plan.get("deployment_design") or {}
        property_graph = deployment_design.get("property_graph") or {}
        graph_name = (property_graph.get("graph_name") or f"{domain.domain_id}_PG").strip()
        if not graph_name:
            return ""

        vertex_specs = []
        for entity in entities:
            key_property = next((prop.property_name.upper() for prop in entity.properties if prop.is_primary_key == "Y"), None)
            if not key_property:
                continue
            table_name = entity.table_name or f"ONTO_NODE_{entity.entity_name.upper()}"
            label = self._sanitize_graph_label(entity.entity_name)
            vertex_specs.append(
                f"  {table_name}\n    KEY ({key_property})\n    LABEL {label}\n    PROPERTIES ARE ALL COLUMNS"
            )

        edge_specs = []
        for relation in relations:
            relation_mapping = relation.relation_mapping
            source_entity = next((item for item in entities if item.entity_id == relation.source_entity_id), None)
            target_entity = next((item for item in entities if item.entity_id == relation.target_entity_id), None)
            if not source_entity or not target_entity:
                continue
            source_pk = next((prop.property_name.upper() for prop in source_entity.properties if prop.is_primary_key == "Y"), None)
            target_pk = next((prop.property_name.upper() for prop in target_entity.properties if prop.is_primary_key == "Y"), None)
            if not source_pk or not target_pk:
                continue

            if not self._relation_join_is_deployable(relation, source_entity, target_entity):
                continue
            edge_table_name = self._resolve_relation_storage_name(relation)
            edge_specs.append(
                f"  {edge_table_name}\n"
                f"    KEY (EDGE_ID)\n"
                f"    SOURCE KEY (SOURCE_ID) REFERENCES {source_entity.table_name or f'ONTO_NODE_{source_entity.entity_name.upper()}'} ({source_pk})\n"
                f"    DESTINATION KEY (TARGET_ID) REFERENCES {target_entity.table_name or f'ONTO_NODE_{target_entity.entity_name.upper()}'} ({target_pk})\n"
                f"    LABEL {self._sanitize_graph_label(relation.relation_name or relation.relation_type)}\n"
                f"    PROPERTIES ARE ALL COLUMNS"
            )

        if not vertex_specs:
            return ""

        if edge_specs:
            return "CREATE OR REPLACE PROPERTY GRAPH {graph_name}\nVERTEX TABLES (\n{vertices}\n)\nEDGE TABLES (\n{edges}\n)\nOPTIONS (ALLOW MIXED PROPERTY TYPES);".format(
                graph_name=graph_name,
                vertices=",\n".join(vertex_specs),
                edges=",\n".join(edge_specs),
            )
        return "CREATE OR REPLACE PROPERTY GRAPH {graph_name}\nVERTEX TABLES (\n{vertices}\n)\nOPTIONS (ALLOW MIXED PROPERTY TYPES);".format(
            graph_name=graph_name,
            vertices=",\n".join(vertex_specs),
        )

    def _extract_semantic_plan(self, blueprint_package: Dict[str, Any]) -> Dict[str, Any]:
        view_plan = blueprint_package.get("view_plan") or {}
        if view_plan:
            standardized_views = []
            for item in view_plan.get("standardized_views") or []:
                standardized_views.append({
                    "view_name": (item.get("view_name") or "").strip().upper(),
                    "view_kind": item.get("view_kind") or "standardized",
                    "source_role": item.get("source_role") or "standardized",
                    "source_tables": [str(x).strip().upper() for x in (item.get("source_tables") or []) if str(x).strip()],
                    "purpose": item.get("purpose") or "",
                    "deploy": bool(item.get("deploy")),
                    "deploy_reason": item.get("deploy_reason") or "",
                    "sql": item.get("sql"),
                    "sql_confirmed": bool(
                        item.get("sql_confirmed")
                        or str(item.get("mapping_status") or "").upper() == "CONFIRMED"
                    ),
                })
            edge_views = []
            for item in view_plan.get("edge_views") or []:
                edge_views.append({
                    "view_name": (item.get("view_name") or "").strip().upper(),
                    "purpose": item.get("purpose") or "",
                    "deploy": bool(item.get("deploy")),
                    "deploy_reason": item.get("deploy_reason") or "",
                    "source_tables": [str(x).strip().upper() for x in ((item.get("source_tables") or item.get("source_views") or [])) if str(x).strip()],
                    "sql": item.get("sql"),
                    "sql_confirmed": bool(
                        item.get("sql_confirmed")
                        or str(item.get("mapping_status") or "").upper() == "CONFIRMED"
                    ),
                })
            graph_layer = view_plan.get("graph_layer") or {}
            property_graph = {
                "graph_name": str(graph_layer.get("graph_name") or "ONTOLOGY_PG").strip().upper(),
                "vertex_entities": [str(x).strip() for x in (graph_layer.get("vertex_entities") or []) if str(x).strip()],
                "edge_relations": [str(x).strip() for x in (graph_layer.get("edge_relations") or []) if str(x).strip()],
                "note": str(graph_layer.get("note") or "").strip(),
            }
            return {
                "view_plan": view_plan,
                "deployment_design": {
                    "semantic_views": standardized_views,
                    "edge_views": edge_views,
                    "property_graph": property_graph,
                },
            }
        return {
            "view_plan": {},
            "deployment_design": blueprint_package.get("deployment_design") or {},
        }

    def _sanitize_graph_label(self, value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9_]+", "_", (value or "").upper()).strip("_")
        return token or "GRAPH_LABEL"

    def _build_relation_edge_view_name(self, relation: SysOntologyRelation) -> str:
        raw_name = relation.relation_name or relation.relation_id or "EDGE"
        token = re.sub(r"[^A-Za-z0-9_]+", "_", raw_name.upper()).strip("_")
        token = token[:20] or "EDGE"
        return f"ONTO_EDGE_{token}_V"

    def _generate_table_ddl(self, entity: SysOntologyEntity) -> str:
        """生成实体表DDL，数据直接来源于源数据表或实体映射SQL"""
        table_name = entity.table_name or f"ONTO_NODE_{entity.entity_name.upper()}"
        source_query = self._build_entity_source_query(entity)
        pk_columns = [prop.property_name.upper() for prop in entity.properties if prop.is_primary_key == "Y"]
        ddl = f"""-- 实体: {entity.entity_name} ({entity.entity_display_name})
-- 构建方式: Source-backed Management Table
CREATE TABLE {table_name} AS
{source_query};"""
        if pk_columns:
            null_predicate = " OR ".join(f"t.{column} IS NULL" for column in pk_columns)
            same_key_predicate = " AND ".join(f"d.{column} = t.{column}" for column in pk_columns)
            ddl += f"""

-- CTAS 来源可能包含同一业务主键的多条履历；保留一条记录后再建立节点主键。
DELETE FROM {table_name} t
WHERE {null_predicate}
   OR t.ROWID > (
       SELECT MIN(d.ROWID)
       FROM {table_name} d
       WHERE {same_key_predicate}
   );

ALTER TABLE {table_name}
ADD CONSTRAINT PK_{table_name} PRIMARY KEY ({', '.join(pk_columns)});"""
        return ddl

    def _generate_view_ddl(self, entity: SysOntologyEntity) -> str:
        """生成实体视图DDL，数据直接来源于源数据表或实体映射SQL"""
        view_name = entity.table_name or f"ONTO_NODE_{entity.entity_name.upper()}_V"
        source_query = self._build_entity_source_query(entity)
        ddl = f"""-- 实体: {entity.entity_name} ({entity.entity_display_name})
-- 构建方式: Source-backed Management View
CREATE OR REPLACE VIEW {view_name} AS
{source_query};"""

        return ddl

    def _generate_comments_ddl(self, entity: SysOntologyEntity) -> List[Dict]:
        """生成Comments DDL"""
        table_name = entity.table_name or f"ONTO_NODE_{entity.entity_name.upper()}"
        statements = []

        entity_mapping = getattr(entity, "entity_mapping", None)
        explicit_sql = (entity_mapping.view_sql or "").strip() if entity_mapping else ""
        # CTAS 形式的显式 SQL 有时只输出实体属性的子集。仅对明确出现在
        # SELECT 别名中的列生成列注释，避免 ORA-00904 / ORA-00942 类失败。
        projected_columns = {
            column.upper()
            for column in re.findall(r"\bAS\s+([A-Za-z][A-Za-z0-9_$#]*)\b", explicit_sql, re.IGNORECASE)
        } if explicit_sql else set()

        # Table comment
        desc = entity.entity_desc or entity.entity_display_name or entity.entity_name
        sql = f"COMMENT ON TABLE {table_name} IS '{desc}';"
        statements.append({
            "type": "comment_table",
            "sql": sql,
            "name": f"{table_name} (table)"
        })

        # Column comments
        for prop in entity.properties:
            if projected_columns and (prop.property_name or "").upper() not in projected_columns:
                continue
            prop_desc = prop.property_desc or prop.property_display_name or prop.property_name
            sql = f"COMMENT ON COLUMN {table_name}.{prop.property_name.upper()} IS '{prop_desc}';"
            statements.append({
                "type": "comment_column",
                "sql": sql,
                "name": f"{table_name}.{prop.property_name.upper()}"
            })

        return statements

    def _generate_relation_table_ddl(self, relation: SysOntologyRelation, entities: List[SysOntologyEntity]) -> Optional[str]:
        """从本体节点表及其主键生成 Oracle Property Graph 边表。"""
        entity_by_id = {entity.entity_id: entity for entity in entities}
        source_entity = entity_by_id.get(relation.source_entity_id)
        target_entity = entity_by_id.get(relation.target_entity_id)
        if not source_entity or not target_entity:
            raise ValueError(f"关系 {relation.relation_name or relation.relation_id} 的两端本体对象不存在")

        source_key = self._get_entity_primary_key(source_entity)
        target_key = self._get_entity_primary_key(target_entity)
        if not source_key or not target_key:
            raise ValueError(
                f"关系 {relation.relation_name or relation.relation_id} 的两端本体对象必须各自配置唯一主键后才能生成边表"
            )

        source_table = source_entity.table_name or f"ONTO_NODE_{source_entity.entity_name.upper()}"
        target_table = target_entity.table_name or f"ONTO_NODE_{target_entity.entity_name.upper()}"
        relation_mapping = relation.relation_mapping
        join_condition = self._build_relation_join_condition(
            relation,
            source_entity,
            target_entity,
            source_key,
            target_key,
        )
        # A relation can be part of the conceptual ontology before its edge
        # mapping is complete.  Do not emit an invalid CTAS which would make a
        # whole deployment fail; it will be included once its mapping refers to
        # columns that actually exist in the two node objects.
        if join_condition is None:
            return None

        rel_table = self._resolve_relation_storage_name(relation)
        safe_name = (relation.relation_name or "").replace("'", "''")
        safe_desc = (relation.relation_desc or relation.relation_name or "关系数据").replace("'", "''")
        ddl = f"""-- 关系表: {relation.relation_name or relation.relation_id}
-- 源节点: {source_table}({source_key}) -> 目标节点: {target_table}({target_key})
-- 关系 Join: {join_condition}
CREATE TABLE {rel_table} AS
SELECT
    ROW_NUMBER() OVER (ORDER BY src.{source_key}, dst.{target_key}) AS EDGE_ID,
    src.{source_key} AS SOURCE_ID,
    dst.{target_key} AS TARGET_ID,
    '{safe_name}' AS RELATION_NAME,
    '{safe_desc}' AS RELATION_DESC
FROM {source_table} src
JOIN {target_table} dst ON {join_condition};

ALTER TABLE {rel_table}
ADD CONSTRAINT PK_{rel_table[:95]} PRIMARY KEY (EDGE_ID);

COMMENT ON TABLE {rel_table} IS '{safe_desc}';"""

        return ddl

    def _relation_join_is_deployable(
        self,
        relation: SysOntologyRelation,
        source_entity: SysOntologyEntity,
        target_entity: SysOntologyEntity,
    ) -> bool:
        source_key = self._get_entity_primary_key(source_entity)
        target_key = self._get_entity_primary_key(target_entity)
        return bool(
            source_key
            and target_key
            and self._build_relation_join_condition(
                relation,
                source_entity,
                target_entity,
                source_key,
                target_key,
            )
        )

    def _build_relation_join_condition(
        self,
        relation: SysOntologyRelation,
        source_entity: SysOntologyEntity,
        target_entity: SysOntologyEntity,
        source_key: str,
        target_key: str,
    ) -> Optional[str]:
        relation_mapping = relation.relation_mapping
        join_condition = (relation_mapping.join_condition or "").strip() if relation_mapping else ""
        canonical_join = self._get_tams_canonical_relation_join(
            source_entity,
            target_entity,
        )
        # The structured TAMS model has several identity relationships whose
        # join semantics are deterministic.  Older mapping suggestions can
        # incorrectly bind them to SOURCE_TABLE_NAME or a conceptual property
        # that was not projected by the physical node.  Prefer this canonical
        # join only when the corresponding columns are present in node output.
        if canonical_join and self._relation_join_columns_exist(canonical_join, source_entity, target_entity):
            return canonical_join
        if not join_condition:
            return f"src.{source_key} = dst.{target_key}"

        normalized = self._normalize_relation_join_condition(
            join_condition,
            relation_mapping.source_table if relation_mapping else None,
            relation_mapping.target_table if relation_mapping else None,
            source_entity,
            target_entity,
        )
        if not normalized or not self._relation_join_columns_exist(normalized, source_entity, target_entity):
            return None
        return normalized

    def _get_tams_canonical_relation_join(
        self,
        source_entity: SysOntologyEntity,
        target_entity: SysOntologyEntity,
    ) -> str:
        pair = (
            (source_entity.entity_name or "").strip().upper(),
            (target_entity.entity_name or "").strip().upper(),
        )
        joins = {
            ("PRODUCTUNIT", "PRODUCTMODEL"): (
                "src.MODEL = dst.MODEL_CODE "
                "AND NVL(TRIM(UPPER(src.CONFIG)), '~') = NVL(dst.CONFIG_NAME_STD, '~')"
            ),
            ("PRODUCTUNIT", "TESTRUN"): "src.VCM_ID = dst.VCM_ID",
            ("TESTRUN", "METRICRESULT"): "src.TEST_RUN_ID = dst.TEST_RUN_ID",
            # The previously generated AlarmEvent CTAS projects VCM_ID.  New
            # canonical blueprints also expose PRODUCT_UNIT_ID, but retaining
            # this join keeps existing deployed mapping SQL compatible.
            ("PRODUCTUNIT", "ALARMEVENT"): "src.VCM_ID = dst.VCM_ID",
            ("PRODUCTUNIT", "AALOGFEATURE"): "src.VCM_ID = dst.PRODUCT_UNIT_ID",
            ("PRODUCTUNIT", "PROCESSEVENT"): "src.VCM_ID = dst.PRODUCT_UNIT_ID",
            ("METRICRESULT", "DEFECTTYPE"): (
                "dst.DEFECT_TYPE_CODE = CASE "
                "WHEN UPPER(src.METRIC_NAME) LIKE '%CEN%' THEN 'CENTER_SFR_LOW' "
                "WHEN UPPER(src.METRIC_NAME) LIKE '%EDGE%' THEN 'EDGE_SFR_LOW' "
                "WHEN UPPER(src.METRIC_NAME) LIKE '%LR%' OR UPPER(src.METRIC_NAME) LIKE '%LEFT%' OR UPPER(src.METRIC_NAME) LIKE '%RIGHT%' THEN 'LR_ASYMMETRY' "
                "WHEN UPPER(src.METRIC_NAME) LIKE '%TB%' OR UPPER(src.METRIC_NAME) LIKE '%TOP%' OR UPPER(src.METRIC_NAME) LIKE '%BOTTOM%' THEN 'TB_ASYMMETRY' "
                "WHEN UPPER(src.METRIC_NAME) LIKE '%FOCUS%' OR UPPER(src.METRIC_NAME) LIKE '%DISPLACEMENT%' THEN 'FOCUS_COMPENSATION_ABNORMAL' "
                "ELSE 'SFR_DEFECT_OTHER' END"
            ),
            ("PROCESSEVENT", "STATION"): "src.STATION_CODE = dst.STATION_CODE",
            ("PROCESSEVENT", "EQUIPMENT"): "src.EQUIPMENT_CODE = dst.EQUIPMENT_ID",
            ("PROCESSEVENT", "TOOLINGCARRIER"): "src.TOOLING_ID = dst.TOOLING_ID",
            ("PROCESSEVENT", "MATERIALLOT"): "src.MATERIAL_LOT_ID = dst.MATERIAL_LOT_ID",
        }
        return joins.get(pair, "")

    def _get_entity_primary_key(self, entity: SysOntologyEntity) -> Optional[str]:
        for prop in entity.properties or []:
            if (prop.is_primary_key or "").upper() == "Y":
                return (prop.property_name or "").strip().upper() or None
        return None

    def _normalize_relation_join_condition(
        self,
        join_condition: str,
        mapped_source_table: Optional[str],
        mapped_target_table: Optional[str],
        source_entity: Optional[SysOntologyEntity] = None,
        target_entity: Optional[SysOntologyEntity] = None,
    ) -> str:
        """将映射阶段的来源表引用转换为边表生成时的节点表别名。"""
        normalized = (join_condition or "").strip().rstrip(";")
        if not normalized:
            return ""
        for table_name, alias in ((mapped_source_table, "src"), (mapped_target_table, "dst")):
            table = (table_name or "").strip()
            if table:
                normalized = re.sub(rf"(?i)\b{re.escape(table)}\s*\.", f"{alias}.", normalized)
        # Older mapping suggestions used ``tgt`` while generated edge CTAS
        # consistently names the target node alias ``dst``.
        normalized = re.sub(r"(?i)\btgt\s*\.", "dst.", normalized)

        for alias, entity in (("src", source_entity), ("dst", target_entity)):
            if not entity:
                continue
            mapped_names: Dict[str, str] = {}
            for prop in entity.properties or []:
                property_name = (prop.property_name or "").strip().upper()
                if not property_name:
                    continue
                mapped_names[property_name] = property_name
                mapping = getattr(prop, "mapping", None)
                source_column = (getattr(mapping, "source_column", None) or "").strip().upper()
                if source_column:
                    mapped_names[source_column] = property_name
            for source_column, property_name in mapped_names.items():
                normalized = re.sub(
                    rf"(?i)\b{alias}\s*\.\s*{re.escape(source_column)}\b",
                    f"{alias}.{property_name}",
                    normalized,
                )
        return normalized

    def _relation_join_columns_exist(
        self,
        join_condition: str,
        source_entity: SysOntologyEntity,
        target_entity: SysOntologyEntity,
    ) -> bool:
        columns_by_alias = {
            "SRC": self._get_relation_output_columns(source_entity),
            "DST": self._get_relation_output_columns(target_entity),
        }
        for alias, column in re.findall(r"(?i)\b(src|dst)\s*\.\s*([A-Za-z][A-Za-z0-9_$#]*)", join_condition):
            if column.upper() not in columns_by_alias[alias.upper()]:
                return False
        return True

    def _get_relation_output_columns(self, entity: SysOntologyEntity) -> set[str]:
        columns = {(prop.property_name or "").strip().upper() for prop in entity.properties or []}
        entity_mapping = getattr(entity, "entity_mapping", None)
        explicit_sql = (getattr(entity_mapping, "view_sql", None) or "").upper()
        # Explicit node CTAS/VIEW SQL can contain a bare projected identifier,
        # e.g. `VCM_ID, ...`, even when an older ontology property was omitted.
        # Restrict this supplement to known identifier-style outputs.
        for identifier in (
            "VCM_ID", "TEST_RUN_ID", "MODEL", "CONFIG", "MODEL_CODE", "CONFIG_NAME_STD",
            "PRODUCT_UNIT_ID", "STATION_CODE", "TOOLING_ID", "EQUIPMENT_CODE", "EQUIPMENT_ID",
            "MATERIAL_LOT_ID", "METRIC_NAME", "DEFECT_TYPE_CODE",
        ):
            if re.search(rf"\b{identifier}\b", explicit_sql):
                columns.add(identifier)
        return columns

    def _build_entity_source_query(self, entity: SysOntologyEntity) -> str:
        entity_mapping = getattr(entity, "entity_mapping", None)
        explicit_sql = (entity_mapping.view_sql or "").strip() if entity_mapping and entity_mapping.view_sql else ""
        if explicit_sql:
            # Oracle VARCHAR2 必须声明长度；LLM 偶尔生成 CAST(NULL AS VARCHAR2)。
            return re.sub(
                r"(?i)CAST\(\s*NULL\s+AS\s+VARCHAR2\s*\)",
                "CAST(NULL AS VARCHAR2(500))",
                explicit_sql.rstrip(";"),
            )

        mapped_properties = [
            prop for prop in (entity.properties or [])
            if getattr(prop, "mapping", None)
            and (
                ((prop.mapping.mapping_type or "").upper() == "DIRECT" and (prop.mapping.source_table or "").strip() and (prop.mapping.source_column or "").strip())
                or ((prop.mapping.mapping_type or "").upper() == "COMPUTED" and (prop.mapping.source_table or "").strip() and (prop.mapping.formula_expr or "").strip())
            )
        ]
        if not mapped_properties:
            raise ValueError(f"实体 {entity.entity_display_name or entity.entity_name} 缺少已确认的源数据映射，无法生成来源于源表的数据DDL")

        source_tables: List[str] = []
        for prop in mapped_properties:
            table_name = (prop.mapping.source_table or "").strip().upper()
            if table_name and table_name not in source_tables:
                source_tables.append(table_name)
        if len(source_tables) != 1:
            raise ValueError(
                f"实体 {entity.entity_display_name or entity.entity_name} 使用了多个源表映射，请先在数据映射中维护 entity_mapping.view_sql，再生成DDL"
            )

        alias = "src"
        select_columns: List[str] = []
        for prop in (entity.properties or []):
            prop_name = (prop.property_name or "").strip().upper()
            mapping = getattr(prop, "mapping", None)
            if mapping:
                mapping_type = (mapping.mapping_type or "").strip().upper()
                if mapping_type == "DIRECT" and (mapping.source_column or "").strip():
                    select_columns.append(f"    {alias}.{mapping.source_column.strip().upper()} AS {prop_name}")
                    continue
                if mapping_type == "COMPUTED" and (mapping.formula_expr or "").strip():
                    select_columns.append(f"    {mapping.formula_expr.strip()} AS {prop_name}")
                    continue
            select_columns.append(f"    CAST(NULL AS {self._map_data_type(prop.data_type or 'VARCHAR2')}) AS {prop_name}")

        return "SELECT\n{cols}\nFROM {table_name} {alias}".format(
            cols=",\n".join(select_columns),
            table_name=source_tables[0],
            alias=alias,
        )

    def _resolve_relation_storage_name(self, relation: SysOntologyRelation) -> str:
        if relation.relation_table_name:
            return relation.relation_table_name.strip().upper()
        raw_name = relation.relation_name or relation.relation_id or "EDGE"
        token = re.sub(r"[^A-Za-z0-9_]+", "_", raw_name.upper()).strip("_")
        # 中文等非 ASCII 关系名会被清洗为空，过去统一回退为 EDGE，导致
        # 多条关系都生成 ONTO_EDGE_EDGE。无显式边表名时，附加关系 ID
        # 作为稳定且唯一的 Oracle 对象名后缀。
        token = token[:20] or "EDGE"
        relation_id = re.sub(r"[^A-Za-z0-9_]+", "_", (relation.relation_id or "").upper()).strip("_")
        if not relation_id:
            relation_id = "RELATION"
        return f"ONTO_EDGE_{token}_{relation_id}"

    def _map_data_type(self, ontology_type: str) -> str:
        """映射本体数据类型到 Oracle 类型"""
        type_mapping = {
            "VARCHAR2": "VARCHAR2(500)",
            "NUMBER": "NUMBER",
            "NUMBER(10)": "NUMBER(10)",
            "NUMBER(10,4)": "NUMBER(10,4)",
            "DATE": "DATE",
            "TIMESTAMP": "TIMESTAMP",
            "CLOB": "CLOB",
            "BLOB": "BLOB",
            "CHAR(1)": "CHAR(1)",
            "FLOAT": "FLOAT",
            "INTEGER": "NUMBER(10)",
        }
        return type_mapping.get(ontology_type.upper(), ontology_type)

    def _extract_object_name(self, sql: str, stmt_type: str) -> str:
        """从DDL中提取对象名"""
        patterns = {
            "create_table": r"CREATE\s+TABLE\s+(\w+)",
            "create_view": r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(\w+)",
            "create_graph": r"CREATE\s+(?:OR\s+REPLACE\s+)?PROPERTY\s+GRAPH\s+(\w+)",
            "comment_table": r"COMMENT\s+ON\s+TABLE\s+(\w+)",
            "comment_column": r"COMMENT\s+ON\s+COLUMN\s+(\w+\.\w+)"
        }
        pattern = patterns.get(stmt_type, r"(\w+)")
        match = re.search(pattern, sql, re.IGNORECASE)
        return match.group(1) if match else "unknown"

    async def execute_ddl(
        self,
        ddl_content: str,
        target_source: SysDataSource,
        execute_mode: str = "all",
        skip_existing: bool = False,
    ) -> Dict:
        """通过选定的业务对象数据库执行 DDL，平台元数据库不参与对象创建。"""
        # Split the generated script and remove comment-only lines without
        # discarding the CREATE statement that follows a leading description.
        statements = []
        for raw_statement in ddl_content.split(";"):
            cleaned = "\n".join(
                line
                for line in raw_statement.splitlines()
                if not line.strip().startswith("--")
            ).strip()
            if cleaned:
                statements.append(cleaned)

        source_service = SourceDataService(self.db)
        connection = None
        cursor = None
        results = []
        try:
            connection = source_service._connect_to_oracle(target_source)
            cursor = connection.cursor()
            for stmt in statements:
                # Oracle drivers expect executable SQL without a SQL*Plus terminator.
                stmt = stmt.strip().rstrip(";").strip()

                # Oracle does not support DROP ... IF EXISTS.  Probe the target
                # dictionary first so an initial deployment never reports the
                # absence of historical generated objects as an execution error.
                if self._is_drop_statement(stmt) and not self._ddl_object_exists(cursor, stmt):
                    results.append({
                        "statement": stmt,
                        "status": "skipped",
                        "message": "目标数据库对象不存在，无需清理",
                        **self._describe_ddl_statement(stmt),
                    })
                    continue

                if skip_existing and self._ddl_object_exists(cursor, stmt):
                    results.append({
                        "statement": stmt,
                        "status": "skipped",
                        "message": "目标数据库对象已存在",
                        **self._describe_ddl_statement(stmt),
                    })
                    continue

                try:
                    cursor.execute(stmt)
                    connection.commit()
                    results.append({"statement": stmt, "status": "success", **self._describe_ddl_statement(stmt)})
                except Exception as exc:
                    connection.rollback()
                    if self._is_missing_ddl_object_error(exc, stmt):
                        results.append({
                            "statement": stmt,
                            "status": "skipped",
                            "message": "目标数据库对象不存在，无需清理",
                            **self._describe_ddl_statement(stmt),
                        })
                        continue
                    results.append({"statement": stmt, "status": "failed", "error": str(exc), **self._describe_ddl_statement(stmt)})
        finally:
            try:
                if cursor:
                    cursor.close()
            finally:
                if connection:
                    connection.close()

        success_count = sum(1 for r in results if r["status"] == "success")
        failed_count = sum(1 for r in results if r["status"] == "failed")
        skipped_count = sum(1 for r in results if r["status"] == "skipped")

        return {
            "total": len(results),
            "success": success_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "details": results
        }

    def _is_missing_ddl_object_error(self, exc: Exception, statement: str) -> bool:
        if not re.match(r"^DROP\s+", statement or "", re.IGNORECASE):
            return False
        error_text = str(exc).upper()
        return any(code in error_text for code in ("ORA-00942", "ORA-04043", "ORA-42421"))

    def _is_drop_statement(self, statement: str) -> bool:
        return bool(re.match(r"^DROP\s+(?:TABLE|VIEW|PROPERTY\s+GRAPH)\b", statement or "", re.IGNORECASE))

    def _ddl_object_exists(self, cursor, statement: str) -> bool:
        patterns = [
            (r"(?:CREATE|DROP)\s+TABLE(?:\s+IF\s+EXISTS)?\s+(\w+)", "TABLE"),
            (r"(?:CREATE\s+(?:OR\s+REPLACE\s+)?|DROP\s+)VIEW(?:\s+IF\s+EXISTS)?\s+(\w+)", "VIEW"),
            (r"(?:CREATE\s+(?:OR\s+REPLACE\s+)?|DROP\s+)PROPERTY\s+GRAPH(?:\s+IF\s+EXISTS)?\s+(\w+)", "PROPERTY GRAPH"),
        ]
        for pattern, object_type in patterns:
            match = re.search(pattern, statement, re.IGNORECASE)
            if not match:
                continue
            cursor.execute(
                "SELECT 1 FROM USER_OBJECTS WHERE OBJECT_NAME = :object_name AND OBJECT_TYPE = :object_type",
                {"object_name": match.group(1).upper(), "object_type": object_type},
            )
            return cursor.fetchone() is not None
        return False

    def _describe_ddl_statement(self, statement: str) -> Dict[str, str]:
        patterns = [
            (r"(?:CREATE|DROP)\s+TABLE(?:\s+IF\s+EXISTS)?\s+(\w+)", "TABLE"),
            (r"(?:CREATE\s+(?:OR\s+REPLACE\s+)?|DROP\s+)VIEW(?:\s+IF\s+EXISTS)?\s+(\w+)", "VIEW"),
            (r"(?:CREATE\s+(?:OR\s+REPLACE\s+)?|DROP\s+)PROPERTY\s+GRAPH(?:\s+IF\s+EXISTS)?\s+(\w+)", "PROPERTY GRAPH"),
            (r"COMMENT\s+ON\s+(?:TABLE|COLUMN)\s+([\w.]+)", "COMMENT"),
            (r"ALTER\s+TABLE\s+(\w+)", "TABLE"),
        ]
        for pattern, object_type in patterns:
            match = re.search(pattern, statement, re.IGNORECASE)
            if match:
                return {"object_type": object_type, "object_name": match.group(1).upper()}
        return {"object_type": "SQL", "object_name": "-"}
