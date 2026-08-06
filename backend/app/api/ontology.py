from datetime import datetime
from typing import Optional, List, Dict, Set
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import or_, text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.schemas.schemas import (
    ApiResponse, EntityCreate, EntityUpdate, EntityResponse,
    PropertyCreate, PropertyUpdate, PropertyResponse,
    RelationCreate, RelationUpdate, RelationResponse,
    OntologyGuideGenerateRequest, OntologyGuideApplyRequest, OntologyNaturalAdjustRequest, OntologyNaturalAdjustApplyRequest,
)
from app.models.models import (
    SysOntologyEntity, SysOntologyProperty, SysOntologyRelation,
    SysDomain, SysEntityMapping, SysPropertyMapping, SysRelationMapping,
    SysOntologyBlueprint, SysMappingTask, SysDDLLog, SysDDLStatementLog, generate_id
)
import json
import re

router = APIRouter(tags=["本体构建"])
logger = get_logger(__name__)

SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")


def _normalize_identifier(name: Optional[str]) -> str:
    token = (name or "").strip().upper()
    if not token:
        return ""
    if "." in token:
        token = token.split(".")[-1].strip()
    return token if SAFE_IDENTIFIER_RE.match(token) else ""


def _normalize_relation_table_name(name: Optional[str]) -> str:
    """将英文关系名转换为可执行的边表名。"""
    token = _normalize_identifier(name)
    if not token:
        return ""
    return token if token.startswith("ONTO_") else f"ONTO_EDGE_{token}"


def _build_relation_edge_view_name(relation_name: Optional[str], relation_id: Optional[str]) -> str:
    raw_name = relation_name or relation_id or "EDGE"
    token = re.sub(r"[^A-Za-z0-9_]+", "_", raw_name.upper()).strip("_")
    token = token[:20] or "EDGE"
    return f"ONTO_EDGE_{token}_V"


def _collect_blueprint_generated_objects(
    domain_id: str,
    entities: List[SysOntologyEntity],
    relations: List[SysOntologyRelation],
    blueprints: List[SysOntologyBlueprint],
) -> Dict[str, Set[str]]:
    graph_names: Set[str] = set()
    view_names: Set[str] = set()
    table_names: Set[str] = set()

    for entity in entities:
        object_name = _normalize_identifier(entity.table_name or f"ONTO_{(entity.entity_name or '').upper()}")
        if not object_name:
            continue
        if (entity.build_type or "TABLE").upper() == "VIEW":
            view_names.add(object_name)
        else:
            table_names.add(object_name)

    for relation in relations:
        relation_table_name = _normalize_identifier(relation.relation_table_name)
        if relation_table_name:
            table_names.add(relation_table_name)
        edge_view_name = _normalize_identifier(_build_relation_edge_view_name(relation.relation_name, relation.relation_id))
        if edge_view_name:
            view_names.add(edge_view_name)

    for blueprint in blueprints:
        try:
            payload = json.loads(blueprint.blueprint_json or "{}")
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        deployment_design = payload.get("deployment_design") or {}
        property_graph = deployment_design.get("property_graph") or {}
        graph_name = _normalize_identifier(property_graph.get("graph_name") or f"{domain_id}_PG")
        if graph_name:
            graph_names.add(graph_name)

        for item in (deployment_design.get("semantic_views") or []):
            view_name = _normalize_identifier(item.get("view_name"))
            if view_name:
                view_names.add(view_name)

        for item in (deployment_design.get("edge_views") or []):
            view_name = _normalize_identifier(item.get("view_name"))
            if view_name:
                view_names.add(view_name)

    return {
        "graphs": graph_names,
        "views": view_names,
        "tables": table_names,
    }


