from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import ensure_domain_access, get_current_user
from app.core.database import get_db
from app.models.models import (
    SysBusinessActivity, SysBusinessRule, SysDomain, SysOntologyEntity,
    SysOntologyRelation, SysProcessDef, generate_id,
)
from app.schemas.schemas import ApiResponse, BusinessActivityCreate, BusinessActivityUpdate, BusinessRuleCreate, BusinessRuleUpdate

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


def require_domain(db: Session, current_user: dict, domain_id: str):
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="分析域不存在")
    ensure_domain_access(db, current_user, domain_id)
    return domain


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
