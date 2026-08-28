from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from .db import get_user_center_db, init_user_center_db
from .models import UserAccount, VerificationCode
from .notifier import send_email_code, send_sms_code
from .security import generate_otp_code, hash_password, verify_password

router = APIRouter(prefix="/api/v1/user-center", tags=["独立用户中心"])

# 确保表已初始化
init_user_center_db()

# 头像存储目录
_STATIC_DIST_AVATARS = Path(__file__).resolve().parent.parent / "static_dist" / "avatars"
_STATIC_DIST_AVATARS.mkdir(parents=True, exist_ok=True)

def _get_current_user(request: Request, db: Session = Depends(get_user_center_db)) -> UserAccount:
    """获取当前登录用户；缺少有效认证时明确拒绝请求。

    The test administrator is seeded once when the user-center database is
    initialized, but a database record must never be treated as a session.
    ``AuthMiddleware`` resolves the signed Tax session/JWT and stores the
    principal on ``request.state`` before this dependency runs.
    """
    principal = getattr(request.state, "current_user", None)
    user_id = getattr(principal, "id", None)
    principal_source = str(getattr(principal, "auth_source", "") or "").strip().lower()
    if user_id is None or principal_source != "user_center":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
            headers={"WWW-Authenticate": "Session"},
        )

    try:
        user = db.get(UserAccount, int(user_id))
    except (TypeError, ValueError):
        user = None
    principal_username = str(getattr(principal, "username", "") or "").strip()
    if (
        user is None
        or not bool(user.active)
        or (principal_username and user.username != principal_username)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Session"},
        )
    return user

# --- 请求模型 ---
class SendCodeRequest(BaseModel):
    target: str = Field(..., description="接收邮箱地址或手机号")
    channel: str = Field(..., description="email 或 sms")
    purpose: str = Field(default="change_password", description="change_password / bind_email / bind_phone")

class BindAccountRequest(BaseModel):
    channel: str = Field(..., description="email 或 sms")
    target: str = Field(..., description="邮箱地址或手机号")
    code: str = Field(..., min_length=6, max_length=6, description="6位数字验证码")

class ChangePasswordRequest(BaseModel):
    channel: str = Field(..., description="email 或 sms (选择验证码接收通道)")
    code: str = Field(..., min_length=6, max_length=6, description="6位数字验证码")
    new_password: str = Field(..., min_length=6, max_length=64, description="新密码")

class UpdateNicknameRequest(BaseModel):
    nickname: str = Field(..., min_length=1, max_length=64, description="用户昵称")

# --- 接口实现 ---

@router.get("/profile")
def get_user_profile(user: UserAccount = Depends(_get_current_user)):
    """获取当前用户的个人中心资料（含头像、手机、邮箱绑定状态）"""
    return {
        "id": user.id,
        "username": user.username,
        "nickname": user.nickname or user.username,
        "avatar_url": user.avatar_url or "/static/avatars/default.png",
        "email": user.email or "",
        "email_verified": bool(user.email_verified),
        "phone": user.phone or "",
        "phone_verified": bool(user.phone_verified),
        "role": user.role,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }

@router.post("/nickname")
def update_nickname(
    req: UpdateNicknameRequest,
    user: UserAccount = Depends(_get_current_user),
    db: Session = Depends(get_user_center_db),
):
    """修改用户昵称"""
    user.nickname = req.nickname.strip()
    db.commit()
    db.refresh(user)
    return {"message": "昵称修改成功", "nickname": user.nickname}

@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user: UserAccount = Depends(_get_current_user),
    db: Session = Depends(get_user_center_db),
):
    """上传并更新用户头像图片"""
    allowed_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    ext = Path(file.filename or "avatar.png").suffix.lower()
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的图片格式：{ext}，仅支持 PNG/JPG/WEBP/GIF",
        )

    file_name = f"avatar_{user.username}_{int(datetime.now().timestamp())}{ext}"
    dest_path = _STATIC_DIST_AVATARS / file_name

    try:
        with dest_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存头像失败: {exc}")

    # 更新数据库中的头像路径
    avatar_url = f"/avatars/{file_name}"
    user.avatar_url = avatar_url
    db.commit()
    db.refresh(user)

    return {"message": "头像上传成功", "avatar_url": avatar_url}

