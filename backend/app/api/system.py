from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import get_current_user, normalize_role, require_admin
from app.core.security import get_password_hash, verify_password, create_access_token
from app.schemas.schemas import (
    ApiResponse, LoginRequest, LoginResponse, PasswordChangeRequest,
    UserCreate, UserUpdate, UserResponse,
    LLMConfigCreate, LLMConfigUpdate, LLMConfigResponse
)
from app.models.models import SysUser, SysLLMConfig, SysOperationLog, generate_id
from app.services.llm_service import normalize_model_name

router = APIRouter(prefix="/system", tags=["系统管理"])


def _validate_llm_limits(max_tokens: int | None, context_window_tokens: int | None) -> None:
    if context_window_tokens is None:
        return
    if context_window_tokens <= 0:
        raise HTTPException(
            status_code=400,
            detail="最大Token必须大于 0。",
        )


# ====== 认证 ======

@router.post("/auth/login", response_model=ApiResponse)
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(SysUser).filter(SysUser.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if user.status != "ACTIVE":
        raise HTTPException(status_code=403, detail="用户已被禁用")

    role = normalize_role(user.role)
    token = create_access_token({"user_id": user.user_id, "username": user.username, "role": role})
    return ApiResponse(data=LoginResponse(
        access_token=token,
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        role=role
    ).model_dump())


@router.put("/auth/password", response_model=ApiResponse)
async def change_password(
    req: PasswordChangeRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if req.new_password != req.confirm_password:
        raise HTTPException(status_code=400, detail="新密码与确认密码不一致")

    user = db.query(SysUser).filter(SysUser.user_id == current_user.get("user_id")).first()
    if not verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码错误")

    user.password_hash = get_password_hash(req.new_password)
    user.updated_at = datetime.utcnow()
    db.commit()
    return ApiResponse(message="密码已修改")


# ====== 用户管理 ======

@router.get("/users", response_model=ApiResponse)
async def list_users(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    users = db.query(SysUser).order_by(SysUser.created_at).all()
    data = [UserResponse(
        user_id=u.user_id,
        username=u.username,
        display_name=u.display_name,
        email=u.email,
        role=u.role,
        status=u.status,
        created_at=u.created_at
    ).model_dump() for u in users]
    return ApiResponse(data=data)


@router.post("/users", response_model=ApiResponse)
async def create_user(
    req: UserCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    existing = db.query(SysUser).filter(SysUser.username == req.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")

    user = SysUser(
        user_id=generate_id("usr"),
        username=req.username,
        display_name=req.display_name,
        email=req.email,
        password_hash=get_password_hash(req.password),
        role=req.role,
        status="ACTIVE"
    )
    db.add(user)
    db.commit()
    return ApiResponse(data=UserResponse(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=user.role,
        status=user.status,
        created_at=user.created_at
    ).model_dump())


@router.put("/users/{user_id}", response_model=ApiResponse)
async def update_user(
    user_id: str,
    req: UserUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    user = db.query(SysUser).filter(SysUser.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    for field, value in req.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    user.updated_at = datetime.utcnow()
    db.commit()
    return ApiResponse(message="用户已更新")


@router.delete("/users/{user_id}", response_model=ApiResponse)
async def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    user = db.query(SysUser).filter(SysUser.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.status = "INACTIVE"
    user.updated_at = datetime.utcnow()
    db.commit()
    return ApiResponse(message="用户已禁用")


# ====== LLM配置管理 ======

@router.get("/llm-configs", response_model=ApiResponse)
async def list_llm_configs(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    configs = db.query(SysLLMConfig).order_by(SysLLMConfig.created_at).all()
    data = [LLMConfigResponse(
        config_id=c.config_id,
        config_name=c.config_name,
        api_base_url=c.api_base_url,
        api_key_display=c.api_key_enc[:8] + "..." if c.api_key_enc else "",
        model_name=c.model_name,
        temperature=c.temperature,
        max_tokens=c.max_tokens,
        context_window_tokens=c.context_window_tokens,
        timeout=c.timeout,
        is_active=c.is_active,
        is_default=c.is_default,
        created_at=c.created_at
    ).model_dump() for c in configs]
    return ApiResponse(data=data)


@router.post("/llm-configs", response_model=ApiResponse)
async def create_llm_config(
    req: LLMConfigCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    _validate_llm_limits(req.max_tokens, req.context_window_tokens)
    config = SysLLMConfig(
        config_id=generate_id("llm"),
        config_name=req.config_name,
        api_base_url=req.api_base_url,
        api_key_enc=req.api_key,  # In production, encrypt this
        model_name=normalize_model_name(req.model_name, req.api_base_url),
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        context_window_tokens=req.context_window_tokens,
        timeout=req.timeout,
        is_default="Y" if req.is_default else "N"
    )

    # If this is set as default, unset others
    if req.is_default:
        db.query(SysLLMConfig).update({SysLLMConfig.is_default: "N"})

    db.add(config)
    db.commit()
    return ApiResponse(data={"config_id": config.config_id})


@router.put("/llm-configs/{config_id}", response_model=ApiResponse)
async def update_llm_config(
    config_id: str,
    req: LLMConfigUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    config = db.query(SysLLMConfig).filter(SysLLMConfig.config_id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    next_max_tokens = req.max_tokens if req.max_tokens is not None else config.max_tokens
    next_context_window = req.context_window_tokens if req.context_window_tokens is not None else config.context_window_tokens
    _validate_llm_limits(next_max_tokens, next_context_window)

    for field, value in req.model_dump(exclude_unset=True).items():
        if field == "is_active":
            config.is_active = "Y" if value else "N"
        elif field == "is_default":
            if value:
                db.query(SysLLMConfig).update({SysLLMConfig.is_default: "N"})
            config.is_default = "Y" if value else "N"
        elif field == "api_key":
            config.api_key_enc = value  # In production, encrypt
        elif field == "model_name":
            config.model_name = normalize_model_name(
                value,
                req.api_base_url if req.api_base_url is not None else config.api_base_url
            )
        elif field == "api_base_url":
            config.api_base_url = value
            config.model_name = normalize_model_name(config.model_name, value)
        else:
            setattr(config, field, value)

    db.commit()
    return ApiResponse(message="配置已更新")


@router.delete("/llm-configs/{config_id}", response_model=ApiResponse)
async def delete_llm_config(
    config_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    config = db.query(SysLLMConfig).filter(SysLLMConfig.config_id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    db.delete(config)
    db.commit()
    return ApiResponse(message="配置已删除")


@router.post("/llm-configs/{config_id}/test", response_model=ApiResponse)
async def test_llm_connection(
    config_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """测试LLM连接"""
    config = db.query(SysLLMConfig).filter(SysLLMConfig.config_id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    from app.services.llm_service import LLMService
    llm_service = LLMService(db)
    result = await llm_service.test_connection(config)
    return ApiResponse(data=result)


# ====== 操作日志 ======

@router.get("/operation-logs", response_model=ApiResponse)
async def list_operation_logs(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    logs = db.query(SysOperationLog).order_by(
        SysOperationLog.created_at.desc()
    ).limit(limit).all()

    data = [{
        "log_id": l.log_id,
        "operator": l.operator,
        "operation_type": l.operation_type,
        "operation_target": l.operation_target,
        "operation_detail": l.operation_detail,
        "created_at": l.created_at.isoformat() if l.created_at else None
    } for l in logs]

    return ApiResponse(data=data)
