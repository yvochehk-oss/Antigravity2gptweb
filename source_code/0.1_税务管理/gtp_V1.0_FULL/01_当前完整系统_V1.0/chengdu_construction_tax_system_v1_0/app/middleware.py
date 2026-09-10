"""Tax 全局认证与 CSRF 安全门禁。"""
from __future__ import annotations

import secrets
from collections.abc import Callable
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .observability import reset_actor, set_actor
from .auth import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    current_user_from_request,
)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

PUBLIC_EXACT_PATHS = frozenset(
    {
        "/login",
        "/healthz",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/api/v1/auth/token",
        "/api/v1/auth/refresh",
    }
)

PUBLIC_PREFIXES = ("/static", "/assets", "/ui")

API_PREFIXES = (
    "/api",
    "/rag-sync",
)


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_EXACT_PATHS:
        return True
    return any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in PUBLIC_PREFIXES
    )


def _is_api_path(path: str) -> bool:
    return any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in API_PREFIXES
    )


def _unauthenticated_requires_json(request: Request) -> bool:
    """匿名 API/AJAX 写请求必须返回 JSON 401，而不是 HTML 重定向。"""
    if _is_api_path(request.url.path):
        return True

    return request.method.upper() not in _SAFE_METHODS


from urllib.parse import parse_qs


async def _csrf_allowed(request: Request) -> tuple[bool, Request]:
    if request.method.upper() in _SAFE_METHODS:
        return True, request

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not cookie_token:
        return False, request

    request_token = (
        request.headers.get("X-CSRF-Token")
        or request.headers.get("X-XSRF-TOKEN")
    )

    if not request_token:
        content_type = request.headers.get("content-type", "").lower()
        if "application/x-www-form-urlencoded" in content_type:
            try:
                body = await request.body()
                parsed = parse_qs(body.decode("utf-8", errors="ignore"))
                token_list = parsed.get("_csrf") or parsed.get("csrf_token")
                if token_list and token_list[0]:
                    request_token = token_list[0]

                async def receive():
                    return {"type": "http.request", "body": body}

                request = Request(request.scope, receive=receive)
            except Exception:
                pass

    if not request_token:
        return False, request

    try:
        return secrets.compare_digest(cookie_token, str(request_token)), request
    except (TypeError, ValueError):
        return False, request


def _same_origin_ok(request: Request) -> bool:
    if request.method.upper() in _SAFE_METHODS:
        return True

    origin = request.headers.get("Origin")
    if not origin:
        return True

    host = request.headers.get("Host")
    if not host:
        return False

    try:
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"}:
            return False
        return parsed.netloc.lower() == host.lower()
    except (TypeError, ValueError):
        return False


def _bearer_only_request(request: Request) -> bool:
    """真正的 Bearer-only API 客户端不使用浏览器 Cookie，因此无需 CSRF。"""
    auth_header = request.headers.get("Authorization", "").strip()

    return (
        auth_header.lower().startswith("bearer ")
        and not request.cookies.get(COOKIE_NAME)
    )


def _attach_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


class AuthMiddleware(BaseHTTPMiddleware):
    """全局 Fail-Closed 身份认证门禁。

    规则：
    - 匿名 HTML GET -> 303 /login
    - 匿名 API/AJAX -> 401 JSON
    - 不存在 development 自动 admin
    - Session Cookie 写操作 -> Same-Origin + CSRF
    - Bearer-only API -> JWT 鉴权，不额外要求浏览器 CSRF
    - 保证 request.state.user 与 request.state.current_user 双向兼容
    - 全响应挂载安全响应头 (nosniff, DENY, same-origin)
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ):
        path = request.url.path
        method = request.method.upper()
        actor_token = set_actor("anonymous")

        try:
            # 登录页本身必须匿名可访问。
            if path == "/login":
                if method not in _SAFE_METHODS and not _same_origin_ok(request):
                    return _attach_security_headers(JSONResponse(
                        status_code=403,
                        content={"detail": "跨站请求被拒绝"},
                    ))
                response = await call_next(request)
                return _attach_security_headers(response)

            # 健康检查、API 文档和 JWT bootstrap 端点。
            if _is_public_path(path):
                response = await call_next(request)
                return _attach_security_headers(response)

            # 唯一身份来源：真实 Session Cookie 或真实 Bearer JWT。
            user = current_user_from_request(request)

            if user is None:
                if _unauthenticated_requires_json(request):
                    return _attach_security_headers(JSONResponse(
                        status_code=401,
                        content={"detail": "请先登录"},
                    ))

                return _attach_security_headers(RedirectResponse(
                    url="/login",
                    status_code=303,
                ))

            # 恢复并保证 user 与 current_user 兼容性
            request.state.user = user
            request.state.current_user = user
            username = str(
                getattr(user, "username", None)
                or getattr(user, "display_name", None)
                or "anonymous",
            )
            reset_actor(actor_token)
            actor_token = set_actor(username)

            # Bearer-only 客户端不依赖浏览器 Cookie，因此不受 CSRF 约束。
            if not _bearer_only_request(request):
                if not _same_origin_ok(request):
                    return _attach_security_headers(JSONResponse(
                        status_code=403,
                        content={"detail": "跨站请求被拒绝"},
                    ))

                csrf_ok, request = await _csrf_allowed(request)
                if not csrf_ok:
                    return _attach_security_headers(JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF 校验失败"},
                    ))

            response = await call_next(request)
            return _attach_security_headers(response)
        finally:
            reset_actor(actor_token)
