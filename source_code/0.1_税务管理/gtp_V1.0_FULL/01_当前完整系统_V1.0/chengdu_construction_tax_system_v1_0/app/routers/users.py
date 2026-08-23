"""用户管理路由（admin 专属）：用户 CRUD。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..auth import hash_password, verify_password
from ..db import SessionLocal
from ..dependencies import admin_only, require_login
from ..models import User


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    display_name: str
    active: bool
    created_at: str
    last_login: str

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    role: str = Field(..., pattern="^(admin|operator)$")
    display_name: str = Field("", max_length=80)


class UserUpdate(BaseModel):
    role: str | None = Field(None, pattern="^(admin|operator)$")
    display_name: str | None = Field(None, max_length=80)
    active: bool | None = None


class UserPasswordChange(BaseModel):
    old_password: str | None = None   # admin 重置时不需要旧密码；用户自改时需要
    new_password: str = Field(..., min_length=6)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "display_name": u.display_name,
        "active": u.active,
        "created_at": u.created_at,
        "last_login": u.last_login,
    }


@router.get("", response_model=list[UserResponse])
def list_users(request: Request):
    """列出所有用户（admin 专属）。"""
    admin_only(request)
    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.id).all()
        return [_user_to_dict(u) for u in users]
    finally:
        db.close()


@router.get("/{user_id}", response_model=UserResponse)
def get_user(request: Request, user_id: int):
    """查看单个用户（admin 专属）。"""
    admin_only(request)
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        return _user_to_dict(user)
    finally:
        db.close()


@router.post("", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
def create_user(request: Request, body: UserCreate):
    """创建新用户（admin 专属）。"""
    admin_only(request)
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == body.username).first()
        if existing:
            raise HTTPException(status_code=400, detail="用户名已存在")
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        pw_hash, pw_salt = hash_password(body.password)
        user = User(
            username=body.username,
            password_hash=f"{pw_hash}:{pw_salt}",
            role=body.role,
            display_name=body.display_name,
            active=True,
            created_at=ts,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return _user_to_dict(user)
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(request: Request, user_id: int, body: UserUpdate):
    """修改用户角色/显示名/状态（admin 专属）。"""
    admin_only(request)
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if body.role is not None:
            user.role = body.role
        if body.display_name is not None:
            user.display_name = body.display_name
        if body.active is not None:
            user.active = body.active
        db.commit()
        db.refresh(user)
        return _user_to_dict(user)
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/self/password")
def self_change_password(request: Request, body: UserPasswordChange):
    """当前登录用户修改自己的密码（需验证旧密码）。"""
    user = require_login(request)
    if body.old_password is None:
        raise HTTPException(status_code=400, detail="需要提供旧密码")
    db = SessionLocal()
    try:
        db_user = db.get(User, user.id)
        if not db_user:
            raise HTTPException(status_code=404, detail="用户不存在")
        parts = db_user.password_hash.split(":")
        if len(parts) != 2:
            raise HTTPException(status_code=500, detail="密码数据损坏")
        hash_hex, salt_hex = parts
        if not verify_password(body.old_password, hash_hex, salt_hex):
            raise HTTPException(status_code=400, detail="旧密码不正确")
        pw_hash, pw_salt = hash_password(body.new_password)
        db_user.password_hash = f"{pw_hash}:{pw_salt}"
        db.commit()
        return {"status": "ok", "message": "密码已更新"}
    finally:
        db.close()

@router.post("/{user_id}/password")
def reset_password(request: Request, user_id: int, body: UserPasswordChange):
    """重置用户密码（admin 专属）或用户自改密码。

    - admin 重置：无需旧密码，直接设置新密码。
    - 用户自改：通过 /self/password 端点，需验证旧密码。
    """
    admin_only(request)
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        pw_hash, pw_salt = hash_password(body.new_password)
        user.password_hash = f"{pw_hash}:{pw_salt}"
        db.commit()
        return {"status": "ok", "message": "密码已更新"}
    finally:
        db.close()


