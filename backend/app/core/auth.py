from fastapi import Request, HTTPException, Depends
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
