from datetime import datetime
from typing import Optional, List, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import get_current_user
from app.schemas.schemas import ApiResponse, DomainCreate, DomainUpdate, DomainResponse
from app.models.models import SysDomain, SysDataSource, generate_id

router = APIRouter(prefix="/domains", tags=["本体-分析域"])


def normalize_domain_name(name: Optional[str]) -> str:
    return (name or "").strip()


@router.get("", response_model=ApiResponse)
async def list_domains(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    query = db.query(SysDomain)
    if status:
        query = query.filter(SysDomain.status == status)
    domains = query.order_by(SysDomain.created_at.desc()).all()
    data = [DomainResponse(
        domain_id=d.domain_id,
        domain_name=d.domain_name,
        domain_type=d.domain_type or "BUSINESS",
        domain_desc=d.domain_desc,
        status=d.status,
        created_by=d.created_by,
        created_at=d.created_at,
        updated_at=d.updated_at
    ).model_dump() for d in domains]
    return ApiResponse(data=data)


@router.post("", response_model=ApiResponse)
async def create_domain(
    req: DomainCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    normalized_name = normalize_domain_name(req.domain_name)
    if not normalized_name:
        raise HTTPException(status_code=400, detail="业务分析域名称不能为空")

    existing = db.query(SysDomain).filter(SysDomain.domain_name == normalized_name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"业务分析域名称已存在：{normalized_name}")

    domain = SysDomain(
        domain_id=generate_id("dm"),
        domain_name=normalized_name,
        domain_type=req.domain_type,
        domain_desc=req.domain_desc,
        status="ACTIVE",
        created_by=current_user.get("username", "unknown")
    )
    db.add(domain)
    db.commit()
    db.refresh(domain)
    return ApiResponse(data=DomainResponse(
        domain_id=domain.domain_id,
        domain_name=domain.domain_name,
        domain_type=domain.domain_type or "BUSINESS",
        domain_desc=domain.domain_desc,
        status=domain.status,
        created_by=domain.created_by,
        created_at=domain.created_at,
        updated_at=domain.updated_at
    ).model_dump())


@router.get("/{domain_id}", response_model=ApiResponse)
async def get_domain(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="分析域不存在")
    return ApiResponse(data=DomainResponse(
        domain_id=domain.domain_id,
        domain_name=domain.domain_name,
        domain_type=domain.domain_type or "BUSINESS",
        domain_desc=domain.domain_desc,
        status=domain.status,
        created_by=domain.created_by,
        created_at=domain.created_at,
        updated_at=domain.updated_at
    ).model_dump())


@router.put("/{domain_id}", response_model=ApiResponse)
async def update_domain(
    domain_id: str,
    req: DomainUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="分析域不存在")
    provided_fields = req.model_fields_set
    if "domain_name" in provided_fields:
        normalized_name = normalize_domain_name(req.domain_name)
        if not normalized_name:
            raise HTTPException(status_code=400, detail="业务分析域名称不能为空")
        duplicated = (
            db.query(SysDomain)
            .filter(SysDomain.domain_name == normalized_name, SysDomain.domain_id != domain_id)
            .first()
        )
        if duplicated:
            raise HTTPException(status_code=400, detail=f"业务分析域名称已存在：{normalized_name}")
        domain.domain_name = normalized_name
    if "domain_type" in provided_fields:
        domain.domain_type = req.domain_type
    if "domain_desc" in provided_fields:
        domain.domain_desc = req.domain_desc
    if "status" in provided_fields:
        domain.status = req.status
    domain.updated_at = datetime.utcnow()
    db.commit()
    return ApiResponse(data=DomainResponse(
        domain_id=domain.domain_id,
        domain_name=domain.domain_name,
        domain_type=domain.domain_type or "BUSINESS",
        domain_desc=domain.domain_desc,
        status=domain.status,
        created_by=domain.created_by,
        created_at=domain.created_at,
        updated_at=domain.updated_at
    ).model_dump())


@router.delete("/{domain_id}", response_model=ApiResponse)
async def delete_domain(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="分析域不存在")

    if domain.entities or domain.relations or domain.processes:
        raise HTTPException(status_code=400, detail="分析域下仍存在本体对象、关系或流程，无法删除")

    linked_data_source = (
        db.query(SysDataSource)
        .filter(SysDataSource.business_domain_id == domain_id)
        .first()
    )
    if linked_data_source:
        raise HTTPException(status_code=400, detail="仍有数据源绑定该分析域，无法删除")

    db.delete(domain)
    db.commit()
    return ApiResponse(message="分析域已删除")
