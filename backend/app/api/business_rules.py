from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import ensure_domain_access, get_current_user
from app.core.database import get_db
from app.models.models import (
    SysBusinessActivity, SysBusinessRule, SysDomain, SysOntologyEntity,
    SysMetricDefinition, SysOntologyRelation, SysProcessDef, generate_id,
)
from app.schemas.schemas import (
    ApiResponse, BusinessActivityCreate, BusinessActivityUpdate,
    BusinessRuleCreate, BusinessRuleUpdate, MetricDefinitionCreate, MetricDefinitionUpdate,
)

router = APIRouter(prefix="/business-rules", tags=["业务规则活动"])


def serialize_activity(activity: SysBusinessActivity):
    return {column: getattr(activity, column) for column in (
        "activity_id", "domain_id", "activity_name", "activity_type", "activity_desc",
        "process_id", "config_json", "status", "created_by", "created_at", "updated_at"
    )}


def serialize_rule(rule: SysBusinessRule):
    return {column: getattr(rule, column) for column in (
        "rule_id", "domain_id", "rule_name", "rule_category", "rule_desc", "trigger_event",
        "scope_entity_id", "scope_relation_id", "condition_json", "activity_id", "priority",
        "status", "created_by", "created_at", "updated_at"
    )}


def serialize_metric(metric: SysMetricDefinition):
    return {column: getattr(metric, column) for column in (
        "metric_id", "domain_id", "entity_id", "metric_code", "metric_name",
        "metric_category", "metric_desc", "calculation_expr", "aggregation_method",
        "calculation_period", "unit", "threshold_config", "status", "created_by",
        "created_at", "updated_at",
    )}


def require_domain(db: Session, current_user: dict, domain_id: str):
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="分析域不存在")
    ensure_domain_access(db, current_user, domain_id)
    return domain


def require_metric_entity(db: Session, domain_id: str, entity_id: str) -> None:
    entity = db.query(SysOntologyEntity).filter(
        SysOntologyEntity.entity_id == entity_id,
        SysOntologyEntity.domain_id == domain_id,
    ).first()
    if not entity:
        raise HTTPException(status_code=400, detail="指标关联的本体对象不存在或不属于当前分析域")


def ensure_metric_code_unique(db: Session, domain_id: str, metric_code: str, metric_id: str | None = None) -> None:
    query = db.query(SysMetricDefinition).filter(
        SysMetricDefinition.domain_id == domain_id,
        SysMetricDefinition.metric_code == metric_code.strip(),
    )
    if metric_id:
        query = query.filter(SysMetricDefinition.metric_id != metric_id)
    if query.first():
        raise HTTPException(status_code=400, detail="同一分析域内指标编码不可重复")


