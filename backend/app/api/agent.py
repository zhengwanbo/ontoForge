from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from io import BytesIO
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.schemas.schemas import (
    AgentSkillCreate,
    AgentSkillTestRequest,
    AgentSkillUpdate,
    ApiResponse,
)
from app.services.agent_service import AgentService
from app.models.models import SysDataSource
from app.services.source_data_service import SourceDataService

router = APIRouter(prefix="/agent", tags=["智能体构建"])


@router.get("/domains/{domain_id}/property-graphs", response_model=ApiResponse)
async def list_domain_property_graphs(
    domain_id: str,
    source_id: str = Query(...),
    schema: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    source = db.query(SysDataSource).filter(SysDataSource.source_id == source_id, SysDataSource.is_active == "Y").first()
    if not source:
        raise HTTPException(status_code=400, detail="数据源不存在或未启用")
    if (source.db_type or "").lower() != "oracle":
        raise HTTPException(status_code=400, detail="属性图对象仅支持 Oracle 数据源")
    if source.business_domain_id and source.business_domain_id != domain_id:
        raise HTTPException(status_code=400, detail="数据源不属于当前业务分析域")
    try:
        data = SourceDataService(db).get_remote_property_graphs(source_id, schema=schema)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取 Oracle Property Graph 失败: {str(exc)}")
    return ApiResponse(data=data)


@router.get("/skills", response_model=ApiResponse)
async def list_agent_skills(
    domain_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = AgentService(db)
    return ApiResponse(data=service.list_skills(domain_id=domain_id))


@router.get("/managed-skills", response_model=ApiResponse)
async def list_managed_agent_skills(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ApiResponse(data=AgentService(db).list_managed_skills())


@router.post("/managed-skills/upload", response_model=ApiResponse)
async def upload_managed_agent_skill(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        content = await file.read()
        data = AgentService(db).upload_managed_skill(file.filename or "agent_skill.zip", content, current_user.get("username", "unknown"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        await file.close()
    return ApiResponse(data=data, message="Agent Skill 已上传")


@router.delete("/managed-skills/{managed_skill_id}", response_model=ApiResponse)
async def delete_managed_agent_skill(
    managed_skill_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        AgentService(db).delete_managed_skill(managed_skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ApiResponse(message="Agent Skill 已删除")


@router.get("/managed-skill-test-sessions", response_model=ApiResponse)
async def list_managed_skill_test_sessions(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ApiResponse(data=AgentService(db).list_managed_skill_test_sessions())


@router.get("/managed-skill-test-sessions/{session_id}", response_model=ApiResponse)
async def get_managed_skill_test_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        return ApiResponse(data=AgentService(db).get_managed_skill_test_session(session_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/managed-skills/{managed_skill_id}/test", response_model=ApiResponse)
async def test_managed_agent_skill(
    managed_skill_id: str,
    req: AgentSkillTestRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        data = await AgentService(db).test_managed_skill(managed_skill_id, req.model_dump(), current_user.get("username", "unknown"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"托管 Skill 测试失败: {str(exc)}")
    return ApiResponse(data=data)


@router.get("/skills/{skill_id}", response_model=ApiResponse)
async def get_agent_skill(
    skill_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = AgentService(db)
    try:
        data = service.get_skill(skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ApiResponse(data=data)


@router.post("/domains/{domain_id}/skills", response_model=ApiResponse)
async def create_agent_skill(
    domain_id: str,
    req: AgentSkillCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = AgentService(db)
    try:
        data = await service.create_skill(domain_id, req.model_dump(), current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ApiResponse(data=data, message="技能已创建")


@router.put("/skills/{skill_id}", response_model=ApiResponse)
async def update_agent_skill(
    skill_id: str,
    req: AgentSkillUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = AgentService(db)
    try:
        data = await service.update_skill(skill_id, req.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ApiResponse(data=data, message="技能已更新")


@router.delete("/skills/{skill_id}", response_model=ApiResponse)
async def delete_agent_skill(
    skill_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = AgentService(db)
    try:
        service.delete_skill(skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ApiResponse(message="技能已删除")


@router.post("/skills/{skill_id}/package")
async def download_agent_skill_package(
    skill_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = AgentService(db)
    try:
        package = await service.build_skill_package(skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"生成技能包失败: {str(exc)}")
    return StreamingResponse(
        BytesIO(package["content"]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{package["filename"]}"'},
    )


@router.post("/skills/{skill_id}/test", response_model=ApiResponse)
async def test_agent_skill(
    skill_id: str,
    req: AgentSkillTestRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = AgentService(db)
    try:
        data = await service.test_skill(skill_id, req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"技能测试失败: {str(exc)}")
    return ApiResponse(data=data)
