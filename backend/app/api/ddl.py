from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload
from app.core.database import get_db
from app.core.auth import get_current_user
from app.schemas.schemas import ApiResponse, DDLGenerateRequest, DDLExecuteRequest, DDLLogResponse, DDLStatementLogResponse
from app.models.models import (
    SysDDLLog,
    SysDDLStatementLog,
    SysDataSource,
    SysOntologyBlueprint,
    SysOntologyEntity,
    SysOntologyProperty,
    SysOntologyRelation,
    SysRelationMapping,
    SysDomain,
    generate_id,
)
from app.services.source_data_service import SourceDataService
import json
import re
import time

router = APIRouter(prefix="/ddl", tags=["DDL生成与应用"])


def _entity_object_name(entity: SysOntologyEntity) -> str:
    """Return a safe Oracle object name for deployment-state reconciliation."""
    name = (entity.table_name or f"ONTO_NODE_{entity.entity_name.upper()}").strip().upper()
    return name if re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", name) else ""


def _sync_entity_deployment_status(
    db: Session,
    domain_id: str,
    target_source: SysDataSource,
) -> None:
    """Use target Oracle objects as the source of truth for entity deployment status."""
    entities = db.query(SysOntologyEntity).filter(
        SysOntologyEntity.domain_id == domain_id,
    ).all()
    object_names = [_entity_object_name(entity) for entity in entities]
    deployed = SourceDataService(db).get_remote_object_metadata(
        source_id=target_source.source_id,
        object_names=[name for name in object_names if name],
        schema=target_source.schema_name,
    )
    actual_names = {
        (item.get("object_name") or "").upper()
        for item in (deployed.get("objects") or [])
    }
    for entity in entities:
        if _entity_object_name(entity) in actual_names:
            entity.status = "DEPLOYED"


def _sync_relation_deployment_status(
    db: Session,
    domain_id: str,
    target_source: SysDataSource,
) -> None:
    """Reflect whether each verified relation edge exists in the target schema."""
    relations = db.query(SysOntologyRelation).options(
        selectinload(SysOntologyRelation.relation_mapping),
    ).filter(SysOntologyRelation.domain_id == domain_id).all()
    edge_names = [
        (relation.relation_table_name or "").strip().upper()
        for relation in relations
        if (relation.relation_table_name or "").strip().upper().startswith("ONTO_EDGE_")
    ]
    deployed = SourceDataService(db).get_remote_object_metadata(
        source_id=target_source.source_id,
        object_names=edge_names,
        schema=target_source.schema_name,
    )
    actual_names = {(item.get("object_name") or "").upper() for item in (deployed.get("objects") or [])}
    for relation in relations:
        mapping = relation.relation_mapping
        if not mapping:
            continue
        edge_name = (relation.relation_table_name or "").strip().upper()
        if edge_name and edge_name in actual_names:
            mapping.mapping_status = "DEPLOYED"
        elif (mapping.join_condition or "").strip():
            # A valid mapping exists but this target has no corresponding edge
            # table, so the DDL page can make the redeployment requirement clear.
            mapping.mapping_status = "STALE"


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


def _serialize_property(prop) -> dict:
    mapping = getattr(prop, "mapping", None)
    return {
        "property_id": prop.property_id,
        "entity_id": prop.entity_id,
        "property_name": prop.property_name,
        "property_display_name": prop.property_display_name,
        "data_type": prop.data_type,
        "is_primary_key": prop.is_primary_key,
        "is_nullable": prop.is_nullable,
        "property_desc": prop.property_desc,
        "order_num": prop.order_num,
        "source_mark": prop.source_mark,
        "mapping": {
            "mapping_id": mapping.mapping_id,
            "property_id": mapping.property_id,
            "source_table": mapping.source_table,
            "source_column": mapping.source_column,
            "mapping_type": mapping.mapping_type,
            "formula_expr": mapping.formula_expr,
            "formula_desc": mapping.formula_desc,
            "confidence": mapping.confidence,
            "mapping_status": mapping.mapping_status,
        } if mapping else None,
    }


def _serialize_entity(entity) -> dict:
    entity_mapping = getattr(entity, "entity_mapping", None)
    return {
        "entity_id": entity.entity_id,
        "domain_id": entity.domain_id,
        "entity_name": entity.entity_name,
        "entity_display_name": entity.entity_display_name,
        "entity_desc": entity.entity_desc,
        "build_type": entity.build_type,
        "table_name": entity.table_name,
        "status": entity.status,
        "icon": entity.icon,
        "color": entity.color,
        "graph_position": entity.graph_position,
        "properties": [_serialize_property(prop) for prop in (entity.properties or [])],
        "entity_mapping": {
            "mapping_id": entity_mapping.mapping_id,
            "entity_id": entity_mapping.entity_id,
            "build_type": entity_mapping.build_type,
            "view_sql": entity_mapping.view_sql,
            "mapping_status": entity_mapping.mapping_status,
        } if entity_mapping else None,
    }


