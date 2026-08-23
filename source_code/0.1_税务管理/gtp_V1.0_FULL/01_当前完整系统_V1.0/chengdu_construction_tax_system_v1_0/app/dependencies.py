"""FastAPI 依赖注入：当前用户 / 角色保护。"""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from .auth import current_user_from_request
from .models import User


# ---------------------------------------------------------------------------
# 可选认证：未登录返回 None，登录返回 User
# ---------------------------------------------------------------------------
def get_optional_user(request: Request) -> User | None:
    """用于模板上下文：返回当前登录用户，未登录则为 None。"""
    return current_user_from_request(request)


# ---------------------------------------------------------------------------
# 强制认证：未登录 → 重定向到登录页
# ---------------------------------------------------------------------------
def require_login(request: Request) -> User:
    """用于需要登录的路由。未登录返回 401 重定向。"""
    user = current_user_from_request(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
        )
    return user


# ---------------------------------------------------------------------------
# 角色保护
# ---------------------------------------------------------------------------
def require_role(*roles: str):
    """装饰工厂：只允许指定角色的用户访问。"""
    def dependency(request: Request) -> User:
        user = require_login(request)
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要 {roles} 角色，当前为 {user.role}",
            )
        return user
    return dependency


# ---------------------------------------------------------------------------
# 快捷依赖
# ---------------------------------------------------------------------------
def admin_only(request: Request) -> User:
    """管理员专属路由。未登录 → 401；非 admin → 403。"""
    user = require_login(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
