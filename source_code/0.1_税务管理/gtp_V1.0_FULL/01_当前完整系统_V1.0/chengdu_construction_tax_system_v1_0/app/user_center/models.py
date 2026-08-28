from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from .db import UserCenterBase

def _utc_now():
    return datetime.now(timezone.utc)

class UserAccount(UserCenterBase):
    """独立用户中心账号表（不与业务库共用）"""
    __tablename__ = "user_accounts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    nickname = Column(String(64), nullable=True, default="")
    avatar_url = Column(String(255), nullable=True, default="/static/avatars/default.png")
    
    email = Column(String(128), unique=True, index=True, nullable=True)
    email_verified = Column(Boolean, default=False)
    
    phone = Column(String(32), unique=True, index=True, nullable=True)
    phone_verified = Column(Boolean, default=False)
    
    role = Column(String(32), default="operator")  # admin / operator / manager / auditor
    
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)

class VerificationCode(UserCenterBase):
    """6位动态验证码记录表（用于绑定/改密安全校验）"""
    __tablename__ = "verification_codes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    target = Column(String(128), index=True, nullable=False)  # 接收邮箱或手机号
    code = Column(String(8), nullable=False)                 # 6位数字验证码
    purpose = Column(String(32), nullable=False)             # change_password / bind_email / bind_phone
    
    created_at = Column(DateTime, default=_utc_now)
    expires_at = Column(DateTime, nullable=False)            # 5分钟后过期
    used = Column(Boolean, default=False)                   # 是否已使用