def _serialize_relation(relation) -> dict:
    relation_mapping = getattr(relation, "relation_mapping", None)
    mapping_status = getattr(relation_mapping, "mapping_status", None)
    # Legacy records were marked PENDING because they lacked the now-retired
    # edge_sql field, even when a complete physical Join had been saved.
    # Present these correctly as needing deployment rather than configuration.
    if (
        relation_mapping
        and mapping_status == "PENDING"
        and (relation_mapping.source_table or "").strip()
        and (relation_mapping.target_table or "").strip()
        and (relation_mapping.join_condition or "").strip()
    ):
        mapping_status = "STALE"
    return {
        "relation_id": relation.relation_id,
        "domain_id": relation.domain_id,
        "source_entity_id": relation.source_entity_id,
        "target_entity_id": relation.target_entity_id,
        "relation_name": relation.relation_name,
        "relation_type": relation.relation_type,
        "relation_desc": relation.relation_desc,
        "relation_table_name": relation.relation_table_name,
        "relation_mapping": {
            "mapping_id": relation_mapping.mapping_id,
            "relation_id": relation_mapping.relation_id,
            "source_table": relation_mapping.source_table,
            "target_table": relation_mapping.target_table,
            "join_condition": relation_mapping.join_condition,
            "edge_sql": relation_mapping.edge_sql,
            "mapping_status": mapping_status,
        } if relation_mapping else None,
    }


@router.get("/domains/{domain_id}/context", response_model=ApiResponse)
async def get_ddl_context(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="分析域不存在")

    entities = db.query(SysOntologyEntity).options(
        selectinload(SysOntologyEntity.properties).selectinload(SysOntologyProperty.mapping),
        selectinload(SysOntologyEntity.entity_mapping),
    ).filter(
        SysOntologyEntity.domain_id == domain_id
    ).all()

    relations = db.query(SysOntologyRelation).options(
        selectinload(SysOntologyRelation.relation_mapping),
    ).filter(
        SysOntologyRelation.domain_id == domain_id
    ).all()

    latest_blueprint = (
        db.query(SysOntologyBlueprint)
        .filter(SysOntologyBlueprint.domain_id == domain_id)
        .order_by(SysOntologyBlueprint.version_no.desc(), SysOntologyBlueprint.created_at.desc())
        .first()
    )

    return ApiResponse(data={
        "domain_id": domain.domain_id,
        "domain_name": domain.domain_name,
        "entities": [_serialize_entity(entity) for entity in entities],
        "relations": [_serialize_relation(relation) for relation in relations],
        "blueprint": _serialize_blueprint_payload(latest_blueprint),
    })


@router.post("/domains/{domain_id}/generate", response_model=ApiResponse)
async def generate_ddl(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """调用LLM生成DDL"""
    from app.services.ddl_service import DDLService

    # Get domain data
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="分析域不存在")

    entities = db.query(SysOntologyEntity).options(
        selectinload(SysOntologyEntity.properties).selectinload(SysOntologyProperty.mapping),
        selectinload(SysOntologyEntity.entity_mapping),
    ).filter(
        SysOntologyEntity.domain_id == domain_id
    ).all()
    if not entities:
        raise HTTPException(status_code=400, detail="当前分析域下没有本体对象，无法生成DDL")

    relations = db.query(SysOntologyRelation).options(
        selectinload(SysOntologyRelation.relation_mapping),
    ).filter(
        SysOntologyRelation.domain_id == domain_id
    ).all()

    ddl_service = DDLService(db)
    try:
        ddl_result = await ddl_service.generate_ddl(domain, entities, relations)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not (ddl_result.get("ddl_statements") or []):
        raise HTTPException(status_code=400, detail="未生成任何DDL语句，请检查本体、映射或部署设计配置")

    # Update entity status
    for entity in entities:
        if entity.status == "MAPPED":
            entity.status = "DDL_GENERATED"
    # 生成即保存脚本快照。这样即使用户暂未执行，也能在 DDL 页面回看并继续编辑。
    db.add(SysDDLLog(
        log_id=generate_id("ddl"),
        domain_id=domain_id,
        ddl_content=ddl_result.get("full_ddl") or "",
        execution_result="GENERATED",
        executed_by=current_user.get("username", "unknown"),
        executed_at=datetime.utcnow(),
    ))
    db.commit()

    return ApiResponse(data=ddl_result)


