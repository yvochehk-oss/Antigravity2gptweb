"""独立轻量级用户管理中心模块。

拥有独立的 SQLite 数据库 (data/user_center.db)，与 Tax/RAG 业务主库 (PostgreSQL projectrag) 彻底物理隔离。
提供用户个人中心、头像上传裁剪、邮箱/手机绑定、6位验证码动态改密 (OTP)。
"""
from .router import router as user_center_router

__all__ = ["user_center_router"]
