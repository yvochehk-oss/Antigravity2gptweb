"""认证、CSRF 和安全响应头中间件。

HTML navigation requests still redirect to the login page.  API callers get
machine-readable 401/403 responses so a fetch client never receives an HTML
login document by surprise.

The middleware also installs a per-request ``request_id`` (echoed via
``X-Request-ID``) so log lines, audit entries and outbound Tax→RAG calls
share a single trace identifier.
"""
from __future__ import annotations

import hmac
import os
from urllib.parse import parse_qs, urlsplit

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from .auth import CSRF_COOKIE_NAME, current_user_from_request
from .observability import (
    REQUEST_ID_HEADER,
    get_request_id,
    reset_actor,
    reset_request_id,
    set_actor,
    set_request_id,
)
from .structured_logging import resolve_request_id

# Only authentication, documentation, health and static-resource routes are
# public.  In particular, ``/api/projects`` is deliberately absent: project
# data and planning APIs must pass through the session/role checks below.
PUBLIC_EXACT_PATHS = frozenset({
    "/",
    "/login",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/healthz",
    "/favicon.ico",
})
PUBLIC_PREFIXES = (
    "/docs/",
    "/redoc/",
    "/avatars/",
    "/api/v1/auth/",
)


def _is_public_path(path: str) -> bool:
    """Return whether *path* belongs to a public route namespace.

    Exact paths and slash-delimited namespaces avoid the old ``startswith``
    boundary bug (for example ``/api/projects/1`` must never be public just
    because it starts with ``/api/projects``).
    """
    return path in PUBLIC_EXACT_PATHS or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 公开路径直接放行
        path = request.url.path
        # RequestIdMiddleware normally runs outside this layer.  Reuse its
        # resolved value so nested middleware never creates a second trace id;
        # keep the fallback for tests or alternate app wiring.
        request_id = getattr(request.state, "request_id", "") or resolve_request_id(
            request.headers.get(REQUEST_ID_HEADER),
        )
        id_token = set_request_id(request_id)
        actor_token = set_actor("anonymous")
        request.state.request_id = request_id
        try:
            if _is_public_path(path):
                response = await call_next(request)
                return self._security_headers(self._attach_request_id(request, response))

            # 尝试解析当前用户
            user = current_user_from_request(request)
            if user is None and os.getenv("APP_ENV", "development").lower() not in {"test", "production"}:
                from .models import User
                user = User(
                    id=1,
                    username="admin",
                    role="ADMIN",
                    display_name="系统管理员",
                    active=True,
                )

            # 已登录或演示环境免登：放行，并在 request.state 写入 user 供下游使用
            if user is not None:
                request.state.current_user = user
                username = str(
                    getattr(user, "username", None)
                    or getattr(user, "display_name", None)
                    or "anonymous",
                )
                reset_actor(actor_token)
                actor_token = set_actor(username)
                if not await self._csrf_allowed(request):
                    response = JSONResponse(
                        {"detail": "CSRF token 无效或来源不可信"}, status_code=403,
                    )
                    return self._security_headers(self._attach_request_id(request, response))
                response = await call_next(request)
                return self._security_headers(self._attach_request_id(request, response))

            # 未登录：HTML 页面 → 重定向，API 路径 → 401
            if path.startswith("/api") or path.startswith("/rag-sync"):
                response = JSONResponse({"detail": "请先登录"}, status_code=401)
                response.headers["WWW-Authenticate"] = "Session"
                return self._security_headers(self._attach_request_id(request, response))

            return self._security_headers(
                self._attach_request_id(
                    request, RedirectResponse(f"/login?next={path}", status_code=302),
                ),
            )
        finally:
            reset_actor(actor_token)
            reset_request_id(id_token)

    @staticmethod
    def _attach_request_id(request: Request, response):
        request_id = getattr(request.state, "request_id", "") or get_request_id()
        if request_id:
            response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @staticmethod
    def _same_origin(request: Request) -> bool:
        """Accept same-origin browser requests and non-browser test clients."""
        origin = request.headers.get("origin")
        if origin:
            actual = f"{request.url.scheme}://{request.url.netloc}"
            return hmac.compare_digest(origin.rstrip("/"), actual.rstrip("/"))
        referer = request.headers.get("referer")
        if referer:
            parsed = urlsplit(referer)
            actual = f"{request.url.scheme}://{request.url.netloc}"
            return hmac.compare_digest(
                f"{parsed.scheme}://{parsed.netloc}".rstrip("/"), actual.rstrip("/"),
            )
        # Non-browser API clients generally do not send Origin/Referer.  They
        # are still authenticated by the session cookie; CSRF attacks rely on
        # a browser sending a foreign origin, which is rejected above.
        return True

    @classmethod
    async def _csrf_allowed(cls, request: Request) -> bool:
        """Validate CSRF token when supplied, otherwise enforce same-origin."""
        if request.method.upper() in {"GET", "HEAD", "OPTIONS", "TRACE"}:
            return True
        # Login is intentionally public and logout remains compatible with
        # existing browser links; both are still protected by same-origin.
        supplied = request.headers.get("X-CSRF-Token", "")
        if not supplied:
            supplied = request.headers.get("X-XSRF-TOKEN", "")
        if not supplied and request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded",
        ):
            # ``body()`` caches and replays the bytes for downstream FastAPI
            # form parsing; ``form()`` here would consume the receive channel.
            try:
                raw = await request.body()
                values = parse_qs(raw.decode("utf-8", errors="ignore"), keep_blank_values=True)
                supplied = (values.get("_csrf") or [""])[0]
            except Exception:
                supplied = ""
        # Do not consume request.form() in middleware: BaseHTTPMiddleware may
        # wrap the receive channel and downstream FastAPI handlers must still
        # be able to parse their form fields.  Browser requests are protected
        # by the Origin/Referer same-origin check below; API clients can send
        # the token in X-CSRF-Token/X-XSRF-TOKEN.
        # The origin check is intentionally evaluated first.  A valid token
        # alone must not turn a cross-origin state change into an allowed
        # request (for example, when a test client or an embedded browser
        # happens to carry both cookies and headers).
        if not cls._same_origin(request):
            return False
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
        if supplied and cookie_token:
            return hmac.compare_digest(supplied, cookie_token)
        return True

    @staticmethod
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response
