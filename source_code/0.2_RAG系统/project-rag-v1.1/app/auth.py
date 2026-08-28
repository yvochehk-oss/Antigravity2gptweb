"""RAG 认证：仅验证 Tax 后端签发的 JWT，不持有用户表。

所有 token 由 Tax 后端通过 POST /api/v1/auth/token 签发。
RAG 只验证 JWT 签名，不签发 token、不存密码、不做用户管理。
"""
from __future__ import annotations

import json
import os
import secrets
from typing import Any, Optional

import jwt
from fastapi import Depends, Header, HTTPException, Request, status

# ---------------------------------------------------------------------------
# JWT 验证配置（与 Tax 后端保持一致）
# ---------------------------------------------------------------------------
_JWT_SECRET_ENV = "JWT_SECRET_KEY"
_JWT_ALGORITHM = "HS256"
_PROTECTED_ENVIRONMENTS = frozenset({"production", "prod", "staging", "stage", "preprod", "pre-production"})

# A development/test process may deliberately omit a JWT secret.  Keep one
# process-local value instead of generating a new value on every verification
# call (which made every token fail after the first call).  Production never
# uses this value: it fails closed when the process environment is missing the
# Tax-issued JWT secret.
_DEV_JWT_SECRET = secrets.token_urlsafe(48)


def _get_jwt_secret() -> str:
    """从环境变量读取 JWT 密钥。生产环境必须设置。"""
    raw = os.getenv(_JWT_SECRET_ENV, "").strip()
    if not raw:
        try:
            from .config import BASE_DIR
            from dotenv import load_dotenv
            load_dotenv(BASE_DIR / ".env")
            raw = os.getenv(_JWT_SECRET_ENV, "").strip()
        except Exception:
            pass
    if not raw:
        environment = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
        if environment in _PROTECTED_ENVIRONMENTS:
            return ""
        return _DEV_JWT_SECRET
    return raw


def verify_tax_jwt(token: str) -> dict[str, Any] | None:
    """Verify an access JWT issued by the Tax service.

    The RAG service is a verifier only.  It does not mint tokens or keep a
    user table.  Invalid/missing configuration is intentionally represented as
    ``None`` at this boundary so callers return a normal 401 rather than a
    diagnostic 500.  In particular, secrets and token contents are never
    written to logs.
    """
    if not isinstance(token, str) or not token.strip():
        return None
    secret = _get_jwt_secret()
    if not secret:
        return None
    try:
        payload = jwt.decode(token.strip(), secret, algorithms=[_JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        if not isinstance(payload.get("sub"), (str, int)):
            return None
        return payload
    except (jwt.PyJWTError, TypeError, ValueError):
        return None

# ---------------------------------------------------------------------------
# FastAPI 依赖
# ---------------------------------------------------------------------------

class TaxPrincipal:
    """从 Tax JWT 中解析出的认证主体。"""
    def __init__(self, payload: dict, *, source: str = "tax_jwt"):
        try:
            self.user_id: int = int(payload["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("JWT subject must be an integer") from exc
        self.username: str = str(payload.get("username") or "")
        self.role: str = str(payload.get("role") or "")  # admin / operator
        self.display_name: str = str(payload.get("display_name") or "")
        # The source is assigned by the verifier, never trusted from an
        # arbitrary JWT claim.  This keeps response metadata honest.
        self.source = source

    def is_admin(self) -> bool:
        return self.role == "admin"

    def is_operator(self) -> bool:
        return self.role == "operator"


class ServicePrincipal:
    """Marker principal for an explicitly configured service-key caller."""

    role = "service"
    source = "shared_api_key"


def require_web_auth(request: Request) -> TaxPrincipal:
    """验证 Web Cookie 中的 JWT，失败则重定向到 /login。"""
    token = request.cookies.get("cdjg_rag_token")
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
    try:
        return TaxPrincipal(payload)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/login"},
        ) from None


# Marker consumed by the security middleware's route-aware policy.  A path
# allow-list alone must never make an unrelated handler cookie-addressable.
require_web_auth.__web_auth_boundary__ = True


def _web_cookie_principal(request: Request) -> TaxPrincipal:
    """Validate the HttpOnly browser cookie without accepting API headers."""
    token = request.cookies.get("cdjg_rag_token", "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少有效的 Web 会话",
        )
    payload = verify_tax_jwt(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Web 会话无效或已过期",
        )
    try:
        return TaxPrincipal(payload)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Web 会话无效或已过期",
        ) from None


def require_web_write(request: Request) -> TaxPrincipal:
    """Require a valid browser session for a cookie-backed mutation.

    Unlike ``require_web_auth`` this dependency never redirects.  Mutation
    callers receive an explicit 401/403 response, which prevents a failed
    form/fetch request from being mistaken for a successful login redirect.
    Role checks are layered through ``require_web_role`` below.
    """
    return _web_cookie_principal(request)


def require_web_role(*roles: str):
    """Return a Web mutation dependency restricted to the supplied roles."""
    allowed_roles = frozenset(
        role.strip().lower()
        for role in roles
        if isinstance(role, str) and role.strip()
    )
    if not allowed_roles:
        raise ValueError("require_web_role requires at least one non-empty role")

    def dependency(request: Request) -> TaxPrincipal:
        principal = require_web_write(request)
        if principal.role.strip().lower() not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "WEB_ROLE_FORBIDDEN",
                    "message": "当前角色无权执行该 Web 操作",
                    "required_roles": sorted(allowed_roles),
                },
            )
        return principal

    dependency.__web_auth_boundary__ = True
    return dependency


