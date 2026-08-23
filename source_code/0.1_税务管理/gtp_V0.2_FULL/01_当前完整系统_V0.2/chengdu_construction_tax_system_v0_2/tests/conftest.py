"""V0.2 pytest 全局 fixture。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def _isolated_db():
    """测试期间使用独立 SQLite 文件。"""
    tmp = tempfile.mkdtemp(prefix="v02_test_")
    db_path = Path(tmp) / "v02_test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    yield db_path
    # 清理
    try:
        db_path.unlink()
    except FileNotFoundError:
        pass


@pytest.fixture(scope="session")
def seeded_app(_isolated_db):
    """注入种子数据的 app 实例。"""
    from app.db import Base, engine, SessionLocal
    from app.seed import run as seed_run

    Base.metadata.create_all(engine)
    seed_run()
    yield
    SessionLocal().close()