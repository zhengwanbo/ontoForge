import asyncio
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload
from app.core.database import SessionLocal, get_db
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
from app.api.mapping import _find_latest_relation_task_recommendation
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


def _run_ddl_execution_task(
    log_id: str,
    domain_id: str,
    ddl_content: str,
    target_source_id: str,
    execute_mode: str,
    skip_existing: bool,
) -> None:
    """Run a DDL task outside the request worker and persist its final result."""
    from app.services.ddl_service import DDLService

    task_db = SessionLocal()
    started_at = time.time()
    try:
        target_source = task_db.query(SysDataSource).filter(
            SysDataSource.source_id == target_source_id,
            SysDataSource.is_active == "Y",
        ).first()
        if not target_source:
            raise ValueError("目标对象数据库不存在或未启用")

        result = asyncio.run(DDLService(task_db).execute_ddl(
            ddl_content,
            target_source=target_source,
            execute_mode=execute_mode,
            skip_existing=skip_existing,
        ))
        execution_result = "SUCCESS" if (result.get("failed") or 0) == 0 else "FAILED"
        error_message = (
            f"{result.get('failed') or 0} 条 DDL 语句执行失败"
            if execution_result == "FAILED" else None
        )

        log = task_db.query(SysDDLLog).filter(SysDDLLog.log_id == log_id).first()
        if not log:
            return
        log.execution_result = execution_result
        log.error_message = error_message
        log.execution_duration = time.time() - started_at
        for sequence_no, detail in enumerate(result.get("details") or [], start=1):
            task_db.add(SysDDLStatementLog(
                log_id=log_id,
                sequence_no=sequence_no,
                statement=detail.get("statement") or "",
                status=detail.get("status") or "failed",
                object_type=detail.get("object_type"),
                object_name=detail.get("object_name"),
                message=detail.get("message"),
                error_message=detail.get("error"),
            ))

        # Persist each SQL result before the optional deployment-state
        # reconciliation. This guarantees that a metadata-query failure never
        # turns an otherwise complete execution history into an empty list.
        task_db.commit()
        try:
            _sync_entity_deployment_status(task_db, domain_id, target_source)
            _sync_relation_deployment_status(task_db, domain_id, target_source)
            task_db.commit()
        except Exception:
            # DDL execution is already fully recorded. Keep its result and
            # leave status refresh for the next page reload/redeployment.
            task_db.rollback()
    except Exception as exc:
        task_db.rollback()
        log = task_db.query(SysDDLLog).filter(SysDDLLog.log_id == log_id).first()
        if log:
            log.execution_result = "FAILED"
            log.error_message = str(exc)
            log.execution_duration = time.time() - started_at
            has_statement_detail = task_db.query(SysDDLStatementLog).filter(
                SysDDLStatementLog.log_id == log_id,
            ).first()
            if not has_statement_detail:
                task_db.add(SysDDLStatementLog(
                    log_id=log_id,
                    sequence_no=1,
                    statement=ddl_content,
                    status="failed",
                    object_type="DDL TASK",
                    object_name="-",
                    message="DDL 后台执行任务异常终止",
                    error_message=str(exc),
                ))
            task_db.commit()
    finally:
        task_db.close()


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


def _relation_mapping_is_complete(mapping: Optional[SysRelationMapping]) -> bool:
    if not mapping:
        return False
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


def _effective_relation_mapping(db: Session, relation: SysOntologyRelation) -> dict:
    """Return persisted mapping, augmented with the latest verified task draft."""
    mapping = getattr(relation, "relation_mapping", None)
    if _relation_mapping_is_complete(mapping):
        return {
            "mapping_id": mapping.mapping_id,
            "relation_id": mapping.relation_id,
            "source_table": mapping.source_table,
            "target_table": mapping.target_table,
            "join_condition": mapping.join_condition,
            "edge_sql": mapping.edge_sql,
            "mapping_mode": mapping.mapping_mode or "DIRECT",
            "relation_table": mapping.relation_table,
            "relation_source_column": mapping.relation_source_column,
            "relation_target_column": mapping.relation_target_column,
            "mapping_status": mapping.mapping_status,
            "recommendation_source": "persisted_mapping",
        }
    recommendation = _find_latest_relation_task_recommendation(db, relation)
    if recommendation:
        return {
            "mapping_id": mapping.mapping_id if mapping else "",
            "relation_id": relation.relation_id,
            "source_table": recommendation.get("source_table"),
            "target_table": recommendation.get("target_table"),
            "join_condition": recommendation.get("join_condition"),
            "edge_sql": "",
            "mapping_mode": "DIRECT",
            "relation_table": "",
            "relation_source_column": "",
            "relation_target_column": "",
            "mapping_status": "SUGGESTED",
            "recommendation_source": "latest_bulk_mapping_task",
        }
    return {
        "mapping_id": mapping.mapping_id if mapping else "",
        "relation_id": relation.relation_id,
        "source_table": mapping.source_table if mapping else "",
        "target_table": mapping.target_table if mapping else "",
        "join_condition": mapping.join_condition if mapping else "",
        "edge_sql": mapping.edge_sql if mapping else "",
        "mapping_mode": mapping.mapping_mode if mapping else "DIRECT",
        "relation_table": mapping.relation_table if mapping else "",
        "relation_source_column": mapping.relation_source_column if mapping else "",
        "relation_target_column": mapping.relation_target_column if mapping else "",
        "mapping_status": mapping.mapping_status if mapping else "PENDING",
        "recommendation_source": "none",
    }


