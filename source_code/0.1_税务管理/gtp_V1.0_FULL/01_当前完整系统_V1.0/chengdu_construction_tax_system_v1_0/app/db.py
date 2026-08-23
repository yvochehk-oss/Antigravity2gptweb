"""PostgreSQL database engine and SQLAlchemy base for the Tax system."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=env_file, override=False)
URL = os.getenv("DATABASE_URL", "").strip()
if not URL:
    raise RuntimeError("DATABASE_URL is required; Tax is PostgreSQL-only")
backend = make_url(URL).get_backend_name()
if backend not in {"postgresql", "postgres"}:
    raise RuntimeError(f"Tax is PostgreSQL-only; unsupported DATABASE_URL backend: {backend}")
engine = create_engine(URL, future=True, pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE_SECONDS", "3600")))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
class Base(DeclarativeBase):
    """SQLAlchemy 2.x declarative base."""
