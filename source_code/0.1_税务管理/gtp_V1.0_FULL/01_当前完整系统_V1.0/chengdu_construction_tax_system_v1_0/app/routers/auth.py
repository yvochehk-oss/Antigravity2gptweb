"""认证路由：GET /login, POST /login, POST /logout, /api/v1/auth/token, /api/v1/auth/refresh, /api/v1/auth/verify。
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from ..auth import (
    COOKIE_MAX_AGE,
    COOKIE_SECURE,
    CSRF_COOKIE_NAME,
    clear_session,
    create_session,
    issue_jwt,
    refresh_access_token,
    verify_jwt,
    _auth_source_for_user,
)
from ..auth import login as auth_login
from ..templates import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    error = request.query_params.get("error", "")
    csrf_token = secrets.token_urlsafe(32)
    request.state.csrf_token = csrf_token

    response = templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "error": error,
            "csrf_token": csrf_token,
        },
    )

    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=COOKIE_MAX_AGE,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite="lax",
    )

    return response


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(default="", alias="_csrf"),
) -> RedirectResponse:
    cookie_csrf = request.cookies.get(CSRF_COOKIE_NAME, "")

    if (
        not cookie_csrf
        or not csrf
        or not secrets.compare_digest(cookie_csrf, csrf)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF 校验失败",
        )
    user = auth_login(username, password)
    if user is None:
        return RedirectResponse(
            "/login?error=用户名或密码错误",
            status_code=302,
        )
    response = RedirectResponse("/", status_code=302)
    create_session(response, user.id, _auth_source_for_user(user))
    return response


@router.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse("/login", status_code=302)
    # GET remains a compatibility navigation endpoint but is intentionally
    # side-effect free.  Session invalidation is a POST protected by the
    # authentication/CSRF middleware.
    if request.method.upper() == "POST":
        clear_session(response)
    return response


# ---------------------------------------------------------------------------
# 移动端 / API JWT 令牌端点（不依赖 Session Cookie）
# ---------------------------------------------------------------------------
_api_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class TokenRequest(BaseModel):
    username: str
    password: str

@_api_router.post("/token")
async def api_token(
    request: Request,
) -> JSONResponse:
    """移动端登录：验证用户名密码后签发 JWT access + refresh token。"""
    content_type = request.headers.get("content-type", "")
    username = ""
    password = ""
    if "application/json" in content_type:
        try:
            body = await request.json()
            username = body.get("username", "")
            password = body.get("password", "")
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")
    else:
        # Fallback to query params or json
        username = request.query_params.get("username", "")
        password = request.query_params.get("password", "")
        if not username:
            try:
                body = await request.json()
                username = body.get("username", "")
                password = body.get("password", "")
            except Exception:
                pass

    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="缺少用户名或密码",
        )

    user = auth_login(username, password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    access_token, refresh_token = issue_jwt(user)
    return JSONResponse({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": 60 * 60,
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "display_name": user.display_name,
        },
    })


@_api_router.post("/refresh")
async def api_refresh(request: Request) -> JSONResponse:
    """用 refresh token 换新 access + refresh token 对，支持 JSON Body、Form 或 Query 参数。"""
    content_type = request.headers.get("content-type", "")
    token = ""
    if "application/json" in content_type:
        try:
            body = await request.json()
            token = body.get("refresh_token", "") if isinstance(body, dict) else ""
        except Exception:
            pass
    if not token:
        token = request.query_params.get("refresh_token", "")
    if not token:
        try:
            form = await request.form()
            token = form.get("refresh_token", "")
        except Exception:
            pass

    if not token:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="缺少 refresh_token",
        )

    result = refresh_access_token(token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token 无效或已过期",
        )
    access_token, new_refresh_token = result
    return JSONResponse({
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "Bearer",
        "expires_in": 60 * 60,
    })


@_api_router.get("/verify")
def api_verify(request: Request) -> JSONResponse:
    """校验当前 Authorization Bearer token 是否合法。供 RAG 后端回调验证。"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    token = auth_header[7:]
    payload = verify_jwt(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return JSONResponse({
        "valid": True,
        "user_id": int(payload["sub"]),
        "username": payload["username"],
        "role": payload["role"],
        "display_name": payload.get("display_name", ""),
    })


# 将 API 路由注册到主路由（支持 /api/v1/auth/*）
router.include_router(_api_router)
