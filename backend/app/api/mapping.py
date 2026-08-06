import asyncio
import json
import re
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db, SessionLocal
from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.schemas.schemas import (
    ApiResponse, PropertyMappingCreate, PropertyMappingUpdate,
    PropertyMappingResponse, EntityMappingUpdate, RelationMappingCreate,
    RelationMappingUpdate, EdgeSqlPreviewRequest, RelationJoinAnalyzeRequest, AutoMappingRequest, MappingConfirmRequest,
    BulkAutoMappingRequest, BulkMappingApplyRequest
)
from app.models.models import (
    SysPropertyMapping, SysEntityMapping, SysRelationMapping,
    SysOntologyBlueprint, SysOntologyEntity, SysOntologyProperty, SysOntologyRelation, SysDomain, SysMappingTask,
    generate_id
)

router = APIRouter(prefix="/mapping", tags=["数据映射"])
logger = get_logger(__name__)


def _normalize_edge_table_name(value: Optional[str]) -> str:
    """将数据映射中确认的英文关系名标准化为 Oracle 边表名。"""
    token = (value or "").strip().upper()
    if not token:
        return ""
    if token.startswith("ONTO_EDGE_"):
        token = token[len("ONTO_EDGE_"):]
    if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,116}", token):
        return ""
    return f"ONTO_EDGE_{token}"


def _set_relation_edge_table_name(
    db: Session,
    relation: SysOntologyRelation,
    value: Optional[str],
) -> None:
    if value is None:
        relation.relation_table_name = None
        return
    normalized = _normalize_edge_table_name(value)
    if value.strip() and not normalized:
        raise HTTPException(status_code=400, detail="英文关系名只能包含英文字母、数字、下划线、$ 或 #")
    if normalized:
        duplicate = db.query(SysOntologyRelation).filter(
            SysOntologyRelation.domain_id == relation.domain_id,
            SysOntologyRelation.relation_id != relation.relation_id,
            SysOntologyRelation.relation_table_name == normalized,
        ).first()
        if duplicate:
            raise HTTPException(status_code=400, detail=f"边表名 {normalized} 已被关系「{duplicate.relation_name}」使用")
    relation.relation_table_name = normalized or None


def _ensure_blueprint_storage(db: Session):
    SysOntologyBlueprint.__table__.create(bind=db.bind, checkfirst=True)


def _load_latest_blueprint_payload(db: Session, domain_id: str) -> Optional[dict]:
    _ensure_blueprint_storage(db)
    latest = (
        db.query(SysOntologyBlueprint)
        .filter(SysOntologyBlueprint.domain_id == domain_id)
        .order_by(SysOntologyBlueprint.version_no.desc(), SysOntologyBlueprint.created_at.desc())
        .first()
    )
    return _serialize_blueprint_payload(latest)


def _load_blueprint_payload_by_id(db: Session, blueprint_id: Optional[str]) -> Optional[dict]:
    if not blueprint_id:
        return None
    _ensure_blueprint_storage(db)
    blueprint = (
        db.query(SysOntologyBlueprint)
        .filter(SysOntologyBlueprint.blueprint_id == blueprint_id)
        .first()
    )
    return _serialize_blueprint_payload(blueprint)


def _serialize_blueprint_payload(blueprint: Optional[SysOntologyBlueprint]) -> Optional[dict]:
    if not blueprint or not blueprint.blueprint_json:
        return None
    try:
        payload = json.loads(blueprint.blueprint_json)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    payload["blueprint_id"] = blueprint.blueprint_id
    payload["blueprint_version"] = blueprint.version_no
    payload["blueprint_status"] = blueprint.status
    return payload


def _extract_task_blueprint_version(task: SysMappingTask) -> Optional[int]:
    for raw in [task.request_json, task.result_json, task.summary_json]:
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        version = payload.get("blueprint_version")
        if version is None and isinstance(payload.get("summary"), dict):
            version = payload["summary"].get("blueprint_version")
        if version is None:
            continue
        try:
            return int(version)
        except (TypeError, ValueError):
            continue
    return None


def _find_blueprint_entity_recommendation(blueprint_payload: Optional[dict], entity: SysOntologyEntity) -> Optional[dict]:
    if not blueprint_payload:
        return None
    mapping_design = blueprint_payload.get("mapping_design") or {}
    entity_mappings = mapping_design.get("entity_mappings") or []
    for item in entity_mappings:
        if (item.get("entity_name") or "").strip().lower() == (entity.entity_name or "").strip().lower():
            return item
    return None


def _build_blueprint_mapping_context(
    blueprint_payload: Optional[dict],
    entity: SysOntologyEntity,
) -> Dict[str, Any]:
    entity_recommendation = _find_blueprint_entity_recommendation(blueprint_payload, entity)
    source_role_bindings = (blueprint_payload or {}).get("table_roles") or (blueprint_payload or {}).get("source_role_bindings") or []
    role_by_table = {
        (item.get("table_name") or "").strip().upper(): (item.get("source_role") or "").strip().lower()
        for item in source_role_bindings
        if item.get("table_name")
    }
    entity_candidates = (blueprint_payload or {}).get("entity_candidates") or []
    relation_candidates = (blueprint_payload or {}).get("relation_candidates") or []
    candidate_entity = next(
        (
            item for item in entity_candidates
            if (item.get("entityName") or "").strip().lower() == (entity.entity_name or "").strip().lower()
        ),
        None,
    )
    preferred_tables = [
        str(item).strip().upper()
        for item in (((entity_recommendation or {}).get("source_hints") or (candidate_entity or {}).get("sourceHints") or []))
        if str(item).strip()
    ]
    preferred_roles = [
        str(item).strip().lower()
        for item in (((entity_recommendation or {}).get("source_roles") or (candidate_entity or {}).get("sourceRoles") or []))
        if str(item).strip()
    ]
    return {
        "blueprint_id": (blueprint_payload or {}).get("blueprint_id"),
        "blueprint_version": (blueprint_payload or {}).get("blueprint_version"),
        "blueprint_status": (blueprint_payload or {}).get("blueprint_status"),
        "entity_recommendation": entity_recommendation or {},
        "entity_candidate": candidate_entity or {},
        "rule_summary": (blueprint_payload or {}).get("rule_summary") or {},
        "business_summary": (blueprint_payload or {}).get("business_summary") or {},
        "candidate_counts": {
            "entity_candidates": len(entity_candidates),
            "relation_candidates": len(relation_candidates),
        },
        "preferred_tables": preferred_tables,
        "preferred_roles": preferred_roles,
        "role_by_table": role_by_table,
    }


def _find_blueprint_relation_recommendation(
    blueprint_payload: Optional[dict],
    relation: SysOntologyRelation,
) -> Optional[dict]:
    if not blueprint_payload:
        return None
    mapping_design = blueprint_payload.get("mapping_design") or {}
    relation_mappings = mapping_design.get("relation_mappings") or []
    for item in relation_mappings:
        if (
            (item.get("relation_name") or "").strip() == (relation.relation_name or "").strip()
            and (item.get("source_entity_name") or "").strip().lower() == (relation.source_entity.entity_name if relation.source_entity else "").strip().lower()
            and (item.get("target_entity_name") or "").strip().lower() == (relation.target_entity.entity_name if relation.target_entity else "").strip().lower()
        ):
            return item
    relation_candidates = blueprint_payload.get("relation_candidates") or []
    for item in relation_candidates:
        if (
            (item.get("relationName") or "").strip() == (relation.relation_name or "").strip()
            and (item.get("sourceEntityName") or "").strip().lower() == (relation.source_entity.entity_name if relation.source_entity else "").strip().lower()
            and (item.get("targetEntityName") or "").strip().lower() == (relation.target_entity.entity_name if relation.target_entity else "").strip().lower()
        ):
            return {
                "relation_name": item.get("relationName"),
                "source_entity_name": item.get("sourceEntityName"),
                "target_entity_name": item.get("targetEntityName"),
                "evidence_tables": item.get("evidenceTables") or [],
                "source_table": item.get("sourceTable") or "",
                "target_table": item.get("targetTable") or "",
                "join_condition": item.get("joinCondition") or "",
                "edge_sql": item.get("edgeSql") or "",
            }
    return None


def _get_entity_primary_property_name(entity: Optional[SysOntologyEntity]) -> str:
    if not entity:
        return ""
    primary = next((prop.property_name for prop in (entity.properties or []) if prop.is_primary_key == "Y"), "")
    return (primary or "").strip().upper()