@router.post("/domains/{domain_id}/execute", response_model=ApiResponse)
async def execute_ddl(
    domain_id: str,
    req: DDLExecuteRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """执行DDL"""
    from app.services.ddl_service import DDLService

    target_source = db.query(SysDataSource).filter(
        SysDataSource.source_id == req.target_source_id,
        SysDataSource.is_active == "Y",
    ).first()
    if not target_source:
        raise HTTPException(status_code=400, detail="目标对象数据库不存在或未启用")
    if (target_source.db_type or "").lower() != "oracle":
        raise HTTPException(status_code=400, detail="当前仅支持选择 Oracle 数据源作为 DDL 执行目标")
    if target_source.business_domain_id and target_source.business_domain_id != domain_id:
        raise HTTPException(status_code=400, detail="目标对象数据库不属于当前分析域")

    ddl_service = DDLService(db)
    start_time = time.time()

    try:
        result = await ddl_service.execute_ddl(
            req.ddl_content,
            target_source=target_source,
            execute_mode=req.execute_mode,
            skip_existing=req.skip_existing,
        )
        duration = time.time() - start_time

        execution_result = "SUCCESS" if (result.get("failed") or 0) == 0 else "FAILED"
        error_message = None
        if execution_result == "FAILED":
            error_message = f"{result.get('failed') or 0} 条 DDL 语句执行失败"

        # Log execution summary; individual statement details are returned to the UI.
        log = SysDDLLog(
            log_id=generate_id("ddl"),
            domain_id=domain_id,
            ddl_content=req.ddl_content,
            execution_result=execution_result,
            error_message=error_message,
            executed_by=current_user.get("username", "unknown"),
            executed_at=datetime.utcnow(),
            execution_duration=duration
        )
        db.add(log)
        db.flush()
        for sequence_no, detail in enumerate(result.get("details") or [], start=1):
            db.add(SysDDLStatementLog(
                log_id=log.log_id,
                sequence_no=sequence_no,
                statement=detail.get("statement") or "",
                status=detail.get("status") or "failed",
                object_type=detail.get("object_type"),
                object_name=detail.get("object_name"),
                message=detail.get("message"),
                error_message=detail.get("error"),
            ))

        # Reconcile every entity against the target schema.  This also repairs
        # historical DRAFT/MAPPED rows when their ONTO_NODE_* table exists.
        _sync_entity_deployment_status(db, domain_id, target_source)
        _sync_relation_deployment_status(db, domain_id, target_source)

        db.commit()

        return ApiResponse(data={"result": result, "duration": duration})
    except Exception as e:
        duration = time.time() - start_time
        # Log failure
        log = SysDDLLog(
            log_id=generate_id("ddl"),
            domain_id=domain_id,
            ddl_content=req.ddl_content,
            execution_result="FAILED",
            error_message=str(e),
            executed_by=current_user.get("username", "unknown"),
            executed_at=datetime.utcnow(),
            execution_duration=duration
        )
        db.add(log)
        db.commit()

        return ApiResponse(code=500, message=f"DDL执行失败: {str(e)}", data={"duration": duration})


@router.get("/logs", response_model=ApiResponse)
async def get_ddl_logs(
    domain_id: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    query = db.query(SysDDLLog)
    if domain_id:
        query = query.filter(SysDDLLog.domain_id == domain_id)
    logs = query.order_by(SysDDLLog.executed_at.desc()).limit(limit).all()

    data = [DDLLogResponse(
        log_id=l.log_id,
        domain_id=l.domain_id,
        ddl_content=l.ddl_content,
        execution_result=l.execution_result,
        error_message=l.error_message,
        executed_by=l.executed_by,
        executed_at=l.executed_at,
        execution_duration=l.execution_duration
    ).model_dump() for l in logs]

    return ApiResponse(data=data)


@router.get("/logs/{log_id}/details", response_model=ApiResponse)
async def get_ddl_log_details(
    log_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return the persisted per-statement results for one DDL execution."""
    log = db.query(SysDDLLog).filter(SysDDLLog.log_id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="DDL 历史记录不存在")

    details = db.query(SysDDLStatementLog).filter(
        SysDDLStatementLog.log_id == log_id,
    ).order_by(SysDDLStatementLog.sequence_no.asc()).all()
    return ApiResponse(data=[DDLStatementLogResponse(
        sequence_no=item.sequence_no,
        statement=item.statement,
        status=item.status,
        object_type=item.object_type,
        object_name=item.object_name,
        message=item.message,
        error_message=item.error_message,
    ).model_dump() for item in details])
