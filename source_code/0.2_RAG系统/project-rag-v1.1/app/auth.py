"""RAG 认证：仅验证 Tax 后端签发的 JWT，不持有用户表。

所有 token 由 Tax 后端通过 POST /api/v1/auth/token 签发。
RAG 只验证 JWT 签名，不签发 token、不存密码、不做用户管理。
"""
from __future__ import annotations

import os
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException, status, Request
from fastapi.responses import JSONResponse, RedirectResponse


# ---------------------------------------------------------------------------
# JWT 验证配置（与 Tax 后端保持一致）
# ---------------------------------------------------------------------------
_JWT_SECRET_ENV = "JWT_SECRET_KEY"
_JWT_ALGORITHM = "HS256"


def _get_jwt_secret() -> str:
    """从环境变量读取 JWT 密钥。生产环境必须设置。"""
    raw = os.getenv(_JWT_SECRET_ENV, "").strip()
    if not raw:
        # Development fallback: random per-process secret.
        # Tokens issued with this secret will be rejected in production.
        import secrets
        return secrets.token_urlsafe(48)
    return raw


def verify_tax_jwt(token: str) -> dict | None:
    secret = _get_jwt_secret()
    print(f"DEBUG RAG AUTH: secret='{secret}' token='{token[:15]}...'")
    try:
        payload = jwt.decode(token, secret, algorithms=[_JWT_ALGORITHM])
        if payload.get("type") != "access":
            print("DEBUG RAG AUTH: payload type is not access")
            return None
        print("DEBUG RAG AUTH: success payload=", payload)
        return payload
    except jwt.PyJWTError as e:
        print(f"DEBUG RAG AUTH: jwt error {e}")
        return None

# ---------------------------------------------------------------------------
# FastAPI 依赖
# ---------------------------------------------------------------------------

class TaxPrincipal:
    """从 Tax JWT 中解析出的认证主体。"""
    def __init__(self, payload: dict):
        self.user_id: int = int(payload["sub"])
        self.username: str = payload.get("username", "")
        self.role: str = payload.get("role", "")  # admin / operator
        self.display_name: str = payload.get("display_name", "")
        self.source = "tax_jwt"

    def is_admin(self) -> bool:
        return self.role == "admin"

    def is_operator(self) -> bool:
        return self.role == "operator"


def require_web_auth(request: Request) -> TaxPrincipal:
    """验证 Web Cookie 中的 JWT，失败则重定向到 /login。"""
    token = request.cookies.get("cdjg_rag_token")
    print(f"DEBUG COOKIE: {request.cookies.keys()}")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/login"}
        )
    payload = verify_tax_jwt(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/login"}
        )
    return TaxPrincipal(payload)


def require_auth(
    authorization: Optional[str] = Header(default=None),
) -> TaxPrincipal:
    """验证 Authorization Bearer token，返回 TaxPrincipal。"""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization 头部",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization 头部格式错误，必须为 Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()
    payload = verify_tax_jwt(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TaxPrincipal(payload)


def require_admin(
    authorization: Optional[str] = Header(default=None),
) -> TaxPrincipal:
    """验证 token 并确保角色为 admin。"""
    principal = require_auth(authorization)
    if not principal.is_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return principal


__all__ = ["TaxPrincipal", "require_auth", "require_admin", "require_web_auth", "verify_tax_jwt"]