def _get_blueprint_table_columns(
    db: Session,
    blueprint_payload: Optional[dict],
    table_name: str,
    table_column_cache: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    if not blueprint_payload or not blueprint_payload.get("source_id") or not table_name:
        return []
    cache_key = (table_name or "").strip().upper()
    if table_column_cache is not None and cache_key in table_column_cache:
        return table_column_cache[cache_key]
    try:
        from app.services.source_data_service import SourceDataService
        detail = SourceDataService(db).get_remote_table_detail(
            source_id=blueprint_payload.get("source_id"),
            table_name=table_name,
            schema=blueprint_payload.get("schema"),
            sample_limit=1,
        )
        columns = [
            (item.get("column_name") or "").strip().upper()
            for item in (detail.get("columns") or [])
            if (item.get("column_name") or "").strip()
        ]
        if table_column_cache is not None:
            table_column_cache[cache_key] = columns
        return columns
    except Exception:
        if table_column_cache is not None:
            table_column_cache[cache_key] = []
        return []


def _pick_identifier_column(preferred_names: List[str], table_columns: List[str]) -> str:
    normalized_columns = set(table_columns or [])
    for name in preferred_names:
        candidate = (name or "").strip().upper()
        if candidate and candidate in normalized_columns:
            return candidate
    for fallback in ["VCM_ID", "MODULE_ID", "SENSOR_ID", "LENS_ID", "LOT", "BARCODE", "MODEL", "ID"]:
        if fallback in normalized_columns:
            return fallback
    for column_name in table_columns:
        if column_name.endswith("_ID"):
            return column_name
    return table_columns[0] if table_columns else ""


def _build_oracle_edge_sql(
    relation_id: str,
    source_table: str,
    target_table: str,
    source_key_column: str,
    target_key_column: str,
    join_condition: str = "",
) -> str:
    if not source_table or not target_table or not source_key_column or not target_key_column:
        return ""
    same_table = source_table.upper() == target_table.upper()
    if not same_table and not (join_condition or "").strip():
        return ""

    safe_relation_id = (relation_id or "EDGE").replace("'", "''")
    target_alias = "src" if same_table else "dst"
    source_key_expr = f"src.{source_key_column}"
    target_key_expr = f"{target_alias}.{target_key_column}"
    from_clause = f"FROM {source_table} src"
    if not same_table:
        from_clause += f"\nJOIN {target_table} dst ON {(join_condition or '').strip()}"

    return (
        "SELECT DISTINCT\n"
        f"       '{safe_relation_id}:' || TO_CHAR({source_key_expr}) || ':' || TO_CHAR({target_key_expr}) AS EDGE_ID,\n"
        f"       {source_key_expr} AS SOURCE_ID,\n"
        f"       {target_key_expr} AS TARGET_ID\n"
        f"{from_clause}\n"
        f"WHERE {source_key_expr} IS NOT NULL AND {target_key_expr} IS NOT NULL"
    )


def _annotate_oracle_vertex_mapping(
    entity: SysOntologyEntity,
    mappings: List[dict],
) -> tuple[List[dict], Dict[str, Any]]:
    primary_properties = [prop for prop in (entity.properties or []) if (prop.is_primary_key or "").upper() == "Y"]
    primary_ids = {prop.property_id for prop in primary_properties}
    primary_names = {(prop.property_name or "").strip().lower() for prop in primary_properties}

    selected_index: Optional[int] = None
    for index, item in enumerate(mappings):
        matched_id = (item.get("matchedPropertyId") or "").strip()
        property_name = (item.get("matchedPropertyName") or item.get("propertyName") or "").strip().lower()
        if (matched_id and matched_id in primary_ids) or (property_name and property_name in primary_names):
            selected_index = index
            break

    if selected_index is None:
        for index, item in enumerate(mappings):
            property_name = (item.get("propertyName") or "").strip().lower()
            if property_name == "id" or property_name.endswith("_id") or property_name.endswith("id"):
                selected_index = index
                break

    if selected_index is None and mappings:
        selected_index = 0

    annotated = []
    for index, item in enumerate(mappings):
        annotated.append({
            **item,
            "is_vertex_key": index == selected_index,
        })

    key_mapping = annotated[selected_index] if selected_index is not None else {}
    vertex_table = entity.table_name or (
        f"ONTO_NODE_{(entity.entity_name or entity.entity_id).upper()}_V"
        if (entity.build_type or "").upper() == "VIEW"
        else f"ONTO_NODE_{(entity.entity_name or entity.entity_id).upper()}"
    )
    vertex = {
        "entity_id": entity.entity_id,
        "entity_name": entity.entity_name,
        "entity_display_name": entity.entity_display_name,
        "vertex_table": vertex_table,
        "vertex_label": entity.entity_name,
        "build_type": entity.build_type or "TABLE",
        "key_property": key_mapping.get("propertyName") or key_mapping.get("matchedPropertyName") or "",
        "key_source_table": key_mapping.get("sourceTable") or "",
        "key_source_column": key_mapping.get("sourceColumn") or "",
        "key_data_type": key_mapping.get("sourceDataType") or "",
        "key_inferred": bool(key_mapping) and not bool(primary_properties),
        "property_count": len(annotated),
        "properties": [
            {
                "property_id": item.get("matchedPropertyId") or "",
                "property_name": item.get("propertyName") or item.get("matchedPropertyName") or "",
                "property_display_name": item.get("propertyDisplayName") or "",
                "data_type": item.get("sourceDataType") or "",
                "source_table": item.get("sourceTable") or "",
                "source_column": item.get("sourceColumn") or "",
                "mapping_type": item.get("mappingType") or "DIRECT",
                "formula": item.get("formula") or "",
                "is_vertex_key": bool(item.get("is_vertex_key")),
            }
            for item in annotated
        ],
        "oracle_graph_ready": bool(key_mapping.get("sourceTable") and key_mapping.get("sourceColumn")),
    }
    return annotated, vertex


def _merge_holistic_node_design(
    entity_result: Dict[str, Any],
    node_design: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not node_design or not (node_design.get("node_sql") or "").strip():
        return entity_result

    key_name = str(node_design.get("key_property_name") or "").strip().lower()
    mappings = list(entity_result.get("mappings") or [])
    matched_key = False
    if key_name:
        remapped = []
        for item in mappings:
            property_name = str(
                item.get("propertyName")
                or item.get("matchedPropertyName")
                or ""
            ).strip().lower()
            is_key = property_name == key_name
            matched_key = matched_key or is_key
            remapped.append({**item, "is_vertex_key": is_key})
        if matched_key:
            mappings = remapped

    vertex = {
        **(entity_result.get("oracle_vertex") or {}),
        "vertex_table": node_design.get("node_table_name"),
        "build_type": node_design.get("build_type") or "TABLE",
        "key_property": node_design.get("key_property_name")
        or (entity_result.get("oracle_vertex") or {}).get("key_property")
        or "",
        "key_output_column": node_design.get("key_output_column") or "",
        "source_tables": node_design.get("source_tables") or [],
        "node_sql": node_design.get("node_sql") or "",
        "design_reason": node_design.get("design_reason") or "",
        "oracle_graph_ready": bool(
            (node_design.get("node_sql") or "").strip()
            and (
                (node_design.get("key_property_name") or "").strip()
                or (entity_result.get("oracle_vertex") or {}).get("key_property")
            )
        ),
    }
    vertex["properties"] = [
        {
            **item,
            "is_vertex_key": (
                str(item.get("property_name") or "").strip().lower() == key_name
                if key_name
                else bool(item.get("is_vertex_key"))
            ),
        }
        for item in (vertex.get("properties") or [])
    ]
    return {
        **entity_result,
        "mappings": mappings,
        "node_mapping": node_design,
        "oracle_vertex": vertex,
    }


def _build_relation_mapping_draft(
    db: Session,
    relation: SysOntologyRelation,
    blueprint_payload: Optional[dict],
    table_column_cache: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    source_entity_context = _build_blueprint_mapping_context(blueprint_payload, relation.source_entity) if relation.source_entity else {}
    target_entity_context = _build_blueprint_mapping_context(blueprint_payload, relation.target_entity) if relation.target_entity else {}
    relation_recommendation = _find_blueprint_relation_recommendation(blueprint_payload, relation) or {}

    evidence_tables = [str(item).strip().upper() for item in (relation_recommendation.get("evidence_tables") or []) if str(item).strip()]
    source_preferred = [str(item).strip().upper() for item in (source_entity_context.get("preferred_tables") or []) if str(item).strip()]
    target_preferred = [str(item).strip().upper() for item in (target_entity_context.get("preferred_tables") or []) if str(item).strip()]

    source_table = (
        relation_recommendation.get("source_table")
        or relation_recommendation.get("sourceTable")
        or (source_preferred[0] if source_preferred else (evidence_tables[0] if evidence_tables else ""))
    )
    target_table = (
        relation_recommendation.get("target_table")
        or relation_recommendation.get("targetTable")
        or (target_preferred[0] if target_preferred else (evidence_tables[0] if evidence_tables else source_table))
    )
    source_table = str(source_table or "").strip().upper()
    target_table = str(target_table or "").strip().upper()

    source_columns = _get_blueprint_table_columns(db, blueprint_payload, source_table, table_column_cache)
    target_columns = _get_blueprint_table_columns(db, blueprint_payload, target_table, table_column_cache)
    source_id_candidates = [_get_entity_primary_property_name(relation.source_entity), "VCM_ID", "MODULE_ID", "SENSOR_ID", "LENS_ID", "BARCODE", "LOT"]
    target_id_candidates = [_get_entity_primary_property_name(relation.target_entity), "MODEL", "LOT", "VCM_ID", "MODULE_ID", "SENSOR_ID", "LENS_ID", "BARCODE"]
    source_id_col = _pick_identifier_column(source_id_candidates, source_columns)
    target_id_col = _pick_identifier_column(target_id_candidates, target_columns)

    draft_join_condition = str(
        relation_recommendation.get("join_condition")
        or relation_recommendation.get("joinCondition")
        or ""
    ).strip()
    draft_edge_sql = str(
        relation_recommendation.get("edge_sql")
        or relation_recommendation.get("edgeSql")
        or ""
    ).strip()
    if source_table and target_table and source_id_col and target_id_col:
        if source_table != target_table and not draft_join_condition:
            # Prefer shared business foreign keys before considering a node
            # identifier.  In particular, a BottleCode's PRODUCT_ID describes
            # its product relation while BOTTLE_ID only identifies the bottle.
            common_join_keys = [key for key in [
                "PRODUCT_ID", "BATCH_ID", "LINE_ID", "PACK_ID", "CASE_ID", "PALLET_ID",
                "VCM_ID", "MODULE_ID", "SENSOR_ID", "LENS_ID", "LOT", "BARCODE",
            ] if key in source_columns and key in target_columns]
            join_key = common_join_keys[0] if common_join_keys else (source_id_col if source_id_col == target_id_col and source_id_col in source_columns and source_id_col in target_columns else "")
            if join_key:
                draft_join_condition = f"src.{join_key} = dst.{join_key}"
        if not draft_edge_sql:
            draft_edge_sql = _build_oracle_edge_sql(
                relation_id=relation.relation_id,
                source_table=source_table,
                target_table=target_table,
                source_key_column=source_id_col,
                target_key_column=target_id_col,
                join_condition=draft_join_condition,
            )

    return {
        "source_table": source_table,
        "target_table": target_table,
        "join_condition": draft_join_condition,
        "edge_sql": draft_edge_sql,
        "blueprint_recommendation": {
            "relation_name": relation.relation_name,
            "evidence_tables": evidence_tables,
            "source_preferred_tables": source_preferred,
            "target_preferred_tables": target_preferred,
            "rule_summary": (blueprint_payload or {}).get("rule_summary") or {},
        },
    }


def _build_bulk_relation_mapping_result(
    db: Session,
    relation: SysOntologyRelation,
    blueprint_payload: Optional[dict],
    entity_results_by_id: Dict[str, dict],
    table_column_cache: Optional[Dict[str, List[str]]] = None,
    graph_relation_mapping: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # 关系设计阶段只描述本体图的连接方式。源数据表的 JOIN 及可执行的
    # edge_sql 属于后续 DDL 生成阶段，不应在数据映射操作中提前生成或落库。
    graph_relation_mapping = graph_relation_mapping or {}
    source_vertex = (entity_results_by_id.get(relation.source_entity_id) or {}).get("oracle_vertex") or {}
    target_vertex = (entity_results_by_id.get(relation.target_entity_id) or {}).get("oracle_vertex") or {}
    source_node_mapping = (entity_results_by_id.get(relation.source_entity_id) or {}).get("node_mapping") or {}
    target_node_mapping = (entity_results_by_id.get(relation.target_entity_id) or {}).get("node_mapping") or {}
    source_vertex_table = str(source_node_mapping.get("node_table_name") or source_vertex.get("vertex_table") or "").strip().upper()
    target_vertex_table = str(target_node_mapping.get("node_table_name") or target_vertex.get("vertex_table") or "").strip().upper()
    source_key_property = str(source_node_mapping.get("key_property_name") or source_vertex.get("key_property") or "").strip()
    target_key_property = str(target_node_mapping.get("key_property_name") or target_vertex.get("key_property") or "").strip()
    join_condition = str(graph_relation_mapping.get("join_condition") or "").strip()
    node_ready = bool(source_vertex_table and target_vertex_table and source_key_property and target_key_property)
    ready = bool(node_ready and join_condition)

    return {
        "relation_id": relation.relation_id,
        "relation_name": relation.relation_name,
        "relation_type": relation.relation_type,
        "relation_desc": relation.relation_desc,
        "edge_table_name": (
            graph_relation_mapping.get("edge_table_name")
            or relation.relation_table_name
            or f"ONTO_EDGE_{relation.relation_id.upper()}"
        ),
        "source_entity_id": relation.source_entity_id,
        "source_entity_name": relation.source_entity.entity_name if relation.source_entity else "",
        "source_entity_display_name": relation.source_entity.entity_display_name if relation.source_entity else "",
        "target_entity_id": relation.target_entity_id,
        "target_entity_name": relation.target_entity.entity_name if relation.target_entity else "",
        "target_entity_display_name": relation.target_entity.entity_display_name if relation.target_entity else "",
        "mapping_status": "DESIGNED" if ready else "PENDING",
        "status": "READY" if ready else "EMPTY",
        "diff_status": "ADDED",
        "oracle_edge": {
            "edge_key": "EDGE_ID",
            "source_key": "SOURCE_ID",
            "destination_key": "TARGET_ID",
            "source_vertex_table": source_vertex_table,
            "source_vertex_key_property": source_key_property,
            "target_vertex_table": target_vertex_table,
            "target_vertex_key_property": target_key_property,
            "oracle_graph_ready": node_ready,
        },
        "join_recommendation": {
            "join_condition": join_condition,
            "source_tables": graph_relation_mapping.get("source_tables") or [],
            "design_reason": graph_relation_mapping.get("design_reason") or "",
            "validated": bool(join_condition),
            "message": (
                "已确认业务 Join；完成 Join 后再投影两端节点主键为 SOURCE_ID / TARGET_ID。"
                if join_condition else
                "未找到满足“同名 ID 且至少一端为 PK”的可验证 Join，未推荐边关系。"
            ),
        },
    }


def _sort_table_catalog_with_blueprint(table_catalog: List[Dict[str, Any]], blueprint_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    preferred_tables = set(blueprint_context.get("preferred_tables") or [])
    role_by_table = blueprint_context.get("role_by_table") or {}
    preferred_roles = set(blueprint_context.get("preferred_roles") or [])

    def sort_key(item: Dict[str, Any]):
        table_name = (item.get("table_name") or "").strip().upper()
        table_role = role_by_table.get(table_name, "")
        return (
            0 if table_name in preferred_tables else 1,
            0 if table_role in preferred_roles and table_role else 1,
            table_name,
        )

    sorted_catalog = sorted(table_catalog, key=sort_key)
    return [
        {
            **item,
            "blueprint_preferred": (item.get("table_name") or "").strip().upper() in preferred_tables,
            "source_role": role_by_table.get((item.get("table_name") or "").strip().upper(), ""),
        }
        for item in sorted_catalog
    ]


def _refresh_entity_status(db: Session, entity_id: str):
    entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
    if not entity:
        return

    entity_mapping = db.query(SysEntityMapping).filter(
        SysEntityMapping.entity_id == entity_id
    ).first()
    has_view_sql = bool((entity_mapping.view_sql or "").strip()) if entity_mapping else False

    has_mapped_property = db.query(SysOntologyProperty).filter(
        SysOntologyProperty.entity_id == entity_id,
        SysOntologyProperty.source_mark == "MAPPED"
    ).first() is not None

    entity.status = "MAPPED" if has_mapped_property else "DRAFT"

    properties = db.query(SysOntologyProperty).filter(
        SysOntologyProperty.entity_id == entity_id
    ).all()
    all_properties_ready = bool(properties) and all((prop.source_mark or "").upper() == "MAPPED" for prop in properties)
    if entity_mapping:
        entity_mapping.mapping_status = "CONFIRMED" if (has_view_sql or all_properties_ready) else "PENDING"
        entity_mapping.mapped_at = datetime.utcnow()


def _is_property_mapping_ddl_ready(mapping: Optional[SysPropertyMapping]) -> bool:
    if not mapping:
        return False
    mapping_type = (mapping.mapping_type or "").strip().upper()
    has_source_binding = bool((mapping.source_table or "").strip() and (mapping.source_column or "").strip())
    if mapping_type == "DIRECT":
        return has_source_binding
    if mapping_type == "COMPUTED":
        return has_source_binding and bool((mapping.formula_expr or "").strip())
    return False


def _is_relation_mapping_ddl_ready(mapping: Optional[SysRelationMapping]) -> bool:
    if not mapping:
        return False
    # Edge tables are now generated from node tables, so an old edge_sql view
    # is neither required nor sufficient.  A physical relation must name both
    # source tables and provide an explicit join predicate.
    if (mapping.mapping_mode or "DIRECT").upper() == "RELATION_TABLE":
        return bool(
            (mapping.relation_table or "").strip()
            and (mapping.relation_source_column or "").strip()
            and (mapping.relation_target_column or "").strip()
        )
    return bool(
        (mapping.source_table or "").strip()
        and (mapping.target_table or "").strip()
        and (mapping.join_condition or "").strip()
    )


def _apply_mappings_for_entity(
    db: Session,
    entity: SysOntologyEntity,
    mappings_payload: List[dict],
    current_user: dict,
) -> int:
    property_name_index = {
        (prop.property_name or "").lower(): prop
        for prop in db.query(SysOntologyProperty).filter(SysOntologyProperty.entity_id == entity.entity_id).all()
    }

    max_order_num = (
        db.query(SysOntologyProperty)
        .filter(SysOntologyProperty.entity_id == entity.entity_id)
        .order_by(SysOntologyProperty.order_num.desc())
        .first()
    )
    next_order_num = (max_order_num.order_num + 1) if max_order_num else 1

    confirmed_count = 0
    for item in mappings_payload:
        action = item.get("action", "accept")
        mapping_id = item.get("mapping_id")
        property_id = item.get("property_id")

        mapping = None
        prop = None

        if mapping_id:
            mapping = db.query(SysPropertyMapping).filter(
                SysPropertyMapping.mapping_id == mapping_id
            ).first()
            if mapping:
                prop = db.query(SysOntologyProperty).filter(
                    SysOntologyProperty.property_id == mapping.property_id
                ).first()
        elif property_id:
            prop = db.query(SysOntologyProperty).filter(
                SysOntologyProperty.property_id == property_id
            ).first()
            if prop:
                mapping = db.query(SysPropertyMapping).filter(
                    SysPropertyMapping.property_id == prop.property_id
                ).first()

        if action == "reject":
            if mapping:
                mapping.mapping_status = "REJECTED"
                mapping.source_table = None
                mapping.source_column = None
            if prop:
                prop.source_mark = "PENDING"
            continue

        if not prop:
            property_name = (item.get("property_name") or "").strip()
            if not property_name:
                continue
            normalized_property_name = property_name.lower()
            prop = property_name_index.get(normalized_property_name)
            if not prop:
                prop = SysOntologyProperty(
                    property_id=generate_id("prop"),
                    entity_id=entity.entity_id,
                    property_name=property_name,
                    property_display_name=item.get("property_display_name"),
                    data_type=item.get("data_type") or item.get("source_data_type") or "VARCHAR2",
                    is_primary_key="N",
                    is_nullable="Y",
                    property_desc=item.get("property_desc"),
                    order_num=next_order_num,
                    source_mark="PENDING",
                )
                next_order_num += 1
                db.add(prop)
                db.flush()
                property_name_index[normalized_property_name] = prop

        if not mapping:
            mapping = db.query(SysPropertyMapping).filter(
                SysPropertyMapping.property_id == prop.property_id
            ).first()
        if not mapping:
            mapping = SysPropertyMapping(
                mapping_id=generate_id("pmap"),
                property_id=prop.property_id,
                mapping_type="DIRECT",
                mapping_status="PENDING",
            )
            db.add(mapping)

        mapping.source_table = item.get("source_table")
        mapping.source_column = item.get("source_column")
        mapping.mapping_type = (item.get("mapping_type") or "DIRECT").upper()
        mapping.formula_expr = item.get("formula_expr")
        mapping.formula_desc = item.get("formula_desc")
        mapping.confidence = (item.get("confidence") or "MEDIUM").upper()
        mapping.mapping_status = "CONFIRMED" if _is_property_mapping_ddl_ready(mapping) else "PENDING"
        mapping.mapped_by = current_user.get("username", "unknown")
        mapping.mapped_at = datetime.utcnow()

        if item.get("property_display_name"):
            prop.property_display_name = item.get("property_display_name")
        if item.get("property_desc"):
            prop.property_desc = item.get("property_desc")
        if item.get("data_type") or item.get("source_data_type"):
            prop.data_type = item.get("data_type") or item.get("source_data_type")
        prop.source_mark = "MAPPED" if _is_property_mapping_ddl_ready(mapping) else "PENDING"
        if prop.source_mark == "MAPPED":
            confirmed_count += 1

    entity_mapping = db.query(SysEntityMapping).filter(
        SysEntityMapping.entity_id == entity.entity_id
    ).first()
    if entity_mapping:
        entity_mapping.mapped_by = current_user.get("username", "unknown")
        entity_mapping.mapped_at = datetime.utcnow()
    else:
        db.add(SysEntityMapping(
            mapping_id=generate_id("emap"),
            entity_id=entity.entity_id,
            build_type=entity.build_type,
            mapping_status="PENDING",
            mapped_by=current_user.get("username", "unknown"),
            mapped_at=datetime.utcnow(),
        ))

    _refresh_entity_status(db, entity.entity_id)
    return confirmed_count


def _apply_mapping_for_relation(
    db: Session,
    relation: SysOntologyRelation,
    mapping_payload: dict,
    current_user: dict,
) -> int:
    mapping = db.query(SysRelationMapping).filter(
        SysRelationMapping.relation_id == relation.relation_id
    ).first()
    if not mapping:
        mapping = SysRelationMapping(
            mapping_id=generate_id("rmap"),
            relation_id=relation.relation_id,
        )
        db.add(mapping)

    mapping.source_table = mapping_payload.get("source_table") or None
    mapping.target_table = mapping_payload.get("target_table") or None
    mapping.join_condition = mapping_payload.get("join_condition") or None
    mapping.edge_sql = mapping_payload.get("edge_sql") or None
    mapping.mapping_mode = (mapping_payload.get("mapping_mode") or "DIRECT").upper()
    mapping.relation_table = mapping_payload.get("relation_table") or None
    mapping.relation_source_column = mapping_payload.get("relation_source_column") or None
    mapping.relation_target_column = mapping_payload.get("relation_target_column") or None
    mapping.edge_property_columns_json = mapping_payload.get("edge_property_columns_json") or None
    if mapping_payload.get("edge_table_name"):
        _set_relation_edge_table_name(db, relation, mapping_payload.get("edge_table_name"))
    mapping.mapping_status = "STALE" if _is_relation_mapping_ddl_ready(mapping) else "PENDING"
    mapping.mapped_by = current_user.get("username", "unknown")
    mapping.mapped_at = datetime.utcnow()
    return 1 if mapping.mapping_status == "STALE" else 0


def _apply_node_mapping_for_entity(
    db: Session,
    entity: SysOntologyEntity,
    node_mapping: Optional[Dict[str, Any]],
    current_user: dict,
) -> int:
    if not node_mapping or not (node_mapping.get("node_sql") or "").strip():
        return 0
    build_type = "VIEW" if str(node_mapping.get("build_type") or "").upper() == "VIEW" else "TABLE"
    entity.build_type = build_type
    entity.table_name = (
        f"ONTO_NODE_{entity.entity_name.upper()}_V"
        if build_type == "VIEW"
        else f"ONTO_NODE_{entity.entity_name.upper()}"
    )

    key_property_name = str(node_mapping.get("key_property_name") or "").strip().lower()
    if key_property_name:
        properties = db.query(SysOntologyProperty).filter(
            SysOntologyProperty.entity_id == entity.entity_id
        ).all()
        matched_key = next(
            (
                prop
                for prop in properties
                if (prop.property_name or "").strip().lower() == key_property_name
            ),
            None,
        )
        if matched_key:
            for prop in properties:
                prop.is_primary_key = "Y" if prop.property_id == matched_key.property_id else "N"

    mapping = db.query(SysEntityMapping).filter(
        SysEntityMapping.entity_id == entity.entity_id
    ).first()
    if not mapping:
        mapping = SysEntityMapping(
            mapping_id=generate_id("emap"),
            entity_id=entity.entity_id,
        )
        db.add(mapping)
    mapping.build_type = build_type
    mapping.view_sql = str(node_mapping.get("node_sql") or "").strip().rstrip(";")
    mapping.mapping_status = "CONFIRMED"
    mapping.mapped_by = current_user.get("username", "unknown")
    mapping.mapped_at = datetime.utcnow()
    _refresh_entity_status(db, entity.entity_id)
    return 1


def _get_existing_mappings_snapshot(db: Session, entity_id: str) -> List[dict]:
    rows = (
        db.query(SysOntologyProperty, SysPropertyMapping)
        .outerjoin(SysPropertyMapping, SysPropertyMapping.property_id == SysOntologyProperty.property_id)
        .filter(SysOntologyProperty.entity_id == entity_id)
        .order_by(SysOntologyProperty.order_num, SysOntologyProperty.created_at)
        .all()
    )
    return [
        {
            "property_id": prop.property_id,
            "property_name": prop.property_name,
            "property_display_name": prop.property_display_name,
            "property_desc": prop.property_desc,
            "data_type": prop.data_type,
            "source_table": mapping.source_table if mapping else "",
            "source_column": mapping.source_column if mapping else "",
            "mapping_type": mapping.mapping_type if mapping else "",
            "formula_expr": mapping.formula_expr if mapping else "",
            "formula_desc": mapping.formula_desc if mapping else "",
            "confidence": mapping.confidence if mapping else "",
            "mapping_status": mapping.mapping_status if mapping else "PENDING",
        }
        for prop, mapping in rows
    ]


def _build_mapping_diff_summary(suggestions: List[dict], existing_mappings: List[dict]) -> dict:
    existing_by_property = {
        (item.get("property_id") or item.get("property_name") or "").lower(): item
        for item in existing_mappings
        if item.get("property_id") or item.get("property_name")
    }
    added = 0
    changed = 0
    unchanged = 0
    suggestion_status = []
    for item in suggestions:
        property_key = ((item.get("matchedPropertyId") or "") or (item.get("propertyName") or "")).lower()
        existing = existing_by_property.get(property_key)
        if not existing:
            added += 1
            suggestion_status.append({**item, "diff_status": "ADDED"})
            continue
        same_mapping = (
            (existing.get("source_table") or "").upper() == (item.get("sourceTable") or "").upper() and
            (existing.get("source_column") or "").upper() == (item.get("sourceColumn") or "").upper() and
            (existing.get("mapping_type") or "").upper() == (item.get("mappingType") or "").upper() and
            (existing.get("formula_expr") or "") == (item.get("formula") or "")
        )
        if same_mapping:
            unchanged += 1
            suggestion_status.append({**item, "diff_status": "UNCHANGED"})
        else:
            changed += 1
            suggestion_status.append({**item, "diff_status": "CHANGED"})
    return {
        "added_count": added,
        "changed_count": changed,
        "unchanged_count": unchanged,
        "suggestions": suggestion_status,
    }


def _save_mapping_task(
    db: Session,
    domain_id: str,
    source_id: Optional[str],
    model_config_id: Optional[str],
    task_type: str,
    status: str,
    request_payload: dict,
    result_payload: dict,
    summary_payload: dict,
    current_user: dict,
):
    task = SysMappingTask(
        task_id=generate_id("mtask"),
        domain_id=domain_id,
        source_id=source_id,
        model_config_id=model_config_id,
        task_type=task_type,
        status=status,
        request_json=_safe_json_dumps(request_payload),
        result_json=_safe_json_dumps(result_payload),
        summary_json=_safe_json_dumps(summary_payload),
        created_by=current_user.get("username", "unknown"),
    )
    db.add(task)
    return task


def _update_mapping_task(
    db: Session,
    task_id: str,
    *,
    status: Optional[str] = None,
    result_payload: Optional[dict] = None,
    summary_payload: Optional[dict] = None,
):
    task = db.query(SysMappingTask).filter(SysMappingTask.task_id == task_id).first()
    if not task:
        return None
    if status is not None:
        task.status = status
    if result_payload is not None:
        task.result_json = _safe_json_dumps(result_payload)
    if summary_payload is not None:
        task.summary_json = _safe_json_dumps(summary_payload)
    task.updated_at = datetime.utcnow()
    return task


def _safe_json_dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=_json_default)


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _build_bulk_mapping_response_payload(
    domain: SysDomain,
    results: List[dict],
    applied_total: int,
    auto_apply: bool,
    blueprint_payload: Optional[dict] = None,
    relation_results: Optional[List[dict]] = None,
    applied_relation_total: int = 0,
    graph_mapping_design: Optional[Dict[str, Any]] = None,
    applied_node_total: int = 0,
) -> dict:
    relations = relation_results or []
    summary = {
        "entity_count": len(results),
        "processed_count": len(results),
        "ready_count": sum(1 for item in results if item["status"] in {"READY", "APPLIED"}),
        "empty_count": sum(1 for item in results if item["status"] == "EMPTY"),
        "failed_count": sum(1 for item in results if item["status"] == "FAILED"),
        "applied_total": applied_total,
        "relation_count": len(relations),
        "relation_ready_count": sum(1 for item in relations if item.get("status") in {"READY", "APPLIED"}),
        "relation_missing_count": sum(1 for item in relations if item.get("status") == "EMPTY"),
        "applied_relation_count": applied_relation_total,
        "node_sql_ready_count": sum(1 for item in results if (item.get("node_mapping") or {}).get("node_sql")),
        "applied_node_count": applied_node_total,
        "running": False,
        "current_entity_name": "",
        "blueprint_version": (blueprint_payload or {}).get("blueprint_version"),
    }
    return {
        "domain": {
            "domain_id": domain.domain_id,
            "domain_name": domain.domain_name,
            "domain_desc": domain.domain_desc,
        },
        "summary": summary,
        "entities": results,
        "relations": relations,
        "graph_mapping_design": graph_mapping_design or {},
        "auto_apply": auto_apply,
        "blueprint_id": (blueprint_payload or {}).get("blueprint_id"),
        "blueprint_version": (blueprint_payload or {}).get("blueprint_version"),
        "blueprint_status": (blueprint_payload or {}).get("blueprint_status"),
    }


def _build_bulk_mapping_progress_payload(
    domain: SysDomain,
    results: List[dict],
    applied_total: int,
    auto_apply: bool,
    total_entities: int,
    current_entity_name: str,
    blueprint_payload: Optional[dict] = None,
    relation_results: Optional[List[dict]] = None,
    graph_mapping_design: Optional[Dict[str, Any]] = None,
) -> dict:
    relations = relation_results or []
    summary = {
        "entity_count": total_entities,
        "processed_count": len(results),
        "ready_count": sum(1 for item in results if item["status"] in {"READY", "APPLIED"}),
        "empty_count": sum(1 for item in results if item["status"] == "EMPTY"),
        "failed_count": sum(1 for item in results if item["status"] == "FAILED"),
        "applied_total": applied_total,
        "relation_count": len(relations),
        "relation_ready_count": sum(1 for item in relations if item.get("status") in {"READY", "APPLIED"}),
        "relation_missing_count": sum(1 for item in relations if item.get("status") == "EMPTY"),
        "node_sql_ready_count": sum(1 for item in results if (item.get("node_mapping") or {}).get("node_sql")),
        "running": len(results) < total_entities,
        "current_entity_name": current_entity_name,
        "blueprint_version": (blueprint_payload or {}).get("blueprint_version"),
    }
    return {
        "domain": {
            "domain_id": domain.domain_id,
            "domain_name": domain.domain_name,
            "domain_desc": domain.domain_desc,
        },
        "summary": summary,
        "entities": results,
        "relations": relations,
        "graph_mapping_design": graph_mapping_design or {},
        "auto_apply": auto_apply,
        "blueprint_id": (blueprint_payload or {}).get("blueprint_id"),
        "blueprint_version": (blueprint_payload or {}).get("blueprint_version"),
        "blueprint_status": (blueprint_payload or {}).get("blueprint_status"),
    }


async def _run_bulk_auto_mapping_job_async(
    task_id: str,
    domain_id: str,
    request_payload: dict,
    current_user_payload: dict,
):
    db = SessionLocal()
    try:
        from app.services.llm_service import LLMService
        from app.services.source_data_service import SourceDataService

        domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
        if not domain:
            raise ValueError("业务分析域不存在")

        entities = db.query(SysOntologyEntity).filter(
            SysOntologyEntity.domain_id == domain_id
        ).order_by(SysOntologyEntity.created_at).all()
        if not entities:
            raise ValueError("当前分析域下没有可映射的本体对象")

        source_service = SourceDataService(db)
        llm_service = LLMService(db)
        results = []
        applied_total = 0
        applied_node_total = 0
        total_entities = len(entities)
        holistic_source_tables: Dict[str, Dict[str, Any]] = {}
        blueprint_payload = _load_blueprint_payload_by_id(db, request_payload.get("blueprint_id"))
        if not blueprint_payload:
            blueprint_payload = _load_latest_blueprint_payload(db, domain.domain_id)

        for entity in entities:
            blueprint_context = _build_blueprint_mapping_context(blueprint_payload, entity)
            properties = db.query(SysOntologyProperty).filter(
                SysOntologyProperty.entity_id == entity.entity_id
            ).all()
            property_keywords = []
            for prop in properties:
                property_keywords.extend([
                    prop.property_name or "",
                    prop.property_display_name or "",
                    prop.property_desc or "",
                ])
            try:
                entity_keywords = source_service.build_entity_keywords(
                    entity.entity_name,
                    entity.entity_display_name,
                    entity.entity_desc,
                    property_keywords=property_keywords,
                )
                raw_table_catalog = source_service.get_remote_table_catalog_for_mapping(
                    source_id=request_payload.get("source_id"),
                    domain_id=domain_id,
                    schema=request_payload.get("schema"),
                    entity_keywords=entity_keywords,
                )
                table_catalog = {
                    **raw_table_catalog,
                    "tables": _sort_table_catalog_with_blueprint(raw_table_catalog.get("tables", []) or [], blueprint_context),
                }
                table_selection = await llm_service.select_relevant_tables_for_mapping(
                    entity,
                    properties,
                    table_catalog.get("tables", []),
                    domain_context={
                        "domain_id": domain.domain_id,
                        "domain_name": domain.domain_name,
                        "domain_desc": domain.domain_desc,
                    },
                    blueprint_context=blueprint_context,
                    mapping_instruction=request_payload.get("mapping_instruction"),
                    config_id=request_payload.get("model_config_id"),
                )
                selected_name_map = {
                    item.get("table_name", "").upper(): item
                    for item in (table_catalog.get("tables", []) or [])
                    if item.get("table_name")
                }
                selected_tables = [
                    {**selected_name_map[item["table_name"].upper()], "selection_reason": item.get("reason", "")}
                    for item in table_selection.get("selected_tables", [])
                    if item.get("table_name", "").upper() in selected_name_map
                ]
                source_context = source_service.get_remote_tables_metadata_by_names(
                    source_id=request_payload.get("source_id"),
                    schema=table_catalog.get("schema"),
                    tables=selected_tables,
                    sample_limit=request_payload.get("sample_limit", 3),
                    entity_keywords=entity_keywords,
                    source_name=table_catalog.get("source_name"),
                )
                for source_table in source_context.get("tables", []) or []:
                    table_key = str(source_table.get("table_name") or "").strip().upper()
                    if table_key and table_key not in holistic_source_tables:
                        holistic_source_tables[table_key] = source_table
                llm_result = await llm_service.auto_mapping(
                    entity,
                    properties,
                    source_context.get("tables", []),
                    domain_context={
                        "domain_id": domain.domain_id,
                        "domain_name": domain.domain_name,
                        "domain_desc": domain.domain_desc,
                    },
                    source_context={
                        "source_id": source_context.get("source_id"),
                        "source_name": source_context.get("source_name"),
                        "schema": source_context.get("schema"),
                    },
                    blueprint_context=blueprint_context,
                    mapping_instruction=request_payload.get("mapping_instruction"),
                    config_id=request_payload.get("model_config_id"),
                )
            except Exception as exc:
                _failed_mappings, failed_vertex = _annotate_oracle_vertex_mapping(entity, [])
                results.append({
                    "entity_id": entity.entity_id,
                    "entity_name": entity.entity_name,
                    "entity_display_name": entity.entity_display_name,
                    "entity_desc": entity.entity_desc,
                    "status": "FAILED",
                    "error_message": str(exc),
                    "mappings": [],
                    "candidate_tables": [],
                    "mapping_count": 0,
                    "oracle_vertex": failed_vertex,
                })
                progress_payload = _build_bulk_mapping_progress_payload(
                    domain=domain,
                    results=results,
                    applied_total=applied_total,
                    auto_apply=bool(request_payload.get("auto_apply")),
                    total_entities=total_entities,
                    current_entity_name=entity.entity_display_name or entity.entity_name,
                    blueprint_payload=blueprint_payload,
                )
                _update_mapping_task(
                    db,
                    task_id,
                    status="IN_PROGRESS",
                    result_payload=progress_payload,
                    summary_payload=progress_payload["summary"],
                )
                db.commit()
                continue

            mappings = llm_result.get("mappings", []) or []
            existing_mappings = _get_existing_mappings_snapshot(db, entity.entity_id)
            diff_summary = _build_mapping_diff_summary(mappings, existing_mappings)
            mappings = diff_summary.pop("suggestions", mappings)
            mappings, oracle_vertex = _annotate_oracle_vertex_mapping(entity, mappings)
            entity_result = {
                "entity_id": entity.entity_id,
                "entity_name": entity.entity_name,
                "entity_display_name": entity.entity_display_name,
                "entity_desc": entity.entity_desc,
                "status": "READY" if mappings else "EMPTY",
                "error_message": "",
                "mappings": mappings,
                "candidate_tables": llm_result.get("candidate_tables", []),
                "llm_raw_output": llm_result.get("llm_raw_output", ""),
                "mapping_count": llm_result.get("mapping_count", len(mappings)),
                "generation_mode": llm_result.get("generation_mode", ""),
                "table_selection": table_selection,
                "existing_mappings": existing_mappings,
                "diff_summary": diff_summary,
                "oracle_vertex": oracle_vertex,
                "blueprint_context": {
                    "blueprint_id": blueprint_context.get("blueprint_id"),
                    "blueprint_version": blueprint_context.get("blueprint_version"),
                    "preferred_tables": blueprint_context.get("preferred_tables") or [],
                    "preferred_roles": blueprint_context.get("preferred_roles") or [],
                    "recommended_build_mode": (blueprint_context.get("entity_recommendation") or {}).get("recommended_build_mode") or "",
                },
            }

            if request_payload.get("auto_apply") and mappings:
                applied_count = _apply_mappings_for_entity(db, entity, [
                    {**item, "action": "accept"} for item in mappings
                ], current_user_payload)
                applied_total += applied_count
                entity_result["status"] = "APPLIED"
                entity_result["applied_count"] = applied_count

            results.append(entity_result)
            if request_payload.get("auto_apply"):
                db.commit()

            progress_payload = _build_bulk_mapping_progress_payload(
                domain=domain,
                results=results,
                applied_total=applied_total,
                auto_apply=bool(request_payload.get("auto_apply")),
                total_entities=total_entities,
                current_entity_name=entity.entity_display_name or entity.entity_name,
                blueprint_payload=blueprint_payload,
            )
            _update_mapping_task(
                db,
                task_id,
                status="IN_PROGRESS",
                result_payload=progress_payload,
                summary_payload=progress_payload["summary"],
            )
            db.commit()

        relations = db.query(SysOntologyRelation).filter(
            SysOntologyRelation.domain_id == domain_id
        ).order_by(SysOntologyRelation.created_at).all()
        ontology_entity_context = [
            {
                "entity_id": entity.entity_id,
                "entity_name": entity.entity_name,
                "entity_display_name": entity.entity_display_name,
                "entity_desc": entity.entity_desc,
                "properties": [
                    {
                        "property_id": prop.property_id,
                        "property_name": prop.property_name,
                        "property_display_name": prop.property_display_name,
                        "data_type": prop.data_type,
                        "is_primary_key": (prop.is_primary_key or "").upper() == "Y",
                        "property_desc": prop.property_desc,
                    }
                    for prop in (entity.properties or [])
                ],
            }
            for entity in entities
        ]
        ontology_relation_context = [
            {
                "relation_id": relation.relation_id,
                "relation_name": relation.relation_name,
                "relation_type": relation.relation_type,
                "relation_desc": relation.relation_desc,
                "source_entity_id": relation.source_entity_id,
                "source_entity_name": relation.source_entity.entity_name if relation.source_entity else "",
                "target_entity_id": relation.target_entity_id,
                "target_entity_name": relation.target_entity.entity_name if relation.target_entity else "",
            }
            for relation in relations
        ]
        graph_mapping_design: Dict[str, Any] = {}
        graph_design_progress = _build_bulk_mapping_progress_payload(
            domain=domain,
            results=results,
            applied_total=applied_total,
            auto_apply=bool(request_payload.get("auto_apply")),
            total_entities=total_entities,
            current_entity_name="整体本体节点与关系设计",
            blueprint_payload=blueprint_payload,
        )
        _update_mapping_task(
            db,
            task_id,
            status="IN_PROGRESS",
            result_payload=graph_design_progress,
            summary_payload=graph_design_progress["summary"],
        )
        db.commit()
        try:
            graph_mapping_design = await llm_service.design_ontology_property_graph_mapping(
                domain_context={
                    "domain_id": domain.domain_id,
                    "domain_name": domain.domain_name,
                    "domain_desc": domain.domain_desc,
                },
                ontology_entities=ontology_entity_context,
                ontology_relations=ontology_relation_context,
                source_tables=list(holistic_source_tables.values()),
                entity_mapping_results=results,
                blueprint_context=blueprint_payload,
                mapping_instruction=request_payload.get("mapping_instruction"),
                config_id=request_payload.get("model_config_id"),
            )
        except Exception as exc:
            logger.exception(
                "Holistic ontology graph mapping design failed; continue with deterministic fallback: task_id=%s error=%s",
                task_id,
                str(exc),
            )
            graph_mapping_design = {
                "entity_mappings": [],
                "relation_mappings": [],
                "error_message": str(exc),
            }

        node_design_by_entity = {
            item.get("entity_id"): item
            for item in (graph_mapping_design.get("entity_mappings") or [])
            if item.get("entity_id")
        }
        results = [
            _merge_holistic_node_design(
                item,
                node_design_by_entity.get(item.get("entity_id")),
            )
            for item in results
        ]
        if request_payload.get("auto_apply"):
            entity_by_id = {entity.entity_id: entity for entity in entities}
            for entity_result in results:
                node_design = entity_result.get("node_mapping")
                entity = entity_by_id.get(entity_result.get("entity_id"))
                if entity and node_design:
                    applied_node_count = _apply_node_mapping_for_entity(
                        db,
                        entity,
                        node_design,
                        current_user_payload,
                    )
                    applied_node_total += applied_node_count
                    if applied_node_count:
                        entity_result["status"] = "APPLIED"
            db.commit()
        applied_total += applied_node_total

        relation_design_by_id = {
            item.get("relation_id"): item
            for item in (graph_mapping_design.get("relation_mappings") or [])
            if item.get("relation_id")
        }
        relation_results: List[dict] = []
        applied_relation_total = 0
        entity_results_by_id = {
            item.get("entity_id"): item
            for item in results
            if item.get("entity_id")
        }
        table_column_cache: Dict[str, List[str]] = {}
        for relation in relations:
            relation_result = _build_bulk_relation_mapping_result(
                db=db,
                relation=relation,
                blueprint_payload=blueprint_payload,
                entity_results_by_id=entity_results_by_id,
                table_column_cache=table_column_cache,
                graph_relation_mapping=relation_design_by_id.get(relation.relation_id),
            )
            relation_results.append(relation_result)
        if request_payload.get("auto_apply"):
            db.commit()
        # 关系在此阶段只完成节点 PK 引用设计，DDL 阶段才生成和保存边关系 SQL。

        final_payload = _build_bulk_mapping_response_payload(
            domain=domain,
            results=results,
            applied_total=applied_total,
            auto_apply=bool(request_payload.get("auto_apply")),
            blueprint_payload=blueprint_payload,
            relation_results=relation_results,
            applied_relation_total=applied_relation_total,
            graph_mapping_design=graph_mapping_design,
            applied_node_total=applied_node_total,
        )
        summary = final_payload["summary"]
        final_status = "SUCCESS" if (
            summary["failed_count"] == 0
            and summary["node_sql_ready_count"] == summary["entity_count"]
            and summary["relation_missing_count"] == 0
        ) else "PARTIAL"
        _update_mapping_task(
            db,
            task_id,
            status=final_status,
            result_payload=final_payload,
            summary_payload=final_payload["summary"],
        )
        db.commit()
        logger.info("Background bulk auto mapping completed: task_id=%s status=%s summary=%s", task_id, final_status, final_payload["summary"])
    except Exception as exc:
        logger.exception("Background bulk auto mapping failed: task_id=%s error=%s", task_id, str(exc))
        try:
            failure_summary = {
                "entity_count": 0,
                "processed_count": 0,
                "ready_count": 0,
                "empty_count": 0,
                "failed_count": 1,
                "applied_total": 0,
                "relation_count": 0,
                "relation_ready_count": 0,
                "relation_missing_count": 0,
                "applied_relation_count": 0,
                "node_sql_ready_count": 0,
                "applied_node_count": 0,
                "running": False,
                "current_entity_name": "",
                "error_message": str(exc),
                "blueprint_version": request_payload.get("blueprint_version"),
            }
            _update_mapping_task(
                db,
                task_id,
                status="FAILED",
                result_payload={
                    "summary": failure_summary,
                    "entities": [],
                    "relations": [],
                    "graph_mapping_design": {},
                    "error_message": str(exc),
                    "blueprint_id": request_payload.get("blueprint_id"),
                    "blueprint_version": request_payload.get("blueprint_version"),
                    "blueprint_status": request_payload.get("blueprint_status"),
                },
                summary_payload=failure_summary,
            )
            db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


def _run_bulk_auto_mapping_job(task_id: str, domain_id: str, request_payload: dict, current_user_payload: dict):
    asyncio.run(_run_bulk_auto_mapping_job_async(task_id, domain_id, request_payload, current_user_payload))


# ====== 实体映射 ======

@router.get("/domains/{domain_id}/blueprint/latest", response_model=ApiResponse)
async def get_latest_blueprint(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="业务分析域不存在")

    payload = _load_latest_blueprint_payload(db, domain_id)
    if not payload:
        return ApiResponse(data=None)

    return ApiResponse(data={
        "blueprint_id": payload.get("blueprint_id"),
        "blueprint_version": payload.get("blueprint_version"),
        "blueprint_status": payload.get("blueprint_status"),
        "generation_strategy": payload.get("generation_strategy"),
        "business_scenario": payload.get("business_scenario"),
        "source_id": payload.get("source_id"),
        "schema": payload.get("schema"),
        "selected_tables": payload.get("selected_tables") or [],
        "business_document_parsed": payload.get("business_document_parsed") or {},
        "document_facts": payload.get("document_facts") or {},
        "rule_analysis": payload.get("rule_analysis") or {},
        "schema_analysis": payload.get("schema_analysis") or {},
        "focus_scope": payload.get("focus_scope") or {},
        "metric_semantics": payload.get("metric_semantics") or {},
        "selected_table_schema": payload.get("selected_table_schema") or {},
        "rule_summary": payload.get("rule_summary") or {},
        "spec_limit_summary": payload.get("spec_limit_summary") or {},
        "business_summary": payload.get("business_summary") or {},
        "ontology_design_document": payload.get("ontology_design_document") or {},
        "table_roles": payload.get("table_roles") or [],
        "entity_candidates": payload.get("entity_candidates") or [],
        "relation_candidates": payload.get("relation_candidates") or [],
        "entities": payload.get("entities") or [],
        "relations": payload.get("relations") or [],
        "generation_mode": payload.get("generation_mode"),
        "model": payload.get("model"),
        "ontology_generation_context": payload.get("ontology_generation_context") or {},
        "llm_context_summary": payload.get("llm_context_summary") or {},
        "llm_enrichment": payload.get("llm_enrichment") or {},
        "source_role_bindings": payload.get("source_role_bindings") or [],
        "semantic_patterns": payload.get("semantic_patterns") or [],
        "canonical_model": payload.get("canonical_model") or {},
        "view_plan": payload.get("view_plan") or {},
        "mapping_design": payload.get("mapping_design") or {},
        "deployment_design": payload.get("deployment_design") or {},
    })

@router.get("/entities/{entity_id}/entity-mapping", response_model=ApiResponse)
async def get_entity_mapping(
    entity_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    mapping = db.query(SysEntityMapping).filter(
        SysEntityMapping.entity_id == entity_id
    ).first()
    if not mapping:
        # Create default
        entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
        if not entity:
            raise HTTPException(status_code=404, detail="实体不存在")
        mapping = SysEntityMapping(
            mapping_id=generate_id("emap"),
            entity_id=entity_id,
            build_type=entity.build_type,
            mapping_status="PENDING"
        )
        db.add(mapping)
        db.commit()
        db.refresh(mapping)
    entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
    blueprint_payload = _load_latest_blueprint_payload(db, entity.domain_id) if entity else None
    blueprint_recommendation = _find_blueprint_entity_recommendation(blueprint_payload, entity) if entity else None
    blueprint_context = _build_blueprint_mapping_context(blueprint_payload, entity) if entity else {}

    return ApiResponse(data={
        "mapping_id": mapping.mapping_id,
        "entity_id": mapping.entity_id,
        "build_type": mapping.build_type,
        "view_sql": mapping.view_sql,
        "mapping_status": mapping.mapping_status,
        "mapped_by": mapping.mapped_by,
        "mapped_at": mapping.mapped_at.isoformat() if mapping.mapped_at else None,
        "blueprint_recommendation": blueprint_recommendation,
        "blueprint_context": blueprint_context,
        "blueprint_id": blueprint_payload.get("blueprint_id") if blueprint_payload else None,
        "blueprint_version": blueprint_payload.get("blueprint_version") if blueprint_payload else None,
    })


@router.put("/entities/{entity_id}/entity-mapping", response_model=ApiResponse)
async def update_entity_mapping(
    entity_id: str,
    req: EntityMappingUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    payload = req.model_dump(exclude_unset=True)
    mapping = db.query(SysEntityMapping).filter(SysEntityMapping.entity_id == entity_id).first()
    if not mapping:
        mapping = SysEntityMapping(
            mapping_id=generate_id("emap"),
            entity_id=entity_id,
            build_type=req.build_type,
            mapping_status=req.mapping_status or "PENDING"
        )
        db.add(mapping)
    else:
        mapping.build_type = req.build_type
        if "view_sql" in payload:
            mapping.view_sql = req.view_sql
        if req.mapping_status:
            mapping.mapping_status = req.mapping_status

    mapping.mapped_by = current_user.get("username", "unknown")
    mapping.mapped_at = datetime.utcnow()
    db.commit()

    # Also update entity build_type
    entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
    if entity:
        entity.build_type = req.build_type
        # Update table name based on build type
        if req.build_type == "VIEW":
            entity.table_name = f"ONTO_NODE_{entity.entity_name.upper()}_V"
        else:
            entity.table_name = f"ONTO_NODE_{entity.entity_name.upper()}"
        _refresh_entity_status(db, entity_id)
        db.commit()

    return ApiResponse(message="实体映射已更新")


# ====== 属性映射 ======

@router.get("/entities/{entity_id}/mappings", response_model=ApiResponse)
async def get_property_mappings(
    entity_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    mappings = (
        db.query(SysPropertyMapping, SysOntologyProperty.entity_id)
        .join(SysOntologyProperty, SysOntologyProperty.property_id == SysPropertyMapping.property_id)
        .filter(SysOntologyProperty.entity_id == entity_id)
        .all()
    )

    data = [PropertyMappingResponse(
        mapping_id=mapping.mapping_id,
        property_id=mapping.property_id,
        entity_id=mapped_entity_id,
        source_table=mapping.source_table,
        source_column=mapping.source_column,
        mapping_type=mapping.mapping_type,
        formula_expr=mapping.formula_expr,
        formula_desc=mapping.formula_desc,
        confidence=mapping.confidence,
        mapping_status=mapping.mapping_status,
        mapped_by=mapping.mapped_by,
        mapped_at=mapping.mapped_at.isoformat() if mapping.mapped_at else None
    ).model_dump() for mapping, mapped_entity_id in mappings]

    return ApiResponse(data=data)


@router.put("/properties/{property_id}/mapping", response_model=ApiResponse)
async def update_property_mapping(
    property_id: str,
    req: PropertyMappingUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    prop = db.query(SysOntologyProperty).filter(SysOntologyProperty.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="属性不存在")

    mapping = db.query(SysPropertyMapping).filter(
        SysPropertyMapping.property_id == property_id
    ).first()

    if not mapping:
        mapping = SysPropertyMapping(
            mapping_id=generate_id("pmap"),
            property_id=property_id,
            mapping_type=req.mapping_type or "DIRECT",
            mapping_status=req.mapping_status or "PENDING"
        )
        db.add(mapping)

    for field, value in req.model_dump(exclude_unset=True).items():
        setattr(mapping, field, value)
    mapping.mapped_by = current_user.get("username", "unknown")
    mapping.mapped_at = datetime.utcnow()
    if mapping.mapping_status != "REJECTED":
        mapping.mapping_status = "CONFIRMED" if _is_property_mapping_ddl_ready(mapping) else "PENDING"
    prop.source_mark = "MAPPED" if _is_property_mapping_ddl_ready(mapping) else "PENDING"
    _refresh_entity_status(db, prop.entity_id)
    db.commit()

    return ApiResponse(message="属性映射已更新")


# ====== 关系映射 ======

def _relation_entity_source_table(entity: SysOntologyEntity) -> str:
    """Choose the dominant mapped table for a node entity."""
    table_counts: Dict[str, int] = {}
    for prop in entity.properties or []:
        mapping = getattr(prop, "mapping", None)
        table_name = (getattr(mapping, "source_table", None) or "").strip().upper()
        if table_name:
            table_counts[table_name] = table_counts.get(table_name, 0) + 1
    return max(table_counts, key=table_counts.get) if table_counts else ""


def _relation_join_candidates(
    relation: SysOntologyRelation,
    source_table: str,
    target_table: str,
    source_columns: set[str],
    target_columns: set[str],
    current_join: str = "",
) -> List[Dict[str, Any]]:
    """Produce explainable candidates from ontology mappings and field semantics.

    This is deliberately conservative: matching property/source-column names is
    evidence; two unrelated primary keys are not.
    """
    candidates: Dict[tuple[str, str], Dict[str, Any]] = {}

    def add(source_column: str, target_column: str, score: int, reason: str, kind: str):
        src, dst = source_column.upper(), target_column.upper()
        if src not in source_columns or dst not in target_columns:
            return
        key = (src, dst)
        current = candidates.get(key)
        if current and current["semantic_score"] >= score:
            return
        candidates[key] = {
            "source_column": src,
            "target_column": dst,
            "join_condition": f"src.{src} = dst.{dst}",
            "semantic_score": score,
            "reason": reason,
            "candidate_type": kind,
        }

    # Retain a user-entered simple equality as a candidate so it is validated
    # against real data rather than trusted blindly.
    match = re.fullmatch(r"\s*(?:src\.)?([A-Za-z][A-Za-z0-9_$#]*)\s*=\s*(?:dst\.)?([A-Za-z][A-Za-z0-9_$#]*)\s*", current_join or "")
    if match:
        add(match.group(1), match.group(2), 120, "当前已填写的 Join，待以真实数据复核", "CURRENT")

    # Structured supply-chain identity relation.  This mirrors the DDL layer,
    # but exposes the decision early in the mapping workflow.
    pair = ((relation.source_entity.entity_name or "").upper(), (relation.target_entity.entity_name or "").upper())
    if pair == ("BOTTLECODE", "PRODUCT"):
        add("PRODUCT_ID", "PRODUCT_ID", 115, "瓶码的 PRODUCT_ID 是指向产品的业务外键", "DOMAIN_RULE")

    source_props = { (prop.property_name or "").strip().upper(): prop for prop in (relation.source_entity.properties or []) }
    target_props = { (prop.property_name or "").strip().upper(): prop for prop in (relation.target_entity.properties or []) }
    for property_name in set(source_props).intersection(target_props):
        source_prop, target_prop = source_props[property_name], target_props[property_name]
        source_mapping, target_mapping = getattr(source_prop, "mapping", None), getattr(target_prop, "mapping", None)
        source_column = (getattr(source_mapping, "source_column", None) or property_name).strip().upper()
        target_column = (getattr(target_mapping, "source_column", None) or property_name).strip().upper()
        if property_name.endswith(("_ID", "_CODE", "_NO")):
            add(source_column, target_column, 100, f"两端本体属性“{property_name}”语义一致，且属于业务标识字段", "ONTOLOGY_PROPERTY")
        else:
            add(source_column, target_column, 70, f"两端本体属性“{property_name}”名称一致", "SAME_NAME")

    # Last-resort table recognition: only identical foreign-key-like columns,
    # never SOURCE_PK = TARGET_PK with different names.
    for column in source_columns.intersection(target_columns):
        if column.endswith(("_ID", "_CODE", "_NO")):
            add(column, column, 60, f"源表与目标表均存在业务标识字段“{column}”", "TABLE_SCHEMA")

    return sorted(candidates.values(), key=lambda item: item["semantic_score"], reverse=True)


@router.post("/relations/{relation_id}/join-analysis", response_model=ApiResponse)
async def analyze_relation_join(
    relation_id: str,
    req: RelationJoinAnalyzeRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Recommend and verify Join pairs before a relation mapping is saved."""
    from app.services.source_data_service import SourceDataService

    relation = db.query(SysOntologyRelation).filter(SysOntologyRelation.relation_id == relation_id).first()
    if not relation or not relation.source_entity or not relation.target_entity:
        raise HTTPException(status_code=404, detail="关系或两端本体对象不存在")

    source_table = (req.source_table or _relation_entity_source_table(relation.source_entity)).strip().upper()
    target_table = (req.target_table or _relation_entity_source_table(relation.target_entity)).strip().upper()
    if not source_table or not target_table:
        raise HTTPException(status_code=400, detail="请先选择源节点和目标节点的来源表，再识别关联条件")

    service = SourceDataService(db)
    try:
        source_detail = service.get_remote_table_detail(req.source_id, source_table, req.schema, sample_limit=1)
        target_detail = service.get_remote_table_detail(req.source_id, target_table, req.schema, sample_limit=1)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法读取关联表结构：{exc}")
    source_columns = {(item.get("column_name") or "").upper() for item in (source_detail.get("columns") or [])}
    target_columns = {(item.get("column_name") or "").upper() for item in (target_detail.get("columns") or [])}
    candidates = _relation_join_candidates(
        relation, source_table, target_table, source_columns, target_columns, req.join_condition or ""
    )[:req.max_candidates]
    for candidate in candidates:
        try:
            profile = service.profile_remote_join(
                req.source_id, source_table, candidate["source_column"], target_table, candidate["target_column"], req.schema
            )
            candidate["verification"] = profile
            candidate["status"] = "VERIFIED" if profile["valid"] else "NO_MATCH"
            candidate["confidence"] = "HIGH" if profile["valid"] and candidate["semantic_score"] >= 90 else ("MEDIUM" if profile["valid"] else "LOW")
        except Exception as exc:
            candidate["verification"] = {"valid": False, "error": str(exc)}
            candidate["status"] = "UNVERIFIED"
            candidate["confidence"] = "LOW"

    candidates.sort(key=lambda item: (item["status"] == "VERIFIED", item["semantic_score"]), reverse=True)
    return ApiResponse(data={
        "relation_id": relation_id,
        "source_table": source_table,
        "target_table": target_table,
        "candidates": candidates,
        "analysis_hint": "候选由已映射本体属性、表字段语义和结构化领域规则生成；只有实际命中数据的候选才可确认。",
    })

@router.get("/relations/{relation_id}/mapping", response_model=ApiResponse)
async def get_relation_mapping(
    relation_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    relation = db.query(SysOntologyRelation).filter(
        SysOntologyRelation.relation_id == relation_id
    ).first()
    if not relation:
        raise HTTPException(status_code=404, detail="关系不存在")
    mapping = db.query(SysRelationMapping).filter(
        SysRelationMapping.relation_id == relation_id
    ).first()
    blueprint_payload = _load_latest_blueprint_payload(db, relation.domain_id)
    draft = _build_relation_mapping_draft(db, relation, blueprint_payload)

    return ApiResponse(data={
        "mapping_id": mapping.mapping_id if mapping else "",
        "relation_id": relation.relation_id,
        "edge_table_name": relation.relation_table_name or "",
        "source_table": mapping.source_table if mapping and mapping.source_table else draft.get("source_table", ""),
        "target_table": mapping.target_table if mapping and mapping.target_table else draft.get("target_table", ""),
        "join_condition": mapping.join_condition if mapping and mapping.join_condition else draft.get("join_condition", ""),
        "edge_sql": mapping.edge_sql if mapping and mapping.edge_sql else draft.get("edge_sql", ""),
        "mapping_mode": mapping.mapping_mode if mapping and mapping.mapping_mode else "DIRECT",
        "relation_table": mapping.relation_table if mapping else "",
        "relation_source_column": mapping.relation_source_column if mapping else "",
        "relation_target_column": mapping.relation_target_column if mapping else "",
        "edge_property_columns_json": mapping.edge_property_columns_json if mapping else "",
        "mapping_status": mapping.mapping_status if mapping else ("SUGGESTED" if draft.get("edge_sql") or draft.get("join_condition") or draft.get("source_table") else "PENDING"),
        "mapped_by": mapping.mapped_by if mapping else None,
        "mapped_at": mapping.mapped_at.isoformat() if mapping and mapping.mapped_at else None,
        "draft": draft,
        "blueprint_context": {
            "rule_summary": (blueprint_payload or {}).get("rule_summary") or {},
            "business_summary": (blueprint_payload or {}).get("business_summary") or {},
            "candidate_counts": {
                "entity_candidates": len((blueprint_payload or {}).get("entity_candidates") or []),
                "relation_candidates": len((blueprint_payload or {}).get("relation_candidates") or []),
            },
        },
        "blueprint_id": blueprint_payload.get("blueprint_id") if blueprint_payload else None,
        "blueprint_version": blueprint_payload.get("blueprint_version") if blueprint_payload else None,
    })


@router.post("/relations/{relation_id}/mapping", response_model=ApiResponse)
async def create_relation_mapping(
    relation_id: str,
    req: RelationMappingCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    relation = db.query(SysOntologyRelation).filter(SysOntologyRelation.relation_id == relation_id).first()
    if not relation:
        raise HTTPException(status_code=404, detail="关系不存在")
    has_mapping_content = (
        bool(req.relation_table and req.relation_source_column and req.relation_target_column)
        if (req.mapping_mode or "DIRECT").upper() == "RELATION_TABLE"
        else bool(req.source_table and req.target_table and req.join_condition)
    )
    mapping = SysRelationMapping(
        mapping_id=generate_id("rmap"),
        relation_id=relation_id,
        source_table=req.source_table,
        target_table=req.target_table,
        join_condition=req.join_condition,
        edge_sql=req.edge_sql,
        mapping_mode=(req.mapping_mode or "DIRECT").upper(),
        relation_table=req.relation_table,
        relation_source_column=req.relation_source_column,
        relation_target_column=req.relation_target_column,
        edge_property_columns_json=req.edge_property_columns_json,
        mapping_status="STALE" if has_mapping_content else "PENDING"
    )
    db.add(mapping)
    _set_relation_edge_table_name(db, relation, req.edge_table_name)
    db.commit()
    db.refresh(mapping)
    return ApiResponse(message="关系映射已创建", data={"mapping_id": mapping.mapping_id})


@router.put("/relations/{relation_id}/mapping", response_model=ApiResponse)
async def update_relation_mapping(
    relation_id: str,
    req: RelationMappingUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    mapping = db.query(SysRelationMapping).filter(
        SysRelationMapping.relation_id == relation_id
    ).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="关系映射不存在")

    relation = db.query(SysOntologyRelation).filter(SysOntologyRelation.relation_id == relation_id).first()
    if not relation:
        raise HTTPException(status_code=404, detail="关系不存在")

    payload = req.model_dump(exclude_unset=True)
    edge_table_name = payload.pop("edge_table_name", None)
    if "edge_table_name" in req.model_fields_set:
        _set_relation_edge_table_name(db, relation, edge_table_name)
    for field, value in payload.items():
        setattr(mapping, field, value)
    mapping.mapped_by = current_user.get("username", "unknown")
    mapping.mapped_at = datetime.utcnow()
    mapping.mapping_status = "STALE" if _is_relation_mapping_ddl_ready(mapping) else "PENDING"
    db.commit()
    return ApiResponse(message="关系映射已更新")


@router.post("/relations/edge-sql/preview", response_model=ApiResponse)
async def preview_relation_edge_sql(
    req: EdgeSqlPreviewRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    from app.services.source_data_service import SourceDataService

    service = SourceDataService(db)
    try:
        data = service.preview_remote_select_sql(
            source_id=req.source_id,
            sql=req.edge_sql,
            schema=req.schema,
            sample_limit=req.sample_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"预览 edge_sql 失败: {str(exc)}")
    return ApiResponse(data=data)


# ====== LLM自动映射 ======

@router.post("/entities/{entity_id}/auto-mapping", response_model=ApiResponse)
async def auto_mapping(
    entity_id: str,
    req: AutoMappingRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """调用LLM为实体生成属性候选和源字段映射建议"""
    logger.info(
        "API auto mapping requested: entity_id=%s domain_id=%s source_id=%s schema=%s model_config_id=%s",
        entity_id,
        req.domain_id,
        req.source_id,
        req.schema,
        req.model_config_id,
    )
    from app.services.llm_service import LLMService
    from app.services.source_data_service import SourceDataService

    entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="实体不存在")
    logger.info(
        "Auto mapping entity loaded: entity_id=%s entity_name=%s display_name=%s",
        entity.entity_id,
        entity.entity_name,
        entity.entity_display_name,
    )
    domain = db.query(SysDomain).filter(SysDomain.domain_id == req.domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="业务分析域不存在")
    logger.info(
        "Auto mapping domain loaded: domain_id=%s domain_name=%s",
        domain.domain_id,
        domain.domain_name,
    )
    blueprint_payload = _load_latest_blueprint_payload(db, domain.domain_id)
    blueprint_context = _build_blueprint_mapping_context(blueprint_payload, entity)

    # Get properties
    properties = db.query(SysOntologyProperty).filter(
        SysOntologyProperty.entity_id == entity_id
    ).all()
    logger.info(
        "Auto mapping properties loaded: entity_id=%s property_count=%s property_names=%s",
        entity_id,
        len(properties),
        [prop.property_name for prop in properties[:10]],
    )

    source_service = SourceDataService(db)
    try:
        property_keywords = []
        for prop in properties:
            property_keywords.extend([
                prop.property_name or "",
                prop.property_display_name or "",
                prop.property_desc or "",
            ])
        entity_keywords = source_service.build_entity_keywords(
            entity.entity_name,
            entity.entity_display_name,
            entity.entity_desc,
            property_keywords=property_keywords,
        )
        raw_table_catalog = source_service.get_remote_table_catalog_for_mapping(
            source_id=req.source_id,
            domain_id=req.domain_id,
            schema=req.schema,
            entity_keywords=entity_keywords,
        )
        table_catalog = {
            **raw_table_catalog,
            "tables": _sort_table_catalog_with_blueprint(raw_table_catalog.get("tables", []) or [], blueprint_context),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取数据源元数据失败: {str(exc)}")

    # Call LLM
    llm_service = LLMService(db)
    table_selection = await llm_service.select_relevant_tables_for_mapping(
        entity,
        properties,
        table_catalog.get("tables", []),
        domain_context={
            "domain_id": domain.domain_id,
            "domain_name": domain.domain_name,
            "domain_desc": domain.domain_desc,
        },
        blueprint_context=blueprint_context,
        mapping_instruction=req.mapping_instruction,
        config_id=req.model_config_id,
    )
    selected_name_map = {
        item.get("table_name", "").upper(): item
        for item in (table_catalog.get("tables", []) or [])
        if item.get("table_name")
    }
    selected_tables = [
        {**selected_name_map[item["table_name"].upper()], "selection_reason": item.get("reason", "")}
        for item in table_selection.get("selected_tables", [])
        if item.get("table_name", "").upper() in selected_name_map
    ]
    source_context = source_service.get_remote_tables_metadata_by_names(
        source_id=req.source_id,
        schema=table_catalog.get("schema"),
        tables=selected_tables,
        sample_limit=req.sample_limit,
        entity_keywords=entity_keywords,
        source_name=table_catalog.get("source_name"),
    )
    result = await llm_service.auto_mapping(
        entity,
        properties,
        source_context.get("tables", []),
        domain_context={
            "domain_id": domain.domain_id,
            "domain_name": domain.domain_name,
            "domain_desc": domain.domain_desc,
        },
        source_context={
            "source_id": source_context.get("source_id"),
            "source_name": source_context.get("source_name"),
            "schema": source_context.get("schema"),
        },
        blueprint_context=blueprint_context,
        mapping_instruction=req.mapping_instruction,
        config_id=req.model_config_id,
    )
    result["table_selection"] = table_selection
    result["blueprint_context"] = {
        "blueprint_id": blueprint_context.get("blueprint_id"),
        "blueprint_version": blueprint_context.get("blueprint_version"),
        "preferred_tables": blueprint_context.get("preferred_tables") or [],
        "preferred_roles": blueprint_context.get("preferred_roles") or [],
        "recommended_build_mode": (blueprint_context.get("entity_recommendation") or {}).get("recommended_build_mode") or "",
    }
    existing_mappings = _get_existing_mappings_snapshot(db, entity.entity_id)
    diff_summary = _build_mapping_diff_summary(result.get("mappings", []) or [], existing_mappings)
    annotated_mappings, oracle_vertex = _annotate_oracle_vertex_mapping(
        entity,
        diff_summary.pop("suggestions", result.get("mappings", [])),
    )
    result["mappings"] = annotated_mappings
    result["oracle_vertex"] = oracle_vertex
    result["existing_mappings"] = existing_mappings
    result["diff_summary"] = diff_summary
    logger.info(
        "API auto mapping result: entity_id=%s mapping_count=%s added=%s changed=%s unchanged=%s",
        entity_id,
        len(result.get("mappings", []) or []),
        diff_summary.get("added_count", 0),
        diff_summary.get("changed_count", 0),
        diff_summary.get("unchanged_count", 0),
    )
    return ApiResponse(data=result)


@router.post("/domains/{domain_id}/bulk-auto-mapping", response_model=ApiResponse)
async def bulk_auto_mapping(
    domain_id: str,
    req: BulkAutoMappingRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    logger.info(
        "API bulk auto mapping requested: domain_id=%s source_id=%s schema=%s model_config_id=%s auto_apply=%s",
        domain_id,
        req.source_id,
        req.schema,
        req.model_config_id,
        req.auto_apply,
    )

    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="业务分析域不存在")

    entities = db.query(SysOntologyEntity).filter(
        SysOntologyEntity.domain_id == domain_id
    ).order_by(SysOntologyEntity.created_at).all()
    if not entities:
        raise HTTPException(status_code=400, detail="当前分析域下没有可映射的本体对象")
    running_task = db.query(SysMappingTask).filter(
        SysMappingTask.domain_id == domain_id,
        SysMappingTask.task_type == "BULK_GENERATE",
        SysMappingTask.status == "IN_PROGRESS",
    ).order_by(SysMappingTask.created_at.desc()).first()
    if running_task:
        logger.info("Bulk auto mapping rejected due to running task: domain_id=%s task_id=%s", domain_id, running_task.task_id)
        return ApiResponse(
            message="当前分析域已有后台映射任务正在执行",
            data={
                "task_id": running_task.task_id,
                "status": running_task.status,
                "async_mode": True,
            }
        )

    logger.info(
        "Bulk auto mapping async task created: domain_id=%s entity_count=%s entities=%s",
        domain_id,
        len(entities),
        [item.entity_name for item in entities[:20]],
    )
    latest_blueprint_payload = _load_latest_blueprint_payload(db, domain_id)
    request_payload = req.model_dump()
    request_payload["blueprint_id"] = latest_blueprint_payload.get("blueprint_id") if latest_blueprint_payload else None
    request_payload["blueprint_version"] = latest_blueprint_payload.get("blueprint_version") if latest_blueprint_payload else None
    request_payload["blueprint_status"] = latest_blueprint_payload.get("blueprint_status") if latest_blueprint_payload else None
    initial_summary = {
        "entity_count": len(entities),
        "processed_count": 0,
        "ready_count": 0,
        "empty_count": 0,
        "failed_count": 0,
        "applied_total": 0,
        "relation_count": db.query(SysOntologyRelation).filter(
            SysOntologyRelation.domain_id == domain_id
        ).count(),
        "relation_ready_count": 0,
        "relation_missing_count": 0,
        "applied_relation_count": 0,
        "node_sql_ready_count": 0,
        "applied_node_count": 0,
        "running": True,
        "current_entity_name": "",
        "blueprint_version": request_payload.get("blueprint_version"),
    }
    initial_result = {
        "domain": {
            "domain_id": domain.domain_id,
            "domain_name": domain.domain_name,
            "domain_desc": domain.domain_desc,
        },
        "summary": initial_summary,
        "entities": [],
        "relations": [],
        "graph_mapping_design": {},
        "auto_apply": req.auto_apply,
        "blueprint_id": request_payload.get("blueprint_id"),
        "blueprint_version": request_payload.get("blueprint_version"),
        "blueprint_status": request_payload.get("blueprint_status"),
    }
    try:
        task = _save_mapping_task(
            db=db,
            domain_id=domain_id,
            source_id=req.source_id,
            model_config_id=req.model_config_id,
            task_type="BULK_GENERATE",
            status="IN_PROGRESS",
            request_payload=request_payload,
            result_payload=initial_result,
            summary_payload=initial_summary,
            current_user=current_user,
        )
        db.commit()
        db.refresh(task)
    except Exception as exc:
        logger.exception("Create bulk mapping task failed: domain_id=%s error=%s", domain_id, str(exc))
        db.rollback()
        raise HTTPException(status_code=500, detail="创建后台映射任务失败")

    current_user_payload = {"username": current_user.get("username", "unknown")}
    worker = threading.Thread(
        target=_run_bulk_auto_mapping_job,
        args=(task.task_id, domain_id, request_payload, current_user_payload),
        daemon=True,
        name=f"bulk-mapping-{task.task_id}",
    )
    worker.start()
    return ApiResponse(
        message="全域映射建议已转为后台执行，可在任务列表查看进度",
        data={
            "task_id": task.task_id,
            "status": task.status,
            "async_mode": True,
            "summary": initial_summary,
        }
    )


@router.post("/domains/{domain_id}/bulk-apply-mappings", response_model=ApiResponse)
async def bulk_apply_mappings(
    domain_id: str,
    req: BulkMappingApplyRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    logger.info(
        "API bulk apply mappings requested: domain_id=%s entity_count=%s relation_count=%s",
        domain_id,
        len(req.entities or []),
        len(req.relations or []),
    )
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="业务分析域不存在")

    applied_entities = []
    applied_property_total = 0
    applied_node_total = 0
    for item in req.entities:
        entity = db.query(SysOntologyEntity).filter(
            SysOntologyEntity.entity_id == item.entity_id,
            SysOntologyEntity.domain_id == domain_id,
        ).first()
        if not entity:
            continue
        applied_count = _apply_mappings_for_entity(db, entity, [
            {**mapping, "action": mapping.get("action", "accept")}
            for mapping in item.mappings
        ], current_user)
        node_applied_count = _apply_node_mapping_for_entity(
            db,
            entity,
            {
                "build_type": item.build_type,
                "node_table_name": item.table_name,
                "node_sql": item.view_sql,
                "key_property_name": next(
                    (
                        mapping.get("property_name")
                        for mapping in item.mappings
                        if mapping.get("is_vertex_key")
                    ),
                    "",
                ),
            },
            current_user,
        )
        applied_property_total += applied_count
        applied_node_total += node_applied_count
        applied_entities.append({
            "entity_id": entity.entity_id,
            "entity_name": entity.entity_name,
            "entity_display_name": entity.entity_display_name,
            "applied_count": applied_count,
            "node_applied": bool(node_applied_count),
        })

    applied_relations = []
    applied_relation_total = 0
    for item in req.relations:
        relation = db.query(SysOntologyRelation).filter(
            SysOntologyRelation.relation_id == item.relation_id,
            SysOntologyRelation.domain_id == domain_id,
        ).first()
        if not relation:
            continue
        payload = item.model_dump()
        applied_count = _apply_mapping_for_relation(
            db,
            relation,
            payload,
            current_user,
        )
        applied_relation_total += applied_count
        applied_relations.append({
            "relation_id": relation.relation_id,
            "relation_name": relation.relation_name,
            "source_entity_id": relation.source_entity_id,
            "target_entity_id": relation.target_entity_id,
            "applied": bool(applied_count),
        })

    applied_total = applied_property_total + applied_node_total + applied_relation_total
    db.commit()
    latest_blueprint_payload = _load_latest_blueprint_payload(db, domain_id)
    request_payload = req.model_dump()
    request_payload["blueprint_id"] = latest_blueprint_payload.get("blueprint_id") if latest_blueprint_payload else None
    request_payload["blueprint_version"] = latest_blueprint_payload.get("blueprint_version") if latest_blueprint_payload else None
    request_payload["blueprint_status"] = latest_blueprint_payload.get("blueprint_status") if latest_blueprint_payload else None
    response_payload = {
        "applied_total": applied_total,
        "applied_property_total": applied_property_total,
        "applied_node_total": applied_node_total,
        "applied_relation_total": applied_relation_total,
        "entities": applied_entities,
        "relations": applied_relations,
        "blueprint_id": request_payload.get("blueprint_id"),
        "blueprint_version": request_payload.get("blueprint_version"),
        "blueprint_status": request_payload.get("blueprint_status"),
    }
    summary_payload = {
        "applied_total": applied_total,
        "applied_property_total": applied_property_total,
        "applied_node_total": applied_node_total,
        "applied_relation_total": applied_relation_total,
        "entity_count": len(applied_entities),
        "relation_count": len(applied_relations),
        "blueprint_version": request_payload.get("blueprint_version"),
    }
    try:
        _save_mapping_task(
            db=db,
            domain_id=domain_id,
            source_id=None,
            model_config_id=None,
            task_type="BULK_APPLY",
            status="SUCCESS",
            request_payload=request_payload,
            result_payload=response_payload,
            summary_payload=summary_payload,
            current_user=current_user,
        )
        db.commit()
    except Exception as exc:
        logger.exception("Save bulk apply task failed: domain_id=%s error=%s", domain_id, str(exc))
        db.rollback()
    logger.info(
        "API bulk apply mappings result: domain_id=%s applied_properties=%s applied_nodes=%s applied_relations=%s",
        domain_id,
        applied_property_total,
        applied_node_total,
        applied_relation_total,
    )
    return ApiResponse(message="本体节点属性与关系实现已批量应用", data=response_payload)


@router.get("/domains/{domain_id}/tasks", response_model=ApiResponse)
async def list_mapping_tasks(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    logger.info("API list mapping tasks requested: domain_id=%s", domain_id)
    latest_blueprint_payload = _load_latest_blueprint_payload(db, domain_id)
    latest_blueprint_version = latest_blueprint_payload.get("blueprint_version") if latest_blueprint_payload else None
    tasks = db.query(SysMappingTask).filter(
        SysMappingTask.domain_id == domain_id
    ).order_by(SysMappingTask.created_at.desc()).all()
    if latest_blueprint_version is not None:
        tasks = [
            task for task in tasks
            if _extract_task_blueprint_version(task) == latest_blueprint_version
        ]
    tasks = tasks[:20]
    return ApiResponse(data=[
        {
            "task_id": task.task_id,
            "domain_id": task.domain_id,
            "source_id": task.source_id,
            "model_config_id": task.model_config_id,
            "task_type": task.task_type,
            "status": task.status,
            "request_json": task.request_json,
            "result_json": task.result_json,
            "summary_json": task.summary_json,
            "created_by": task.created_by,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        }
        for task in tasks
    ])


@router.delete("/domains/{domain_id}/tasks", response_model=ApiResponse)
async def clear_mapping_tasks(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """清除指定分析域的数据映射操作任务快照，不影响已确认的映射配置。"""
    logger.info("API clear mapping tasks requested: domain_id=%s", domain_id)
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="业务分析域不存在")

    running_task = db.query(SysMappingTask).filter(
        SysMappingTask.domain_id == domain_id,
        SysMappingTask.status.in_(["PENDING", "IN_PROGRESS", "RUNNING"]),
    ).first()
    if running_task:
        raise HTTPException(status_code=409, detail="当前分析域仍有正在执行的数据映射任务，暂不能清除")

    deleted_count = db.query(SysMappingTask).filter(
        SysMappingTask.domain_id == domain_id
    ).delete(synchronize_session=False)
    db.commit()
    logger.info(
        "Mapping task snapshots cleared: domain_id=%s deleted_count=%s operator=%s",
        domain_id,
        deleted_count,
        current_user.get("username", "unknown"),
    )
    return ApiResponse(
        message="已清除当前分析域的数据映射操作记录",
        data={"domain_id": domain_id, "deleted_count": deleted_count},
    )


@router.get("/tasks/{task_id}", response_model=ApiResponse)
async def get_mapping_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    logger.info("API get mapping task requested: task_id=%s", task_id)
    task = db.query(SysMappingTask).filter(SysMappingTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="映射任务不存在")
    return ApiResponse(data={
        "task_id": task.task_id,
        "domain_id": task.domain_id,
        "source_id": task.source_id,
        "model_config_id": task.model_config_id,
        "task_type": task.task_type,
        "status": task.status,
        "request_json": task.request_json,
        "result_json": task.result_json,
        "summary_json": task.summary_json,
        "created_by": task.created_by,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    })


# ====== 映射确认 ======

@router.post("/entities/{entity_id}/mappings/confirm", response_model=ApiResponse)
async def confirm_mappings(
    entity_id: str,
    req: MappingConfirmRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="实体不存在")
    confirmed_count = _apply_mappings_for_entity(db, entity, req.mappings, current_user)
    db.commit()
    return ApiResponse(message="映射确认完成", data={"confirmed_count": confirmed_count})
