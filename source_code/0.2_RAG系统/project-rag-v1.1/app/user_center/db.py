from __future__ import annotations

import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# 统一指向 V3.0 项目根目录下的独立用户数据库 (data/user_center.db)。
# 环境变量允许测试/部署显式覆盖，默认路径通过项目目录名解析，避免
def _find_v3_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "source_code").is_dir():
            return parent
    return Path(__file__).resolve().parents[4]

_V3_ROOT = _find_v3_root()
_ROOT_DATA_DIR = _V3_ROOT / "data"
_ROOT_DATA_DIR.mkdir(parents=True, exist_ok=True)
_DEFAULT_DB_FILE = _ROOT_DATA_DIR / "user_center.db"

USER_CENTER_DB_URL = os.getenv("USER_CENTER_DB_URL", "").strip() or f"sqlite:///{_DEFAULT_DB_FILE}"

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
