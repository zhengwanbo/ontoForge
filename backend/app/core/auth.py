from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from app.models.models import SysUserDomainPermission
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.security import decode_access_token

security = HTTPBearer()


def normalize_role(role: object) -> str:
    """Return a stable role value for legacy databases with mixed casing."""
    return str(role or "").strip().lower()


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="无效的认证令牌")
    return payload


async def require_admin(current_user: dict = Depends(get_current_user)):
    if normalize_role(current_user.get("role")) != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user


def is_admin(current_user: dict) -> bool:
    """Administrators are intentionally not constrained by domain grants."""
    return normalize_role(current_user.get("role")) == "admin"


def get_authorized_domain_ids(db: Session, current_user: dict) -> list[str] | None:
    """Return None for all-domain access, otherwise the user's explicit grants."""
    if is_admin(current_user):
        return None
    user_id = current_user.get("user_id")
    if not user_id:
        return []
    return [
        row.domain_id
        for row in db.query(SysUserDomainPermission.domain_id)
        .filter(SysUserDomainPermission.user_id == user_id)
        .all()
    ]


def ensure_domain_access(db: Session, current_user: dict, domain_id: str) -> None:
    """Reject direct API access to a domain not granted to the current user."""
    if not domain_id or is_admin(current_user):
        return
    allowed_domain_ids = get_authorized_domain_ids(db, current_user) or []
    if domain_id not in allowed_domain_ids:
        raise HTTPException(status_code=403, detail="当前用户无权访问该业务分析域")
