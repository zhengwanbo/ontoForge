import json
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app.core.auth import ensure_domain_access, get_current_user
from app.core.database import get_db
from app.models.models import (
    SysDataSource,
    SysDDLLog,
    SysMappingTask,
    SysOntologyBlueprint,
    SysOntologyEntity,
    SysOntologyProperty,
    SysOntologyRelation,
)
from app.schemas.schemas import ApiResponse


router = APIRouter(prefix="/dashboard", tags=["平台首页"])


def _safe_json(value: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _activity(timestamp: Any, activity_type: str, title: str, detail: str, status: str, target: str) -> Dict[str, Any]:
    return {
        "time": timestamp.isoformat() if timestamp else "",
        "activity_type": activity_type,
        "title": title,
        "detail": detail,
        "status": status,
        "target": target,
    }


@router.get("/domains/{domain_id}/overview", response_model=ApiResponse)
async def get_domain_dashboard_overview(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """A domain-scoped landing-page overview for ontology delivery progress."""
    ensure_domain_access(db, current_user, domain_id)
    entities = db.query(SysOntologyEntity).options(
        selectinload(SysOntologyEntity.properties).selectinload(SysOntologyProperty.mapping),
        selectinload(SysOntologyEntity.entity_mapping),
    ).filter(SysOntologyEntity.domain_id == domain_id).order_by(SysOntologyEntity.updated_at.desc()).all()
    relations = db.query(SysOntologyRelation).options(
        selectinload(SysOntologyRelation.relation_mapping),
    ).filter(SysOntologyRelation.domain_id == domain_id).all()
    sources = db.query(SysDataSource).filter(SysDataSource.business_domain_id == domain_id).all()

    from app.services.ddl_service import DDLService
    readiness_issues = DDLService(db).validate_ddl_readiness(entities, relations) if entities else []
    mapped_property_count = sum(
        1 for entity in entities for prop in (entity.properties or [])
        if (prop.source_mark or "").upper() == "MAPPED"
        or (getattr(prop, "mapping", None) and (prop.mapping.source_table or "").strip())
    )
    mapped_entity_count = sum(
        1 for entity in entities
        if (getattr(entity, "entity_mapping", None) and (entity.entity_mapping.view_sql or "").strip())
        or any((prop.source_mark or "").upper() == "MAPPED" for prop in (entity.properties or []))
    )
    configured_relation_count = sum(
        1 for relation in relations
        if getattr(relation, "relation_mapping", None)
        and (relation.relation_mapping.mapping_status or "").upper() in {"SUGGESTED", "CONFIRMED", "STALE", "DEPLOYED"}
    )

    latest_blueprint = db.query(SysOntologyBlueprint).filter(
        SysOntologyBlueprint.domain_id == domain_id,
    ).order_by(SysOntologyBlueprint.version_no.desc(), SysOntologyBlueprint.created_at.desc()).first()
    latest_ddl = db.query(SysDDLLog).filter(
        SysDDLLog.domain_id == domain_id,
    ).order_by(SysDDLLog.executed_at.desc()).first()
    recent_tasks = db.query(SysMappingTask).filter(
        SysMappingTask.domain_id == domain_id,
    ).order_by(SysMappingTask.updated_at.desc()).limit(5).all()
    recent_ddls = db.query(SysDDLLog).filter(
        SysDDLLog.domain_id == domain_id,
    ).order_by(SysDDLLog.executed_at.desc()).limit(5).all()

    activities: List[Dict[str, Any]] = []
    if latest_blueprint:
        activities.append(_activity(
            latest_blueprint.updated_at or latest_blueprint.created_at,
            "BLUEPRINT",
            f"本体设计包 v{latest_blueprint.version_no}",
            f"状态：{latest_blueprint.status or 'GENERATED'}",
            latest_blueprint.status or "GENERATED",
            "/business/ontology",
        ))
    for task in recent_tasks:
        summary = _safe_json(task.summary_json)
        activities.append(_activity(
            task.updated_at or task.created_at,
            "MAPPING",
            "数据映射任务",
            f"{task.status or '-'} · 已处理 {summary.get('processed_count', 0)} 个对象",
            task.status or "",
            "/mapping/operation",
        ))
    for log in recent_ddls:
        activities.append(_activity(
            log.executed_at,
            "DDL",
            "DDL 生成 / 执行",
            log.error_message or f"状态：{log.execution_result or '-'}",
            log.execution_result or "",
            "/ddl",
        ))
    activities.sort(key=lambda item: item["time"] or "", reverse=True)

    blocker_issues = readiness_issues[:12]
    return ApiResponse(data={
        "generated_at": datetime.utcnow().isoformat(),
        "summary": {
            "data_source_count": len(sources),
            "connected_source_count": sum(1 for source in sources if (source.connection_status or "").upper() == "CONNECTED"),
            "entity_count": len(entities),
            "relation_count": len(relations),
            "property_count": sum(len(entity.properties or []) for entity in entities),
            "mapped_entity_count": mapped_entity_count,
            "mapped_property_count": mapped_property_count,
            "configured_relation_count": configured_relation_count,
            "ddl_blocker_count": len(readiness_issues),
            "deployed_entity_count": sum(1 for entity in entities if (entity.status or "").upper() == "DEPLOYED"),
            "latest_ddl_status": latest_ddl.execution_result if latest_ddl else "NOT_STARTED",
            "blueprint_version": latest_blueprint.version_no if latest_blueprint else None,
        },
        "stages": [
            {"key": "source", "title": "业务数据", "complete": bool(sources), "detail": f"{len(sources)} 个数据源"},
            {"key": "design", "title": "本体设计", "complete": bool(latest_blueprint or entities), "detail": f"{len(entities)} 个实体 / {len(relations)} 条关系"},
            {"key": "mapping", "title": "数据映射", "complete": bool(entities) and mapped_entity_count == len(entities), "detail": f"{mapped_entity_count} / {len(entities)} 个实体已映射"},
            {"key": "ddl", "title": "DDL 生成", "complete": (latest_ddl.execution_result if latest_ddl else "") in {"GENERATED", "SUCCESS"}, "detail": f"{len(readiness_issues)} 个校验问题"},
            {"key": "deploy", "title": "对象部署", "complete": bool(entities) and all((entity.status or "").upper() == "DEPLOYED" for entity in entities), "detail": f"{sum(1 for entity in entities if (entity.status or '').upper() == 'DEPLOYED')} 个节点已部署"},
            {"key": "graph", "title": "图谱查询", "complete": (latest_ddl.execution_result if latest_ddl else "") == "SUCCESS", "detail": "部署后可进行图数据查询"},
        ],
        "blockers": blocker_issues,
        "activities": activities[:10],
        "graph_overview": {
            "nodes": [
                {
                    "id": entity.entity_id,
                    "name": entity.entity_display_name or entity.entity_name,
                    "status": entity.status or "DRAFT",
                    "mapped": entity.entity_id in {item.entity_id for item in entities if item.entity_id and ((getattr(item, 'entity_mapping', None) and (item.entity_mapping.view_sql or '').strip()) or any((prop.source_mark or '').upper() == 'MAPPED' for prop in (item.properties or [])))},
                }
                for entity in entities[:12]
            ],
            "relation_count": len(relations),
        },
    })
