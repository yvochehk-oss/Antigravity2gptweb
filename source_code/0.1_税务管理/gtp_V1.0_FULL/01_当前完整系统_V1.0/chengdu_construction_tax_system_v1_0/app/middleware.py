"""Tax 全局认证与 CSRF 安全门禁。"""
from __future__ import annotations

import secrets
from collections.abc import Callable
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    current_user_from_request,
)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# 仅保留真正需要匿名访问的入口。
#
# /api/v1/auth/token 与 /refresh 是移动端 JWT 登录引导接口，
# 必须公开，否则 Boss App / API 客户端永远无法取得令牌。
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

# 登录页当前不依赖 SPA assets；保留 /static 仅用于兼容必要静态资源。
PUBLIC_PREFIXES = ("/static",)

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

    # /ai-review/run、/health-check/run、/manager/.../ask 等历史接口
    # 虽没有 /api 前缀，但属于 AJAX 写接口。
    return request.method.upper() not in _SAFE_METHODS


def _csrf_allowed(request: Request) -> bool:
    if request.method.upper() in _SAFE_METHODS:
        return True

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    request_token = (
        request.headers.get("X-CSRF-Token")
        or request.headers.get("X-XSRF-TOKEN")
    )

    if not cookie_token or not request_token:
        return False

    try:
        return secrets.compare_digest(cookie_token, request_token)
    except (TypeError, ValueError):
        return False


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


class AuthMiddleware(BaseHTTPMiddleware):
    """全局 Fail-Closed 身份认证门禁。

    规则：
    - 匿名 HTML GET -> 303 /login
    - 匿名 API/AJAX -> 401 JSON
    - 不存在 development 自动 admin
    - Session Cookie 写操作 -> Same-Origin + CSRF
    - Bearer-only API -> JWT 鉴权，不额外要求浏览器 CSRF
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ):
        path = request.url.path
        method = request.method.upper()

        # 登录页本身必须匿名可访问。
        # POST /login 的 CSRF 双提交校验由登录路由完成；
        # 这里额外执行 Origin 边界检查。
        if path == "/login":
            if method not in _SAFE_METHODS and not _same_origin_ok(request):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "跨站请求被拒绝"},
                )
            return await call_next(request)

        # 健康检查、API 文档和 JWT bootstrap 端点。
        if _is_public_path(path):
            return await call_next(request)

        # 唯一身份来源：真实 Session Cookie 或真实 Bearer JWT。
        # 严禁任何 development/admin 自动兜底。
        user = current_user_from_request(request)

        if user is None:
            if _unauthenticated_requires_json(request):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "请先登录"},
                )

            return RedirectResponse(
                url="/login",
                status_code=303,
            )

        request.state.user = user

        # Bearer-only 客户端不依赖浏览器 Cookie，因此不受 CSRF 约束。
        if not _bearer_only_request(request):
            if not _same_origin_ok(request):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "跨站请求被拒绝"},
                )

            if not _csrf_allowed(request):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF 校验失败"},
                )

        return await call_next(request)
