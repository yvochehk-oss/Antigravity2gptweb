"""RAG 认证路由。

Tax 是唯一的用户认证和 JWT 签发者。RAG 只验证 Tax 签发的 JWT；浏览器
登录页通过本模块的同源 session bridge 把一次性凭据转发给 Tax，并把
Tax 返回的 access token 放进 HttpOnly cookie。RAG 不持有用户表、不验密码、
也不向浏览器返回或记录 token。
"""
from __future__ import annotations

import ipaddress
import os
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from ..auth import verify_tax_jwt
from ..security import require_same_origin

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_DEFAULT_TAX_AUTH_SERVICE_URL = "http://127.0.0.1:8921/api/v1/auth/token"
_TAX_AUTH_URL_ENV = "TAX_AUTH_SERVICE_URL"
_COOKIE_NAME = "cdjg_rag_token"
_DEFAULT_TOKEN_MAX_AGE = 60 * 60
_MAX_TOKEN_MAX_AGE = 7 * 24 * 60 * 60
_MAX_CREDENTIAL_LENGTH = 1024
# Backwards-compatible private alias for existing tests/callers.  The actual
# implementation lives in ``app.security`` so every cookie-backed mutation
# uses exactly the same fail-closed policy.
_require_same_origin = require_same_origin


def _tax_auth_service_url() -> str:
    """Return the server-side Tax token endpoint after URL validation.

    The URL is intentionally read from process configuration only. It is
    never taken from request parameters, headers, or browser storage. HTTP is
    accepted only for loopback development, while non-loopback endpoints must
    use HTTPS. Direct private/reserved IP literals and cloud metadata hosts
    are rejected to keep a misconfigured value from becoming an SSRF pivot.
    """
    raw = os.getenv(_TAX_AUTH_URL_ENV, _DEFAULT_TAX_AUTH_SERVICE_URL).strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Tax auth service URL must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Tax auth service URL must not contain credentials or query parameters")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Tax auth service URL has an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Tax auth service URL has an invalid port")

    host = parsed.hostname.rstrip(".").lower()
    loopback_hosts = {"localhost", "127.0.0.1", "::1"}
    metadata_hosts = {
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "instance-data.ec2.internal",
    }
    if host in metadata_hosts:
        raise ValueError("Tax auth service metadata hosts are not allowed")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        if host not in loopback_hosts:
            raise ValueError("Tax auth service cannot target a private or reserved address")

    if host not in loopback_hosts and parsed.scheme != "https":
        raise ValueError("Non-loopback Tax auth service must use HTTPS")

    path = parsed.path.rstrip("/")
    if not path:
        path = "/api/v1/auth/token"
    if path != "/api/v1/auth/token":
        raise ValueError("Tax auth service URL must target /api/v1/auth/token")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _safe_token_max_age(raw_value: object) -> int:
    """Parse an upstream expiry into a bounded cookie lifetime.

    The value is advisory metadata from Tax, not a browser-controlled value.
    Invalid, boolean, zero, negative, or excessively large values use a safe
    one-hour default/cap rather than reaching Starlette's Set-Cookie builder.
    """
    if isinstance(raw_value, bool):
        return _DEFAULT_TOKEN_MAX_AGE
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return _DEFAULT_TOKEN_MAX_AGE
    if value <= 0:
        return _DEFAULT_TOKEN_MAX_AGE
    return min(value, _MAX_TOKEN_MAX_AGE)


def _secure_cookie(request: Request) -> bool:
    """Use HTTPS automatically, with an explicit deployment override."""
    configured = os.getenv("RAG_COOKIE_SECURE", "").strip().lower()
    if configured in {"1", "true", "yes", "on"}:
        return True
    if configured in {"0", "false", "no", "off"}:
        return False
    return request.url.scheme.lower() == "https"


async def _read_login_credentials(request: Request) -> tuple[str, str]:
    """Read credentials from a POST body, never from the query string."""
    if request.query_params.get("username") is not None or request.query_params.get("password") is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名和密码必须通过 POST 请求体提交",
        )

    content_type = request.headers.get("content-type", "").lower()
    try:
        if "application/json" in content_type:
            payload = await request.json()
            username = payload.get("username", "") if isinstance(payload, dict) else ""
            password = payload.get("password", "") if isinstance(payload, dict) else ""
        elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await request.form()
            username = form.get("username", "")
            password = form.get("password", "")
        else:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="请使用 JSON 或表单请求体提交登录凭据",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="登录请求格式无效",
        ) from exc

    username = username.strip() if isinstance(username, str) else ""
    password = password if isinstance(password, str) else ""
    if not username or not password or len(username) > _MAX_CREDENTIAL_LENGTH or len(password) > _MAX_CREDENTIAL_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="用户名或密码无效",
        )
    return username, password


@router.post("/session")
async def create_session(request: Request):
    """Create a same-origin RAG browser session using Tax authentication.

    This endpoint deliberately returns only an authentication acknowledgement.
    The Tax access token is kept in an HttpOnly cookie and is never exposed to
    browser JavaScript or included in an error response.
    """
    _require_same_origin(request)
    username, password = await _read_login_credentials(request)
    try:
        tax_url = _tax_auth_service_url()
    except ValueError:
        # Configuration details are server-side operational data; do not echo
        # the configured URL to an unauthenticated client.
        return JSONResponse(
            {"detail": "认证服务配置不可用"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            upstream = await client.post(
                tax_url,
                data={"username": username, "password": password},
                headers={"Accept": "application/json"},
            )
    except httpx.RequestError:
        return JSONResponse(
            {"detail": "认证服务暂时不可用，请稍后重试"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if upstream.status_code in {401, 403}:
        return JSONResponse(
            {"detail": "用户名或密码错误"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if upstream.status_code < 200 or upstream.status_code >= 300:
        return JSONResponse(
            {"detail": "认证服务暂时不可用，请稍后重试"},
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    try:
        payload = upstream.json()
    except (TypeError, ValueError):
        return JSONResponse(
            {"detail": "认证服务响应无效"},
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    access_token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(access_token, str) or not access_token.strip() or len(access_token) > 8192:
        return JSONResponse(
            {"detail": "认证服务响应无效"},
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        key=_COOKIE_NAME,
        value=access_token,
        max_age=_safe_token_max_age(payload.get("expires_in")),
        path="/",
        secure=_secure_cookie(request),
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    """Clear the browser session and return the user to the login page.

    Logout is deliberately POST-only.  The browser sends the HttpOnly cookie
    automatically, while the response expires it without ever exposing its
    value to the page or to JavaScript.
    """
    _require_same_origin(request)
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(
        key=_COOKIE_NAME,
        path="/",
        secure=_secure_cookie(request),
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/verify")
def api_verify(authorization: Optional[str] = Header(default=None)):
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