def _sync_verified_relation_recommendations(db: Session, relations: list[SysOntologyRelation]) -> None:
    """Materialize valid latest-task Join suggestions before DDL generation."""
    changed = False
    for relation in relations:
        mapping = getattr(relation, "relation_mapping", None)
        if _relation_mapping_is_complete(mapping):
            continue
        recommendation = _find_latest_relation_task_recommendation(db, relation)
        if not recommendation:
            continue
        if not mapping:
            mapping = SysRelationMapping(mapping_id=generate_id("rmap"), relation_id=relation.relation_id)
            db.add(mapping)
            relation.relation_mapping = mapping
        mapping.source_table = recommendation.get("source_table") or None
        mapping.target_table = recommendation.get("target_table") or None
        mapping.join_condition = recommendation.get("join_condition") or None
        mapping.edge_sql = None
        mapping.mapping_mode = "DIRECT"
        mapping.relation_table = None
        mapping.relation_source_column = None
        mapping.relation_target_column = None
        mapping.mapping_status = "STALE"
        mapping.mapped_at = datetime.utcnow()
        changed = True
    if changed:
        db.flush()


def _serialize_relation(db: Session, relation: SysOntologyRelation) -> dict:
    relation_mapping = getattr(relation, "relation_mapping", None)
    effective_mapping = _effective_relation_mapping(db, relation)
    mapping_status = effective_mapping.get("mapping_status")
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
        "relation_mapping": {**effective_mapping, "mapping_status": mapping_status},
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
        "relations": [_serialize_relation(db, relation) for relation in relations],
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

    _sync_verified_relation_recommendations(db, relations)

    ddl_service = DDLService(db)
    try:
        ddl_result = await ddl_service.generate_ddl(domain, entities, relations)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not (ddl_result.get("ddl_statements") or []):
        raise HTTPException(status_code=400, detail="未生成任何DDL语句，请检查本体、映射或部署设计配置")

    # Update entity status
    entity_deployment_modes = ddl_result.get("entity_deployment_modes") or {}
    for entity in entities:
        deployment_mode = entity_deployment_modes.get(entity.entity_id)
        if deployment_mode in {"VIEW", "TABLE"}:
            # Keep the existing ONTO_NODE_* object name so existing relation
            # mappings remain valid; only its Oracle implementation changes.
            entity.build_type = deployment_mode
            if entity.entity_mapping:
                entity.entity_mapping.build_type = deployment_mode
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
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Create a background DDL execution task and return without waiting for Oracle."""

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

    log = SysDDLLog(
        log_id=generate_id("ddl"),
        domain_id=domain_id,
        ddl_content=req.ddl_content,
        execution_result="RUNNING",
        executed_by=current_user.get("username", "unknown"),
        executed_at=datetime.utcnow(),
    )
    db.add(log)
    db.commit()

    background_tasks.add_task(
        _run_ddl_execution_task,
        log.log_id,
        domain_id,
        req.ddl_content,
        target_source.source_id,
        req.execute_mode,
        req.skip_existing,
    )
    return ApiResponse(message="DDL 已提交后台执行", data={
        "log_id": log.log_id,
        "execution_result": "RUNNING",
    })


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


@router.get("/logs/{log_id}/status", response_model=ApiResponse)
async def get_ddl_execution_status(
    log_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return current task state and persisted per-statement results."""
    log = db.query(SysDDLLog).filter(SysDDLLog.log_id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="DDL 执行任务不存在")
    details = db.query(SysDDLStatementLog).filter(
        SysDDLStatementLog.log_id == log_id,
    ).order_by(SysDDLStatementLog.sequence_no.asc()).all()
    result_details = [{
        "statement": item.statement,
        "status": item.status,
        "object_type": item.object_type,
        "object_name": item.object_name,
        "message": item.message,
        "error": item.error_message,
    } for item in details]
    return ApiResponse(data={
        "log_id": log.log_id,
        "execution_result": log.execution_result,
        "error_message": log.error_message,
        "duration": log.execution_duration,
        "result": {
            "total": len(result_details),
            "success": sum(1 for item in result_details if item["status"] == "success"),
            "failed": sum(1 for item in result_details if item["status"] == "failed"),
            "skipped": sum(1 for item in result_details if item["status"] == "skipped"),
            "details": result_details,
        },
    })


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
    # Historical async tasks from before statement-level failure persistence
    # may only have a task summary. Do not render a misleading empty dialog.
    if not details and log.execution_result == "FAILED" and log.error_message:
        return ApiResponse(data=[{
            "sequence_no": 1,
            "statement": log.ddl_content or "",
            "status": "failed",
            "object_type": "DDL TASK",
            "object_name": "-",
            "message": "DDL 后台任务异常终止，未生成逐语句历史记录",
            "error_message": log.error_message,
        }])
    return ApiResponse(data=[DDLStatementLogResponse(
        sequence_no=item.sequence_no,
        statement=item.statement,
        status=item.status,
        object_type=item.object_type,
        object_name=item.object_name,
        message=item.message,
        error_message=item.error_message,
    ).model_dump() for item in details])
