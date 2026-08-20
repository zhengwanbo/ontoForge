from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import ensure_domain_access, get_current_user
from app.schemas.schemas import (
    ApiResponse, ProcessCreate, ProcessUpdate, ProcessResponse,
    ProcessGuideGenerateRequest,
)
from app.models.models import SysProcessDef, SysDomain, generate_id
from app.services.llm_service import LLMService

router = APIRouter(prefix="/processes", tags=["分析流程"])


@router.post("/domains/{domain_id}/guide/generate", response_model=ApiResponse)
async def generate_process_guide(
    domain_id: str,
    req: ProcessGuideGenerateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    ensure_domain_access(db, current_user, domain_id)
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="分析域不存在")
    if not req.process_description.strip():
        raise HTTPException(status_code=400, detail="请输入流程描述")

    try:
        result = await LLMService(db).generate_process_blueprint(
            domain=domain,
            process_type=req.process_type,
            process_description=req.process_description,
            config_id=req.model_config_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ApiResponse(data=result)


@router.get("/domains/{domain_id}/processes", response_model=ApiResponse)
async def list_processes(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    ensure_domain_access(db, current_user, domain_id)
    processes = db.query(SysProcessDef).filter(
        SysProcessDef.domain_id == domain_id
    ).order_by(SysProcessDef.created_at.desc()).all()

    data = [ProcessResponse(
        process_id=p.process_id,
        domain_id=p.domain_id,
        process_name=p.process_name,
        process_desc=p.process_desc,
        process_json=p.process_json,
        version=p.version,
        status=p.status,
        created_by=p.created_by,
        created_at=p.created_at,
        updated_at=p.updated_at
    ).model_dump() for p in processes]

    return ApiResponse(data=data)


@router.post("/domains/{domain_id}/processes", response_model=ApiResponse)
async def create_process(
    domain_id: str,
    req: ProcessCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    ensure_domain_access(db, current_user, domain_id)
    domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
    if not domain:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="分析域不存在")

    process = SysProcessDef(
        process_id=generate_id("proc"),
        domain_id=domain_id,
        process_name=req.process_name,
        process_desc=req.process_desc,
        process_json=req.process_json,
        version=req.version,
        status="DRAFT",
        created_by=current_user.get("username", "unknown")
    )
    db.add(process)
    db.commit()
    db.refresh(process)

    return ApiResponse(data=ProcessResponse(
        process_id=process.process_id,
        domain_id=process.domain_id,
        process_name=process.process_name,
        process_desc=process.process_desc,
        process_json=process.process_json,
        version=process.version,
        status=process.status,
        created_by=process.created_by,
        created_at=process.created_at,
        updated_at=process.updated_at
    ).model_dump())


@router.put("/{process_id}", response_model=ApiResponse)
async def update_process(
    process_id: str,
    req: ProcessUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    from fastapi import HTTPException
    process = db.query(SysProcessDef).filter(SysProcessDef.process_id == process_id).first()
    if not process:
        raise HTTPException(status_code=404, detail="流程不存在")
    ensure_domain_access(db, current_user, process.domain_id)

    for field, value in req.model_dump(exclude_unset=True).items():
        setattr(process, field, value)
    process.updated_at = datetime.utcnow()
    db.commit()
    return ApiResponse(message="流程已更新")


@router.delete("/{process_id}", response_model=ApiResponse)
async def delete_process(
    process_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    from fastapi import HTTPException
    process = db.query(SysProcessDef).filter(SysProcessDef.process_id == process_id).first()
    if not process:
        raise HTTPException(status_code=404, detail="流程不存在")
    ensure_domain_access(db, current_user, process.domain_id)
    db.delete(process)
    db.commit()
    return ApiResponse(message="流程已删除")