@router.post("/send-code")
def send_verification_code(
    req: SendCodeRequest,
    user: UserAccount = Depends(_get_current_user),
    db: Session = Depends(get_user_center_db),
):
    """发送 6 位动态验证码至手机或邮箱（带 60s 频控防刷与 5 分钟有效期）"""
    target = req.target.strip()
    if not target:
        raise HTTPException(status_code=400, detail="目标邮箱或手机号不能为空")

    now = datetime.now(timezone.utc)
    one_minute_ago = now - timedelta(seconds=60)

    # 检查频控：60秒内不得重复发送
    recent = (
        db.query(VerificationCode)
        .filter(
            VerificationCode.target == target,
            VerificationCode.created_at >= one_minute_ago,
        )
        .first()
    )
    if recent:
        raise HTTPException(status_code=429, detail="验证码发送过于频繁，请 60 秒后再试")

    # 生成 6 位随机验证码
    code = generate_otp_code()
    expires_at = now + timedelta(minutes=5)

    vc = VerificationCode(
        target=target,
        code=code,
        purpose=req.purpose,
        created_at=now,
        expires_at=expires_at,
        used=False,
    )
    db.add(vc)
    db.commit()

    # 触发发送
    if req.channel == "email":
        ok, msg = send_email_code(target, code, req.purpose)
    else:
        ok, msg = send_sms_code(target, code, req.purpose)

    if not ok:
        raise HTTPException(status_code=500, detail=msg)

    return {
        "message": f"验证码已发送至 {target}，有效期 5 分钟",
        "channel": req.channel,
        "cooldown_seconds": 60,
    }

@router.post("/bind-account")
def bind_account(
    req: BindAccountRequest,
    user: UserAccount = Depends(_get_current_user),
    db: Session = Depends(get_user_center_db),
):
    """验证 6 位验证码并绑定手机或邮箱"""
    target = req.target.strip()
    code = req.code.strip()
    now = datetime.now(timezone.utc)

    # 查找未过期、未使用的有效验证码
    vc = (
        db.query(VerificationCode)
        .filter(
            VerificationCode.target == target,
            VerificationCode.code == code,
            VerificationCode.used == False,
            VerificationCode.expires_at >= now,
        )
        .order_by(VerificationCode.id.desc())
        .first()
    )
    if not vc:
        raise HTTPException(status_code=400, detail="验证码错误或已过期，请重新获取")

    # 核销验证码
    vc.used = True

    if req.channel == "email":
        user.email = target
        user.email_verified = True
    else:
        user.phone = target
        user.phone_verified = True

    db.commit()
    db.refresh(user)

    return {
        "message": f"成功绑定{'电子邮箱' if req.channel == 'email' else '手机号码'}",
        "email": user.email,
        "phone": user.phone,
    }

@router.post("/change-password")
def change_password_with_code(
    req: ChangePasswordRequest,
    user: UserAccount = Depends(_get_current_user),
    db: Session = Depends(get_user_center_db),
):
    """更改密码：必须验证用户已绑定的手机号或邮箱接收到的 6 位动态验证码"""
    # 确定接收验证码的目标
    target = user.email if req.channel == "email" else user.phone
    if not target:
        raise HTTPException(
            status_code=400,
            detail=f"尚未绑定{'电子邮箱' if req.channel == 'email' else '手机号码'}，请先在个人中心完成绑定",
        )

    code = req.code.strip()
    now = datetime.now(timezone.utc)

    # 校验验证码
    vc = (
        db.query(VerificationCode)
        .filter(
            VerificationCode.target == target,
            VerificationCode.code == code,
            VerificationCode.used == False,
            VerificationCode.expires_at >= now,
        )
        .order_by(VerificationCode.id.desc())
        .first()
    )
    if not vc:
        raise HTTPException(status_code=400, detail="验证码错误或已过期，请重新获取")

    # 核销验证码
    vc.used = True

    # 更新密码哈希
    user.password_hash = hash_password(req.new_password)
    db.commit()
    db.refresh(user)

    return {
        "message": "登录密码修改成功，请牢记新密码！",
        "username": user.username,
    }
