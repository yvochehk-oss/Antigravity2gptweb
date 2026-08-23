"""认证路由：GET /login, POST /login, POST /logout, /api/v1/auth/token, /api/v1/auth/refresh, /api/v1/auth/verify。
"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..auth import (
    clear_session,
    create_session,
    issue_jwt,
    login as auth_login,
    refresh_access_token,
    verify_jwt,
)
from ..templates import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "error": error},
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse:
    user = auth_login(username, password)
    if user is None:
        return RedirectResponse(
            "/login?error=用户名或密码错误",
            status_code=302,
        )
    response = RedirectResponse("/", status_code=302)
    create_session(response, user.id)
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


@_api_router.post("/token")
def api_token(
    username: str,
    password: str,
) -> JSONResponse:
    """移动端登录：验证用户名密码后签发 JWT access + refresh token。

    返回格式:
        {
          "access_token": "<jwt>",
          "refresh_token": "<jwt>",
          "token_type": "Bearer",
          "expires_in": 3600,
          "user": {"id": 1, "username": "admin", "role": "admin", "display_name": "系统管理员"}
        }
    """
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
def api_refresh(refresh_token: str) -> JSONResponse:
    """用 refresh token 换新 access + refresh token 对。"""
    result = refresh_access_token(refresh_token)
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