@router.get("/domains/{domain_id}/catalog", response_model=ApiResponse)
async def get_rule_catalog(domain_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    require_domain(db, current_user, domain_id)
    return ApiResponse(data={
        "entities": [{"entity_id": item.entity_id, "entity_name": item.entity_name, "entity_display_name": item.entity_display_name} for item in db.query(SysOntologyEntity).filter(SysOntologyEntity.domain_id == domain_id).all()],
        "relations": [{"relation_id": item.relation_id, "relation_name": item.relation_name} for item in db.query(SysOntologyRelation).filter(SysOntologyRelation.domain_id == domain_id).all()],
        "processes": [{"process_id": item.process_id, "process_name": item.process_name} for item in db.query(SysProcessDef).filter(SysProcessDef.domain_id == domain_id).all()],
    })


@router.get("/domains/{domain_id}/activities", response_model=ApiResponse)
async def list_activities(domain_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    require_domain(db, current_user, domain_id)
    items = db.query(SysBusinessActivity).filter(SysBusinessActivity.domain_id == domain_id).order_by(SysBusinessActivity.updated_at.desc()).all()
    return ApiResponse(data=[serialize_activity(item) for item in items])


@router.get("/domains/{domain_id}/metrics", response_model=ApiResponse)
async def list_metrics(domain_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    require_domain(db, current_user, domain_id)
    items = db.query(SysMetricDefinition).filter(
        SysMetricDefinition.domain_id == domain_id,
    ).order_by(SysMetricDefinition.updated_at.desc()).all()
    return ApiResponse(data=[serialize_metric(item) for item in items])


@router.post("/domains/{domain_id}/metrics", response_model=ApiResponse)
async def create_metric(domain_id: str, req: MetricDefinitionCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    require_domain(db, current_user, domain_id)
    require_metric_entity(db, domain_id, req.entity_id)
    if not req.metric_code.strip() or not req.metric_name.strip():
        raise HTTPException(status_code=400, detail="指标编码和指标名称不能为空")
    ensure_metric_code_unique(db, domain_id, req.metric_code)
    item = SysMetricDefinition(
        metric_id=generate_id("metric"), domain_id=domain_id,
        created_by=current_user.get("username", "unknown"), **req.model_dump(),
    )
    db.add(item); db.commit(); db.refresh(item)
    return ApiResponse(data=serialize_metric(item))


@router.put("/metrics/{metric_id}", response_model=ApiResponse)
async def update_metric(metric_id: str, req: MetricDefinitionUpdate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    item = db.query(SysMetricDefinition).filter(SysMetricDefinition.metric_id == metric_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="指标定义不存在")
    ensure_domain_access(db, current_user, item.domain_id)
    require_metric_entity(db, item.domain_id, req.entity_id)
    if not req.metric_code.strip() or not req.metric_name.strip():
        raise HTTPException(status_code=400, detail="指标编码和指标名称不能为空")
    ensure_metric_code_unique(db, item.domain_id, req.metric_code, metric_id)
    for key, value in req.model_dump().items():
        setattr(item, key, value)
    item.updated_at = datetime.utcnow(); db.commit()
    return ApiResponse(data=serialize_metric(item))


@router.delete("/metrics/{metric_id}", response_model=ApiResponse)
async def delete_metric(metric_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    item = db.query(SysMetricDefinition).filter(SysMetricDefinition.metric_id == metric_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="指标定义不存在")
    ensure_domain_access(db, current_user, item.domain_id)
    db.delete(item); db.commit()
    return ApiResponse(message="指标定义已删除")


@router.post("/domains/{domain_id}/activities", response_model=ApiResponse)
async def create_activity(domain_id: str, req: BusinessActivityCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    require_domain(db, current_user, domain_id)
    item = SysBusinessActivity(activity_id=generate_id("act"), domain_id=domain_id, created_by=current_user.get("username", "unknown"), **req.model_dump())
    db.add(item); db.commit(); db.refresh(item)
    return ApiResponse(data=serialize_activity(item))


@router.put("/activities/{activity_id}", response_model=ApiResponse)
async def update_activity(activity_id: str, req: BusinessActivityUpdate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    item = db.query(SysBusinessActivity).filter(SysBusinessActivity.activity_id == activity_id).first()
    if not item: raise HTTPException(status_code=404, detail="业务活动不存在")
    ensure_domain_access(db, current_user, item.domain_id)
    for key, value in req.model_dump().items(): setattr(item, key, value)
    item.updated_at = datetime.utcnow(); db.commit()
    return ApiResponse(data=serialize_activity(item))


@router.delete("/activities/{activity_id}", response_model=ApiResponse)
async def delete_activity(activity_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    item = db.query(SysBusinessActivity).filter(SysBusinessActivity.activity_id == activity_id).first()
    if not item: raise HTTPException(status_code=404, detail="业务活动不存在")
    ensure_domain_access(db, current_user, item.domain_id)
    db.query(SysBusinessRule).filter(SysBusinessRule.activity_id == activity_id).update({"activity_id": None})
    db.delete(item); db.commit()
    return ApiResponse(message="业务活动已删除")


@router.get("/domains/{domain_id}/rules", response_model=ApiResponse)
async def list_rules(domain_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    require_domain(db, current_user, domain_id)
    items = db.query(SysBusinessRule).filter(SysBusinessRule.domain_id == domain_id).order_by(SysBusinessRule.priority.desc(), SysBusinessRule.updated_at.desc()).all()
    return ApiResponse(data=[serialize_rule(item) for item in items])


@router.post("/domains/{domain_id}/rules", response_model=ApiResponse)
async def create_rule(domain_id: str, req: BusinessRuleCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    require_domain(db, current_user, domain_id)
    item = SysBusinessRule(rule_id=generate_id("rule"), domain_id=domain_id, created_by=current_user.get("username", "unknown"), **req.model_dump())
    db.add(item); db.commit(); db.refresh(item)
    return ApiResponse(data=serialize_rule(item))


@router.put("/rules/{rule_id}", response_model=ApiResponse)
async def update_rule(rule_id: str, req: BusinessRuleUpdate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    item = db.query(SysBusinessRule).filter(SysBusinessRule.rule_id == rule_id).first()
    if not item: raise HTTPException(status_code=404, detail="业务规则不存在")
    ensure_domain_access(db, current_user, item.domain_id)
    for key, value in req.model_dump().items(): setattr(item, key, value)
    item.updated_at = datetime.utcnow(); db.commit()
    return ApiResponse(data=serialize_rule(item))


@router.delete("/rules/{rule_id}", response_model=ApiResponse)
async def delete_rule(rule_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    item = db.query(SysBusinessRule).filter(SysBusinessRule.rule_id == rule_id).first()
    if not item: raise HTTPException(status_code=404, detail="业务规则不存在")
    ensure_domain_access(db, current_user, item.domain_id)
    db.delete(item); db.commit()
    return ApiResponse(message="业务规则已删除")
