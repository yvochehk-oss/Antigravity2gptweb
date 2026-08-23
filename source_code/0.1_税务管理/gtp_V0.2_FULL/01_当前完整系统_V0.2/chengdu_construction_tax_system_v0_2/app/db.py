"""V0.2: 数据库引擎与基类。"""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

URL: str = os.getenv("DATABASE_URL", "sqlite:///./data/demo.db")

engine = create_engine(
    URL,
    connect_args={"check_same_thread": False} if URL.startswith("sqlite") else {},
    future=True,
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


class Base(DeclarativeBase):
    """SQLAlchemy 2.x 风格基类。"""