def _service_key_is_valid(request: Request) -> bool:
    # Import lazily to avoid an auth/security import cycle during bootstrap.
    from .security import is_valid_shared_api_key

    return is_valid_shared_api_key(request)


def require_web_or_service_read(request: Request) -> TaxPrincipal | ServicePrincipal:
    """Allow a precise compatibility GET through either trusted channel.

    A valid configured service key preserves the internal bridge contract. If
    it is absent or invalid, the request must carry the HttpOnly Tax JWT
    browser cookie. No Authorization JWT is accepted as a Web cookie.
    """
    if _service_key_is_valid(request):
        return ServicePrincipal()
    return _web_cookie_principal(request)


require_web_or_service_read.__web_auth_boundary__ = True


def require_web_or_service_role(*roles: str):
    """Dual-channel mutation dependency with role and CSRF enforcement.

    Service-key callers are trusted internal callers and do not need a Web
    cookie or browser CSRF metadata. Browser callers must satisfy the Tax JWT
    Cookie role gate and the shared same-origin CSRF helper. An arbitrary
    header, unknown key, invalid cookie, low-privilege role, or cross-origin
    request is rejected.
    """
    allowed_roles = frozenset(
        role.strip().lower()
        for role in roles
        if isinstance(role, str) and role.strip()
    )
    if not allowed_roles:
        raise ValueError("require_web_or_service_role requires at least one non-empty role")

    def dependency(request: Request) -> TaxPrincipal | ServicePrincipal:
        if _service_key_is_valid(request):
            return ServicePrincipal()

        principal = _web_cookie_principal(request)
        if principal.role.strip().lower() not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "WEB_ROLE_FORBIDDEN",
                    "message": "当前角色无权执行该 Web 操作",
                    "required_roles": sorted(allowed_roles),
                },
            )

        from .security import require_same_origin

        require_same_origin(request)
        return principal

    dependency.__web_auth_boundary__ = True
    return dependency


def require_auth(
    authorization: Optional[str] = Header(default=None),
) -> TaxPrincipal:
    """验证 Authorization Bearer token，返回 TaxPrincipal。"""
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization 头部",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = verify_tax_jwt(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return TaxPrincipal(payload)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


def require_admin(principal: TaxPrincipal = Depends(require_auth)) -> TaxPrincipal:
    """验证 token 并确保角色为 admin。"""
    if not principal.is_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return principal


def _bearer_token(authorization: Optional[str]) -> str | None:
    """Return a non-empty Bearer credential, or ``None`` for malformed input."""
    if not authorization:
        return None
    scheme, separator, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not credentials.strip():
        return None
    return credentials.strip()


