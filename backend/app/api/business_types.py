import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.models import SysBusinessType, SysDomain, generate_id
from app.schemas.schemas import ApiResponse, BusinessTypeCreate, BusinessTypeUpdate
from app.services.business_type_service import ensure_default_business_types, serialize_business_type

router = APIRouter(prefix="/business-types", tags=["业务类型语义"])


def normalize_code(value: str) -> str:
    return (value or "").strip().upper().replace(" ", "_")


@router.get("", response_model=ApiResponse)
async def list_business_types(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    ensure_default_business_types(db)
    rows = db.query(SysBusinessType).order_by(SysBusinessType.created_at.asc()).all()
    return ApiResponse(data=[serialize_business_type(row) for row in rows])


@router.get("/{type_code}", response_model=ApiResponse)
async def get_business_type(type_code: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    ensure_default_business_types(db)
    row = db.query(SysBusinessType).filter(SysBusinessType.type_code == normalize_code(type_code)).first()
    if not row:
        raise HTTPException(status_code=404, detail="业务类型不存在")
    return ApiResponse(data=serialize_business_type(row))


@router.post("", response_model=ApiResponse)
async def create_business_type(req: BusinessTypeCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    ensure_default_business_types(db)
    type_code = normalize_code(req.type_code)
    type_name = (req.type_name or "").strip()
    if not type_code or not type_name:
        raise HTTPException(status_code=400, detail="业务类型编码和名称不能为空")
    if db.query(SysBusinessType).filter(SysBusinessType.type_code == type_code).first():
        raise HTTPException(status_code=400, detail=f"业务类型编码已存在：{type_code}")
    row = SysBusinessType(
        type_id=generate_id("btype"), type_code=type_code, type_name=type_name,
        semantic_desc=req.semantic_desc, semantic_patterns_json=json.dumps([item.model_dump() for item in req.semantic_patterns], ensure_ascii=False),
        status=req.status or "ACTIVE", created_by=current_user.get("username", "unknown"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ApiResponse(data=serialize_business_type(row))


@router.put("/{type_code}", response_model=ApiResponse)
async def update_business_type(type_code: str, req: BusinessTypeUpdate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    ensure_default_business_types(db)
    row = db.query(SysBusinessType).filter(SysBusinessType.type_code == normalize_code(type_code)).first()
    if not row:
        raise HTTPException(status_code=404, detail="业务类型不存在")
    if "type_name" in req.model_fields_set:
        if not (req.type_name or "").strip():
            raise HTTPException(status_code=400, detail="业务类型名称不能为空")
        row.type_name = req.type_name.strip()
    if "semantic_desc" in req.model_fields_set:
        row.semantic_desc = req.semantic_desc
    if "semantic_patterns" in req.model_fields_set:
        row.semantic_patterns_json = json.dumps([item.model_dump() for item in (req.semantic_patterns or [])], ensure_ascii=False)
    if "status" in req.model_fields_set:
        row.status = req.status
    db.commit()
    db.refresh(row)
    return ApiResponse(data=serialize_business_type(row))


@router.delete("/{type_code}", response_model=ApiResponse)
async def delete_business_type(type_code: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    code = normalize_code(type_code)
    row = db.query(SysBusinessType).filter(SysBusinessType.type_code == code).first()
    if not row:
        raise HTTPException(status_code=404, detail="业务类型不存在")
    if db.query(SysDomain).filter(SysDomain.domain_type == code).first():
        raise HTTPException(status_code=400, detail="仍有业务分析域使用该类型，无法删除")
    db.delete(row)
    db.commit()
    return ApiResponse(message="业务类型已删除")
