"""RAG 独立用户权限管理路由（直接连接全局独立的 user_center.db 用户数据库）。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_web_auth, require_web_or_service_role, TaxPrincipal
from ..logging_config import get_logger
from ..user_center.db import get_user_center_db
from ..user_center.models import UserAccount
from ..user_center.security import hash_password

logger = get_logger(__name__)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
router = APIRouter(tags=["users"])


def _user_to_dict(u: UserAccount) -> dict:
    created_str = u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "—"
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role or "operator",
        "display_name": u.nickname or u.username,
        "nickname": u.nickname or "",
        "avatar_url": u.avatar_url or "/static/avatars/default.png",
        "email": u.email or "",
        "phone": u.phone or "",
        "active": bool(u.active),
        "created_at": created_str,
    }


class UserCreateSchema(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    role: str = Field("operator", pattern="^(admin|operator|viewer)$")
    nickname: str = Field("", max_length=80)
    email: Optional[str] = Field("", max_length=120)
    phone: Optional[str] = Field("", max_length=20)


class UserUpdateSchema(BaseModel):
    nickname: Optional[str] = Field(None, max_length=80)
    role: Optional[str] = Field(None, pattern="^(admin|operator|viewer)$")
    email: Optional[str] = None
    phone: Optional[str] = None
    active: Optional[bool] = None


class ResetPasswordSchema(BaseModel):
    new_password: str = Field(..., min_length=6)


@router.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    principal: TaxPrincipal = Depends(require_web_auth),
    db: Session = Depends(get_user_center_db),
):
    """用户权限管理看板。"""
    users = db.execute(select(UserAccount).order_by(UserAccount.id.asc())).scalars().all()
    user_dicts = [_user_to_dict(u) for u in users]
    
    total_count = len(user_dicts)
    admin_count = sum(1 for u in user_dicts if u["role"] == "admin" and u["active"])
    operator_count = sum(1 for u in user_dicts if u["role"] == "operator" and u["active"])
    active_count = sum(1 for u in user_dicts if u["active"])

    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "request": request,
            "principal": principal,
            "users": user_dicts,
            "active_page": "users",
            "stats": {
                "total": total_count,
                "admin": admin_count,
                "operator": operator_count,
                "active": active_count,
            },
        },
    )


@router.get("/api/v1/users")
def api_list_users(
    principal: TaxPrincipal = Depends(require_web_or_service_role("admin", "operator")),
    db: Session = Depends(get_user_center_db),
):
    """从独立用户数据库列出所有账号。"""
    users = db.execute(select(UserAccount).order_by(UserAccount.id.asc())).scalars().all()
    return [_user_to_dict(u) for u in users]


@router.post("/api/v1/users", status_code=status.HTTP_201_CREATED)
def api_create_user(
    body: UserCreateSchema,
    principal: TaxPrincipal = Depends(require_web_or_service_role("admin")),
    db: Session = Depends(get_user_center_db),
):
    """在独立用户库中创建新用户（仅管理员）。"""
    clean_username = body.username.strip()
    existing = db.scalar(select(UserAccount).where(UserAccount.username == clean_username))
    if existing:
        raise HTTPException(status_code=400, detail="该用户名已存在")

    pw_hash = hash_password(body.password)
    user = UserAccount(
        username=clean_username,
        password_hash=pw_hash,
        role=body.role,
        nickname=body.nickname.strip() or clean_username,
        email=body.email.strip() if body.email else None,
        phone=body.phone.strip() if body.phone else None,
        active=True,
        avatar_url="/static/avatars/default.png",
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
        logger.info("Created user account %s in user_center.db by %s", user.username, principal.username)
        return _user_to_dict(user)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to create user in user_center.db: %s", clean_username)
        raise HTTPException(status_code=500, detail="创建用户失败，事务已回滚") from exc


@router.post("/api/v1/users/{user_id}/update")
def api_update_user(
    user_id: int,
    body: UserUpdateSchema,
    principal: TaxPrincipal = Depends(require_web_or_service_role("admin")),
    db: Session = Depends(get_user_center_db),
):
    """更新独立用户库中的账号信息（仅管理员）。"""
    user = db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if body.nickname is not None:
        user.nickname = body.nickname.strip()
    if body.role is not None:
        user.role = body.role
    if body.email is not None:
        user.email = body.email.strip() if body.email else None
    if body.phone is not None:
        user.phone = body.phone.strip() if body.phone else None
    if body.active is not None:
        if not body.active and user.username == principal.username:
            raise HTTPException(status_code=400, detail="禁止禁用当前登录的管理员账户")
        user.active = body.active

    try:
        db.commit()
        db.refresh(user)
        logger.info("Updated user account %s in user_center.db by %s", user.username, principal.username)
        return _user_to_dict(user)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to update user %s", user_id)
        raise HTTPException(status_code=500, detail="更新用户失败，事务已回滚") from exc


@router.post("/api/v1/users/{user_id}/reset-password")
def api_reset_password(
    user_id: int,
    body: ResetPasswordSchema,
    principal: TaxPrincipal = Depends(require_web_or_service_role("admin")),
    db: Session = Depends(get_user_center_db),
):
    """在独立用户库中重置用户密码（仅管理员）。"""
    user = db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    pw_hash = hash_password(body.new_password)
    user.password_hash = pw_hash
    try:
        db.commit()
        logger.info("Reset password for user %s in user_center.db by %s", user.username, principal.username)
        return {"status": "ok", "message": f"用户 {user.username} 的密码已成功重置"}
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to reset password for user %s", user_id)
        raise HTTPException(status_code=500, detail="重置密码失败，事务已回滚") from exc


@router.post("/api/v1/users/{user_id}/toggle-active")
def api_toggle_active(
    user_id: int,
    principal: TaxPrincipal = Depends(require_web_or_service_role("admin")),
    db: Session = Depends(get_user_center_db),
):
    """快速切换启用/禁用状态（仅管理员）。"""
    user = db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user.active and user.username == principal.username:
        raise HTTPException(status_code=400, detail="禁止禁用当前登录的管理员账户")

    user.active = not bool(user.active)
    try:
        db.commit()
        db.refresh(user)
        logger.info("Toggled active status of user %s in user_center.db to %s", user.username, user.active)
        return _user_to_dict(user)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to toggle active status for user %s", user_id)
        raise HTTPException(status_code=500, detail="切换状态失败，事务已回滚") from exc