def _json_token_roles(name: str) -> dict[str, str]:
    """Read the legacy test harness token map without exposing its values.

    Older, pre-Tax-JWT executive tests supplied an explicit in-memory token
    map.  Keeping this compatibility path limited to ``APP_ENV=test`` lets
    those tests continue to exercise 401/403 behavior while production and
    development always use the Tax JWT contract.  It is not a user store and
    it is never consulted for a real deployment.
    """
    if os.getenv("APP_ENV", "").strip().lower() != "test":
        return {}
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(token): str(role)
        for token, role in parsed.items()
        if isinstance(token, str) and token and isinstance(role, str) and role
    }


def _legacy_exec_principal(token: str, allowed_roles: frozenset[str]) -> TaxPrincipal | None:
    """Resolve only explicitly configured test-only executive credentials."""
    if os.getenv("APP_ENV", "").strip().lower() != "test":
        return None

    for token_candidate, role in _json_token_roles("RAG_EXEC_TOKENS").items():
        if secrets.compare_digest(token, token_candidate):
            return TaxPrincipal(
                {
                    "sub": "0",
                    "username": "test-executive",
                    "role": role,
                    "type": "access",
                },
                source="test_legacy",
            )

    for token_candidate, role in _json_token_roles("RAG_EXEC_DEMO_TOKENS").items():
        if secrets.compare_digest(token, token_candidate):
            return TaxPrincipal(
                {
                    "sub": "0",
                    "username": "test-executive",
                    "role": role,
                    "type": "access",
                },
                source="test_legacy_demo",
            )

    internal = os.getenv("RAG_EXEC_INTERNAL_TOKEN", "").strip()
    if internal and secrets.compare_digest(token, internal):
        # The test-only service credential models an explicitly configured
        # internal caller.  Assign the first requested role so the normal
        # role gate is still executed; it is not available outside tests.
        role = next(iter(allowed_roles), "")
        return TaxPrincipal(
            {
                "sub": "0",
                "username": "test-internal",
                "role": role,
                "type": "access",
            },
            source="test_internal",
        )
    return None


def require_exec_role(*roles: str):
    """FastAPI dependency factory for executive/RAG RBAC boundaries.

    Callers must explicitly name the Tax roles allowed for an endpoint, for
    example ``Depends(require_exec_role("admin", "operator"))``.  The
    dependency always authenticates a Tax JWT first and returns 401 for a
    missing/invalid credential or 403 for a valid but disallowed role.  A
    narrowly scoped test-only compatibility map is retained for the legacy
    executive contract; it is disabled in every non-test environment.
    """
    allowed_roles = frozenset(role.strip() for role in roles if isinstance(role, str) and role.strip())
    if not allowed_roles:
        raise ValueError("require_exec_role requires at least one non-empty role")

    def dependency(
        authorization: Optional[str] = Header(default=None),
    ) -> TaxPrincipal:
        token = _bearer_token(authorization)
        if token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="缺少或格式错误的 Authorization Bearer 头部",
                headers={"WWW-Authenticate": "Bearer"},
            )

        payload = verify_tax_jwt(token)
        principal: TaxPrincipal | None = None
        legacy_internal = False
        if payload is not None:
            try:
                principal = TaxPrincipal(payload)
            except ValueError:
                principal = None
        if principal is None:
            principal = _legacy_exec_principal(token, allowed_roles)
            legacy_internal = principal is not None and principal.source == "test_internal"
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token 无效或已过期",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if principal.role not in allowed_roles and not legacy_internal:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "EXEC_ROLE_FORBIDDEN",
                    "message": "当前角色无权访问该执行端点",
                    "required_roles": sorted(allowed_roles),
                },
            )
        return principal

    return dependency


__all__ = [
    "TaxPrincipal",
    "ServicePrincipal",
    "require_auth",
    "require_admin",
    "require_exec_role",
    "require_web_auth",
    "require_web_role",
    "require_web_or_service_read",
    "require_web_or_service_role",
    "require_web_write",
    "verify_tax_jwt",
]
