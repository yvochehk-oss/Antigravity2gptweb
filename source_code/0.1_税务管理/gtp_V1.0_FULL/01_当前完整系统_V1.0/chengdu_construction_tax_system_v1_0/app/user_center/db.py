from __future__ import annotations

import os
from pathlib import Path
from sqlalchemy.exc import IntegrityError
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

_EXPLICIT_DB_URL = os.getenv("USER_CENTER_DB_URL", "").strip()
if _EXPLICIT_DB_URL:
    USER_CENTER_DB_URL = _EXPLICIT_DB_URL
else:
    def _find_project_root() -> Path:
        for parent in Path(__file__).resolve().parents:
            if (parent / ".git").exists() or (parent / "source_code").is_dir():
                return parent
        return Path(__file__).resolve().parents[4]

    _PROJECT_ROOT = _find_project_root()
    _ROOT_DATA_DIR = _PROJECT_ROOT / "data"
    _ROOT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _DEFAULT_DB_FILE = _ROOT_DATA_DIR / "user_center.db"
    USER_CENTER_DB_URL = f"sqlite:///{_DEFAULT_DB_FILE.as_posix()}"


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
    """获取独立用户数据库会话"""
    db: Session = UserCenterSessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_user_center_db():
    """初始化独立用户中心数据库表结构"""
    from . import models  # noqa: F401
    UserCenterBase.metadata.create_all(bind=engine)
    _ensure_test_admin()


def _ensure_test_admin() -> None:
    """Seed the retained test admin only when the account is absent.

    This is a bootstrap convenience for the local test environment.  It does
    not reset an existing account's password or role, and it is not used as an
    authentication fallback for anonymous requests.
    """
    environment = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
    if environment not in {"test", "testing", "development", "dev", "local"}:
        return

    from .models import UserAccount
    from .security import hash_password

    db = UserCenterSessionLocal()
    try:
        if db.query(UserAccount).filter(UserAccount.username == "admin").first():
            return
        db.add(UserAccount(
            username="admin",
            password_hash=hash_password("888888"),
            nickname="系统管理员",
            avatar_url="/static/avatars/default.png",
            email="admin@cd-construction.com",
            email_verified=True,
            phone="13800008888",
            phone_verified=True,
            role="admin",
            active=True,
        ))
        try:
            db.commit()
        except IntegrityError:
            # Another process may initialize the same SQLite file at startup.
            db.rollback()
    finally:
        db.close()