def _drop_generated_objects(db: Session, object_names: Dict[str, Set[str]]) -> Dict[str, int]:
    dropped_graphs = 0
    dropped_views = 0
    dropped_tables = 0
    skipped_missing = 0
    failed = 0

    drop_order = [
        ("graphs", "DROP PROPERTY GRAPH {name}", "graph"),
        ("views", "DROP VIEW {name}", "view"),
        ("tables", "DROP TABLE {name} PURGE", "table"),
    ]

    for key, statement_template, object_type in drop_order:
        for object_name in sorted(object_names.get(key) or []):
            stmt = statement_template.format(name=object_name)
            try:
                db.execute(text(stmt))
                db.commit()
                if object_type == "graph":
                    dropped_graphs += 1
                elif object_type == "view":
                    dropped_views += 1
                else:
                    dropped_tables += 1
            except Exception as exc:
                db.rollback()
                error_text = str(exc).upper()
                if any(token in error_text for token in ["ORA-00942", "ORA-04043", "DOES NOT EXIST", "NOT EXIST"]):
                    skipped_missing += 1
                    continue
                failed += 1
                logger.warning("Drop generated object failed: type=%s name=%s error=%s", object_type, object_name, str(exc))

    return {
        "dropped_graphs": dropped_graphs,
        "dropped_views": dropped_views,
        "dropped_tables": dropped_tables,
        "skipped_missing_objects": skipped_missing,
        "failed_drop_objects": failed,
    }


# ====== 实体管理 ======

