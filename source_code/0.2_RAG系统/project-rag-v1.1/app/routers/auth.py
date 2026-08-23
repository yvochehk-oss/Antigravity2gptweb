"""RAG 认证路由：占位，所有认证逻辑由 Tax 后端处理。

RAG 不持有用户表，不签发 token，不处理登录。
保留 /verify 端点用于内部 token 自检，其他 auth 端点已废弃。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from ..auth import require_auth, verify_tax_jwt

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/verify")
def api_verify(authorization: str = None):
    """验证 Authorization Bearer token 并返回用户信息。

    内部使用，正式验证请调用 Tax 后端 /api/v1/auth/verify。
    """
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    token = authorization[7:].strip()
    payload = verify_tax_jwt(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return JSONResponse({
        "valid": True,
        "user_id": int(payload["sub"]),
        "username": payload.get("username", ""),
        "role": payload.get("role", ""),
        "display_name": payload.get("display_name", ""),
    })
