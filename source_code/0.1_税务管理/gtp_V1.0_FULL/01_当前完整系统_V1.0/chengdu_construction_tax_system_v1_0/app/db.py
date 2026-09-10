"""PostgreSQL database engine and SQLAlchemy base for the Tax system."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

def _normalize_db_url(url: str) -> str:
    url = url.strip()
    try:
        parsed = make_url(url)
        if parsed.host in ("127.0.0.1", "localhost") and parsed.port == 54320:
            import socket
            sock = socket.socket()
            sock.settimeout(0.3)
            try:
                sock.connect((parsed.host, 54320))
                sock.close()
            except Exception:
                try:
                    sock2 = socket.socket()
                    sock2.settimeout(0.3)
                    sock2.connect((parsed.host, 5432))
                    sock2.close()
                    url = url.replace(":54320", ":5432")
                except Exception:
                    pass
    except Exception:
        pass
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    return url

env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=env_file, override=False)
URL = os.getenv("DATABASE_URL", "").strip()
if not URL:
    raise RuntimeError("DATABASE_URL is required; Tax is PostgreSQL-only")
backend = make_url(URL).get_backend_name()
if backend not in {"postgresql", "postgres"}:
    raise RuntimeError(f"Tax is PostgreSQL-only; unsupported DATABASE_URL backend: {backend}")
engine = create_engine(_normalize_db_url(URL), future=True, pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE_SECONDS", "3600")))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
class Base(DeclarativeBase):
    """SQLAlchemy 2.x declarative base."""
