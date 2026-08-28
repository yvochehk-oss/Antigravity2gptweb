from __future__ import annotations

import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# 独立数据库存储路径：与 PostgreSQL 业务库彻底隔离
_DATA_DIR = Path(__file__).resolve().parent / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_DEFAULT_DB_FILE = _DATA_DIR / "user_center.db"

USER_CENTER_DB_URL = os.getenv("USER_CENTER_DB_URL", f"sqlite:///{_DEFAULT_DB_FILE}")

engine = create_engine(
    USER_CENTER_DB_URL,
    connect_args={"check_same_thread": False} if USER_CENTER_DB_URL.startswith("sqlite") else {},
    echo=False,
)

UserCenterBase = declarative_base()

UserCenterSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

def get_user_center_db():
    """获取独立用户中心数据库会话"""
    db: Session = UserCenterSessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_user_center_db():
    """初始化创建独立用户数据表并确立初始管理员"""
    UserCenterBase.metadata.create_all(bind=engine)
    from .models import UserAccount
    from .security import hash_password
    db: Session = UserCenterSessionLocal()
    try:
        admin = db.query(UserAccount).filter(UserAccount.username == "admin").first()
        if not admin:
            admin = UserAccount(
                username="admin",
                password_hash=hash_password("888888"),
                nickname="系统管理员",
                avatar_url="/static/avatars/default.png",
                email="admin@cd-construction.com",
                email_verified=True,
                phone="13800008888",
                phone_verified=True,
                role="admin",
            )
            db.add(admin)
            db.commit()
    finally:
        db.close()