@router.get("/domains/{domain_id}/entities", response_model=ApiResponse)
async def list_entities(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    entities = db.query(SysOntologyEntity).filter(
        SysOntologyEntity.domain_id == domain_id
    ).order_by(SysOntologyEntity.created_at).all()

    data = []
    for e in entities:
        props = [PropertyResponse(
            property_id=p.property_id,
            entity_id=p.entity_id,
            property_name=p.property_name,
            property_display_name=p.property_display_name,
            data_type=p.data_type,
            is_primary_key=p.is_primary_key,
            is_nullable=p.is_nullable,
            property_desc=p.property_desc,
            order_num=p.order_num,
            source_mark=p.source_mark,
            created_at=p.created_at,
            mapping=None
        ).model_dump() for p in e.properties]

        data.append(EntityResponse(
            entity_id=e.entity_id,
            domain_id=e.domain_id,
            entity_name=e.entity_name,
            entity_display_name=e.entity_display_name,
            entity_desc=e.entity_desc,
            build_type=e.build_type,
            table_name=e.table_name,
            status=e.status,
            icon=e.icon,
            color=e.color,
            graph_position=e.graph_position,
            created_by=e.created_by,
            created_at=e.created_at,
            updated_at=e.updated_at,
            properties=props
        ).model_dump())

    return ApiResponse(data=data)


@router.post("/domains/{domain_id}/entities", response_model=ApiResponse)
async def create_entity(
    domain_id: str,
    req: EntityCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    # Check domain exists
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="分析域不存在")

    # Check duplicate name in same domain
    existing = db.query(SysOntologyEntity).filter(
        SysOntologyEntity.domain_id == domain_id,
        SysOntologyEntity.entity_name == req.entity_name
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="同分析域内实体名称不可重复")

    # Generate default table name
    table_name = f"ONTO_NODE_{req.entity_name.upper()}"

    entity = SysOntologyEntity(
        entity_id=generate_id("ent"),
        domain_id=domain_id,
        entity_name=req.entity_name,
        entity_display_name=req.entity_display_name,
        entity_desc=req.entity_desc,
        build_type=req.build_type,
        table_name=table_name,
        status="DRAFT",
        icon=req.icon,
        color=req.color,
        graph_position=json.dumps({"x": 200, "y": 200}),
        created_by=current_user.get("username", "unknown")
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)

    return ApiResponse(data=EntityResponse(
        entity_id=entity.entity_id,
        domain_id=entity.domain_id,
        entity_name=entity.entity_name,
        entity_display_name=entity.entity_display_name,
        entity_desc=entity.entity_desc,
        build_type=entity.build_type,
        table_name=entity.table_name,
        status=entity.status,
        icon=entity.icon,
        color=entity.color,
        graph_position=entity.graph_position,
        created_by=entity.created_by,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        properties=[]
    ).model_dump())


@router.put("/entities/{entity_id}", response_model=ApiResponse)
async def update_entity(
    entity_id: str,
    req: EntityUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="实体不存在")

    payload = req.model_dump(exclude_unset=True)

    next_entity_name = payload.get("entity_name", entity.entity_name)
    next_build_type = payload.get("build_type", entity.build_type)

    if "entity_name" in payload and next_entity_name != entity.entity_name:
        existing = db.query(SysOntologyEntity).filter(
            SysOntologyEntity.domain_id == entity.domain_id,
            SysOntologyEntity.entity_name == next_entity_name,
            SysOntologyEntity.entity_id != entity_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="同分析域内实体名称不可重复")

    for field, value in payload.items():
        setattr(entity, field, value)

    if "table_name" not in payload and ("entity_name" in payload or "build_type" in payload):
        entity.table_name = (
            f"ONTO_NODE_{next_entity_name.upper()}_V"
            if next_build_type == "VIEW"
            else f"ONTO_NODE_{next_entity_name.upper()}"
        )

    entity.updated_at = datetime.utcnow()
    db.commit()

    return ApiResponse(data={"entity_id": entity.entity_id, "entity_name": entity.entity_name})


@router.delete("/entities/{entity_id}", response_model=ApiResponse)
async def delete_entity(
    entity_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="实体不存在")

    # 关系的源/目标实体外键未配置数据库级联删除。先逐条删除关联关系，
    # 以便 ORM 同时清理关系映射，避免残留边或外键约束阻止实体删除。
    related_relations = db.query(SysOntologyRelation).filter(
        or_(
            SysOntologyRelation.source_entity_id == entity_id,
            SysOntologyRelation.target_entity_id == entity_id,
        )
    ).all()
    deleted_relation_count = len(related_relations)
    for relation in related_relations:
        db.delete(relation)

    db.delete(entity)
    db.commit()
    return ApiResponse(
        message="实体及关联关系已删除",
        data={"deleted_relation_count": deleted_relation_count}
    )


# ====== 属性管理 ======

@router.get("/entities/{entity_id}/properties", response_model=ApiResponse)
async def list_properties(
    entity_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    properties = db.query(SysOntologyProperty).filter(
        SysOntologyProperty.entity_id == entity_id
    ).order_by(SysOntologyProperty.order_num).all()

    data = [PropertyResponse(
        property_id=p.property_id,
        entity_id=p.entity_id,
        property_name=p.property_name,
        property_display_name=p.property_display_name,
        data_type=p.data_type,
        is_primary_key=p.is_primary_key,
        is_nullable=p.is_nullable,
        property_desc=p.property_desc,
        order_num=p.order_num,
        source_mark=p.source_mark,
        created_at=p.created_at,
        mapping=None
    ).model_dump() for p in properties]

    return ApiResponse(data=data)


@router.post("/entities/{entity_id}/properties", response_model=ApiResponse)
async def create_property(
    entity_id: str,
    req: PropertyCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="实体不存在")

    # Check duplicate property name
    existing = db.query(SysOntologyProperty).filter(
        SysOntologyProperty.entity_id == entity_id,
        SysOntologyProperty.property_name == req.property_name
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="同实体内属性名称不可重复")

    prop = SysOntologyProperty(
        property_id=generate_id("prop"),
        entity_id=entity_id,
        property_name=req.property_name,
        property_display_name=req.property_display_name,
        data_type=req.data_type,
        is_primary_key=req.is_primary_key,
        is_nullable=req.is_nullable,
        property_desc=req.property_desc,
        order_num=req.order_num
    )
    db.add(prop)
    db.commit()
    db.refresh(prop)

    return ApiResponse(data=PropertyResponse(
        property_id=prop.property_id,
        entity_id=prop.entity_id,
        property_name=prop.property_name,
        property_display_name=prop.property_display_name,
        data_type=prop.data_type,
        is_primary_key=prop.is_primary_key,
        is_nullable=prop.is_nullable,
        property_desc=prop.property_desc,
        order_num=prop.order_num,
        source_mark=prop.source_mark,
        created_at=prop.created_at,
        mapping=None
    ).model_dump())


@router.put("/properties/{property_id}", response_model=ApiResponse)
async def update_property(
    property_id: str,
    req: PropertyUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    prop = db.query(SysOntologyProperty).filter(SysOntologyProperty.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="属性不存在")

    for field, value in req.model_dump(exclude_unset=True).items():
        setattr(prop, field, value)
    prop.updated_at = datetime.utcnow()
    db.commit()
    return ApiResponse(message="属性已更新")


@router.delete("/properties/{property_id}", response_model=ApiResponse)
async def delete_property(
    property_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    prop = db.query(SysOntologyProperty).filter(SysOntologyProperty.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="属性不存在")
    entity_id = prop.entity_id
    property_mappings = db.query(SysPropertyMapping).filter(
        SysPropertyMapping.property_id == property_id
    ).all()
    for property_mapping in property_mappings:
        db.delete(property_mapping)
    db.delete(prop)

    remaining_mapped = db.query(SysOntologyProperty).filter(
        SysOntologyProperty.entity_id == entity_id,
        SysOntologyProperty.property_id != property_id,
        SysOntologyProperty.source_mark == "MAPPED"
    ).first()
    entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
    entity_mapping = db.query(SysEntityMapping).filter(SysEntityMapping.entity_id == entity_id).first()
    if entity:
        entity.status = "MAPPED" if remaining_mapped else "DRAFT"
    if entity_mapping:
        entity_mapping.mapping_status = "CONFIRMED" if remaining_mapped else "PENDING"
        entity_mapping.mapped_at = datetime.utcnow()
    db.commit()
    return ApiResponse(message="属性已删除")


# ====== 关系管理 ======

@router.get("/domains/{domain_id}/relations", response_model=ApiResponse)
async def list_relations(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    relations = db.query(SysOntologyRelation).filter(
        SysOntologyRelation.domain_id == domain_id
    ).all()

    data = [RelationResponse(
        relation_id=r.relation_id,
        domain_id=r.domain_id,
        source_entity_id=r.source_entity_id,
        target_entity_id=r.target_entity_id,
        relation_name=r.relation_name,
        relation_type=r.relation_type,
        relation_desc=r.relation_desc,
        relation_table_name=r.relation_table_name,
        created_at=r.created_at
    ).model_dump() for r in relations]

    return ApiResponse(data=data)


@router.post("/domains/{domain_id}/relations", response_model=ApiResponse)
async def create_relation(
    domain_id: str,
    req: RelationCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    # Verify source and target entities exist
    source = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == req.source_entity_id).first()
    target = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == req.target_entity_id).first()
    if not source or not target:
        raise HTTPException(status_code=400, detail="源实体或目标实体不存在")

    # 英文边表名优先；未配置时才保留多对多关系的原有关联表命名。
    relation_table_name = _normalize_relation_table_name(req.relation_table_name)
    if req.relation_table_name and not relation_table_name:
        raise HTTPException(status_code=400, detail="英文边表名只能包含英文字母、数字、下划线、$ 或 #")
    if not relation_table_name and req.relation_type == "MANY_TO_MANY":
        relation_table_name = f"ONTO_REL_{source.entity_name.upper()}_{target.entity_name.upper()}"

    relation = SysOntologyRelation(
        relation_id=generate_id("rel"),
        domain_id=domain_id,
        source_entity_id=req.source_entity_id,
        target_entity_id=req.target_entity_id,
        relation_name=req.relation_name,
        relation_type=req.relation_type,
        relation_desc=req.relation_desc,
        relation_table_name=relation_table_name
    )
    db.add(relation)
    db.commit()
    db.refresh(relation)

    return ApiResponse(data=RelationResponse(
        relation_id=relation.relation_id,
        domain_id=relation.domain_id,
        source_entity_id=relation.source_entity_id,
        target_entity_id=relation.target_entity_id,
        relation_name=relation.relation_name,
        relation_type=relation.relation_type,
        relation_desc=relation.relation_desc,
        relation_table_name=relation.relation_table_name,
        created_at=relation.created_at
    ).model_dump())


@router.put("/relations/{relation_id}", response_model=ApiResponse)
async def update_relation(
    relation_id: str,
    req: RelationUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    relation = db.query(SysOntologyRelation).filter(SysOntologyRelation.relation_id == relation_id).first()
    if not relation:
        raise HTTPException(status_code=404, detail="关系不存在")

    payload = req.model_dump(exclude_unset=True)

    next_source_id = payload.get("source_entity_id", relation.source_entity_id)
    next_target_id = payload.get("target_entity_id", relation.target_entity_id)
    next_relation_type = payload.get("relation_type", relation.relation_type)

    source = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == next_source_id).first()
    target = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == next_target_id).first()
    if not source or not target:
        raise HTTPException(status_code=400, detail="源实体或目标实体不存在")

    requested_relation_table_name = payload.pop("relation_table_name", None)
    has_requested_relation_table_name = "relation_table_name" in req.model_fields_set
    for field, value in payload.items():
        setattr(relation, field, value)

    if has_requested_relation_table_name:
        relation.relation_table_name = _normalize_relation_table_name(requested_relation_table_name)
        if requested_relation_table_name and not relation.relation_table_name:
            raise HTTPException(status_code=400, detail="英文边表名只能包含英文字母、数字、下划线、$ 或 #")
    elif next_relation_type == "MANY_TO_MANY" and not relation.relation_table_name:
        relation.relation_table_name = f"ONTO_REL_{source.entity_name.upper()}_{target.entity_name.upper()}"

    relation.updated_at = datetime.utcnow()
    db.commit()
    return ApiResponse(message="关系已更新")


@router.delete("/relations/{relation_id}", response_model=ApiResponse)
async def delete_relation(
    relation_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    relation = db.query(SysOntologyRelation).filter(SysOntologyRelation.relation_id == relation_id).first()
    if not relation:
        raise HTTPException(status_code=404, detail="关系不存在")
    db.delete(relation)
    db.commit()
    return ApiResponse(message="关系已删除")


@router.delete("/domains/{domain_id}/ontology-data", response_model=ApiResponse)
async def clear_domain_ontology_data(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="分析域不存在")

    entities = db.query(SysOntologyEntity).filter(
        SysOntologyEntity.domain_id == domain_id
    ).all()
    relations = db.query(SysOntologyRelation).filter(
        SysOntologyRelation.domain_id == domain_id
    ).all()
    blueprints = db.query(SysOntologyBlueprint).filter(
        SysOntologyBlueprint.domain_id == domain_id
    ).all()
    mapping_tasks = db.query(SysMappingTask).filter(
        SysMappingTask.domain_id == domain_id
    ).all()
    ddl_logs = db.query(SysDDLLog).filter(
        SysDDLLog.domain_id == domain_id
    ).all()
    ddl_log_ids = [item.log_id for item in ddl_logs]
    ddl_statement_log_count = db.query(SysDDLStatementLog).filter(
        SysDDLStatementLog.log_id.in_(ddl_log_ids)
    ).count() if ddl_log_ids else 0
    if ddl_log_ids:
        # Use a separate, immediate SQL DELETE rather than relying on the ORM
        # unit-of-work ordering.  SysDDLStatementLog has no ORM relationship
        # to SysDDLLog, so Oracle can otherwise receive the parent delete first.
        db.query(SysDDLStatementLog).filter(
            SysDDLStatementLog.log_id.in_(ddl_log_ids)
        ).delete(synchronize_session=False)
        db.flush()
    property_count = db.query(SysOntologyProperty).join(
        SysOntologyEntity,
        SysOntologyProperty.entity_id == SysOntologyEntity.entity_id
    ).filter(
        SysOntologyEntity.domain_id == domain_id
    ).count()
    entity_mapping_count = db.query(SysEntityMapping).join(
        SysOntologyEntity,
        SysEntityMapping.entity_id == SysOntologyEntity.entity_id
    ).filter(
        SysOntologyEntity.domain_id == domain_id
    ).count()
    property_mapping_count = db.query(SysPropertyMapping).join(
        SysOntologyProperty,
        SysPropertyMapping.property_id == SysOntologyProperty.property_id
    ).join(
        SysOntologyEntity,
        SysOntologyProperty.entity_id == SysOntologyEntity.entity_id
    ).filter(
        SysOntologyEntity.domain_id == domain_id
    ).count()
    relation_mapping_count = db.query(SysRelationMapping).join(
        SysOntologyRelation,
        SysRelationMapping.relation_id == SysOntologyRelation.relation_id
    ).filter(
        SysOntologyRelation.domain_id == domain_id
    ).count()

    generated_objects = _collect_blueprint_generated_objects(
        domain_id=domain_id,
        entities=entities,
        relations=relations,
        blueprints=blueprints,
    )
    drop_result = _drop_generated_objects(db, generated_objects)

    for relation in relations:
        db.delete(relation)

    for entity in entities:
        db.delete(entity)

    for blueprint in blueprints:
        db.delete(blueprint)

    for task in mapping_tasks:
        db.delete(task)

    for ddl_log in ddl_logs:
        db.delete(ddl_log)

    db.commit()
    return ApiResponse(
        message="当前分析域的本体对象、属性、关系、映射、Guide设计包和DDL生成数据已清空",
        data={
            "domain_id": domain_id,
            "deleted_entities": len(entities),
            "deleted_properties": property_count,
            "deleted_relations": len(relations),
            "deleted_entity_mappings": entity_mapping_count,
            "deleted_property_mappings": property_mapping_count,
            "deleted_relation_mappings": relation_mapping_count,
            "deleted_blueprints": len(blueprints),
            "deleted_mapping_tasks": len(mapping_tasks),
            "deleted_ddl_logs": len(ddl_logs),
            "deleted_ddl_statement_logs": ddl_statement_log_count,
            **drop_result,
        }
    )


@router.post("/domains/{domain_id}/guide/generate", response_model=ApiResponse)
async def generate_ontology_from_guide(
    domain_id: str,
    req: OntologyGuideGenerateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """结合业务文档和关系表，自动生成业务实体与关系"""
    from app.services.ontology_guide_service import OntologyGuideService

    service = OntologyGuideService(db)
    try:
        data = await service.generate(
            domain_id=domain_id,
            source_id=req.source_id,
            schema=req.schema,
            table_source_mode=req.table_source_mode,
            generation_strategy=req.generation_strategy,
            business_scenario=req.business_scenario,
            relation_tables=req.relation_tables,
            rule_table_name=req.rule_table_name,
            table_bindings=[item.model_dump() for item in req.table_bindings],
            ddl_tables=[item.model_dump() for item in req.ddl_tables],
            rule_datasets=[item.model_dump() for item in req.rule_datasets],
            focus_metric_families=req.focus_metric_families,
            focus_stations=req.focus_stations,
            history_case_sources=req.history_case_sources,
            enabled_patterns=req.enabled_patterns,
            business_document=req.business_document,
            model_config_id=req.model_config_id,
            sample_limit=req.sample_limit,
            auto_apply=req.auto_apply,
            overwrite_existing=req.overwrite_existing,
            created_by=current_user.get("username", "unknown"),
        )
    except ValueError as exc:
        logger.warning("Guide generate rejected: domain_id=%s reason=%s", domain_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Guide generate failed: domain_id=%s error=%s", domain_id, str(exc))
        raise HTTPException(status_code=500, detail=f"Guide 自动生成失败: {str(exc)}")
    return ApiResponse(data=data)


@router.post("/domains/{domain_id}/guide/apply", response_model=ApiResponse)
async def apply_ontology_guide_preview(
    domain_id: str,
    req: OntologyGuideApplyRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """直接应用当前 Guide 预览结果，不重新生成"""
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="分析域不存在")

    from app.services.ontology_guide_service import OntologyGuideService

    service = OntologyGuideService(db)
    try:
        apply_result = service.apply_blueprint(
            domain_id=domain_id,
            blueprint=req.blueprint or {},
            overwrite_existing=req.overwrite_existing,
            created_by=current_user.get("username", "unknown"),
        )
        if req.blueprint_id:
            service.mark_blueprint_status(req.blueprint_id, "APPLIED")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Guide apply failed: domain_id=%s error=%s", domain_id, str(exc))
        raise HTTPException(status_code=500, detail=f"应用 Guide 预览失败: {str(exc)}")
    return ApiResponse(data={"apply_result": apply_result, "blueprint_id": req.blueprint_id})


@router.post("/domains/{domain_id}/guide/parse-document", response_model=ApiResponse)
async def parse_ontology_guide_document(
    domain_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """上传并解析 Guide 文档正文"""
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="业务分析域不存在")

    from app.services.ontology_guide_service import OntologyGuideService

    service = OntologyGuideService(db)
    try:
        content = await file.read()
        data = service.parse_uploaded_document(file.filename or "document", content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"文档解析失败: {str(exc)}")
    return ApiResponse(data=data)


@router.post("/domains/{domain_id}/guide/parse-ddl", response_model=ApiResponse)
async def parse_ontology_guide_ddl(
    domain_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """上传并解析数据库 DDL 文件，提取表结构作为 Guide 表信息来源"""
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="业务分析域不存在")

    from app.services.ontology_guide_service import OntologyGuideService

    service = OntologyGuideService(db)
    try:
        content = await file.read()
        data = service.parse_uploaded_ddl(file.filename or "schema.sql", content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DDL 解析失败: {str(exc)}")
    return ApiResponse(data=data)


@router.post("/domains/{domain_id}/guide/parse-rule-data", response_model=ApiResponse)
async def parse_ontology_guide_rule_data(
    domain_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """上传并解析规则数据文件，用于缺陷识别范围与规则数据提取"""
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="业务分析域不存在")

    from app.services.ontology_guide_service import OntologyGuideService

    service = OntologyGuideService(db)
    try:
        content = await file.read()
        data = service.parse_uploaded_rule_data(file.filename or "rule-data.sql", content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"规则数据解析失败: {str(exc)}")
    return ApiResponse(data=data)


@router.post("/domains/{domain_id}/guide/natural-adjust", response_model=ApiResponse)
async def generate_ontology_natural_adjustment(
    domain_id: str,
    req: OntologyNaturalAdjustRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """通过自然语言生成并应用本体对象/属性/关系调整计划"""
    from app.services.ontology_adjustment_service import OntologyAdjustmentService

    service = OntologyAdjustmentService(db)
    try:
        data = await service.generate(
            domain_id=domain_id,
            instruction=req.instruction,
            selected_entity_id=req.selected_entity_id,
            model_config_id=req.model_config_id,
            auto_apply=req.auto_apply,
            created_by=current_user.get("username", "unknown"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"自然语言调整失败: {str(exc)}")
    return ApiResponse(data=data)


@router.post("/domains/{domain_id}/guide/natural-adjust/apply", response_model=ApiResponse)
async def apply_ontology_natural_adjustment(
    domain_id: str,
    req: OntologyNaturalAdjustApplyRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """应用前端确认后的自然语言调整计划"""
    from app.services.ontology_adjustment_service import OntologyAdjustmentService

    service = OntologyAdjustmentService(db)
    try:
        apply_result = service.apply_plan(
            domain_id=domain_id,
            plan=req.plan or {},
            created_by=current_user.get("username", "unknown"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"应用自然语言调整失败: {str(exc)}")
    return ApiResponse(data={"apply_result": apply_result})


# ====== 本体图数据 ======

@router.get("/domains/{domain_id}/graph", response_model=ApiResponse)
async def get_ontology_graph(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    entities = db.query(SysOntologyEntity).filter(
        SysOntologyEntity.domain_id == domain_id
    ).all()

    relations = db.query(SysOntologyRelation).filter(
        SysOntologyRelation.domain_id == domain_id
    ).all()

    nodes = []
    for e in entities:
        position = json.loads(e.graph_position or '{"x": 200, "y": 200}')
        props_count = len(e.properties)
        mapped_count = sum(1 for p in e.properties if p.source_mark == "MAPPED")
        nodes.append({
            "id": e.entity_id,
            "name": e.entity_name,
            "displayName": e.entity_display_name,
            "desc": e.entity_desc,
            "buildType": e.build_type,
            "tableName": e.table_name,
            "status": e.status,
            "icon": e.icon,
            "color": e.color,
            "position": position,
            "propertiesCount": props_count,
            "mappedCount": mapped_count
        })

    edges = []
    for r in relations:
        edges.append({
            "id": r.relation_id,
            "source": r.source_entity_id,
            "target": r.target_entity_id,
            "name": r.relation_name,
            "type": r.relation_type,
            "desc": r.relation_desc,
            "relationTableName": r.relation_table_name
        })

    return ApiResponse(data={"nodes": nodes, "edges": edges})


# ====== 图形位置更新 ======

@router.put("/entities/{entity_id}/position", response_model=ApiResponse)
async def update_entity_position(
    entity_id: str,
    position: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    entity = db.query(SysOntologyEntity).filter(SysOntologyEntity.entity_id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="实体不存在")
    entity.graph_position = json.dumps(position)
    entity.updated_at = datetime.utcnow()
    db.commit()
    return ApiResponse(message="位置已更新")
