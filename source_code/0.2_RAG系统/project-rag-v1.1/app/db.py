"""PostgreSQL connection/session management for ProjectRAG.

Schema creation and upgrades are owned exclusively by Alembic. Application
startup validates the required PostgreSQL/pgvector objects but never mutates
schema state.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DB_URL
from .logging_config import get_logger

logger = get_logger(__name__)

engine = create_engine(
    DB_URL,
    future=True,
    pool_pre_ping=True,
    pool_size=int(os.getenv("PROJECT_RAG_DB_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("PROJECT_RAG_DB_MAX_OVERFLOW", "20")),
    pool_recycle=int(os.getenv("PROJECT_RAG_DB_POOL_RECYCLE_SECONDS", "3600")),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Single SQLAlchemy 2.x metadata registry for the RAG service."""


def init_db() -> None:
    """Validate the Alembic-managed PostgreSQL schema; never mutate it."""
    required_tables = {
        "projects",
        "entities",
        "external_parties",
        "documents",
        "chunks",
        "ingest_jobs",
        "ai_review_runs",
        "facts_snapshots",
    }
    required_views = {
        "analytics_project_summary",
        "analytics_project_profit",
        "analytics_cost",
        "analytics_eac",
        "analytics_cashflow",
        "analytics_tax",
        "analytics_project_full",
    }
    required_indexes = {"ix_chunks_embedding_hnsw", "ix_chunks_search_text_fts"}

    with engine.connect() as conn:
        tables = set(
            conn.execute(
                text(
                    "SELECT tablename FROM pg_catalog.pg_tables "
                    "WHERE schemaname=current_schema()"
                )
            ).scalars()
        )
        missing_tables = sorted(required_tables - tables)
        if missing_tables:
            raise RuntimeError(
                "ProjectRAG schema incomplete; run Tax migrations first, then RAG migrations. "
                "Missing tables: " + ", ".join(missing_tables)
            )

        views = set(
            conn.execute(
                text(
                    "SELECT viewname FROM pg_catalog.pg_views "
                    "WHERE schemaname=current_schema()"
                )
            ).scalars()
        )
        missing_views = sorted(required_views - views)
        if missing_views:
            raise RuntimeError(
                "ProjectRAG analytics schema incomplete; run RAG Alembic migrations. "
                "Missing views: " + ", ".join(missing_views)
            )

        vector_version = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        ).scalar_one_or_none()
        if not vector_version:
            raise RuntimeError("pgvector extension missing; run RAG Alembic migrations")

        indexes = set(
            conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()")
            ).scalars()
        )
        missing_indexes = sorted(required_indexes - indexes)
        if missing_indexes:
            raise RuntimeError(
                "ProjectRAG vector/search indexes incomplete; run RAG Alembic migrations. "
                "Missing indexes: " + ", ".join(missing_indexes)
            )


def db_health() -> dict:
    """Return a small, non-mutating PostgreSQL health snapshot."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            vector = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            ).scalar_one_or_none()
        return {
            "ok": True,
            "backend": "postgresql",
            "pgvector": vector,
            "pool_size": engine.pool.size(),
            "pool_checked_out": engine.pool.checkedout(),
        }
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        return {"ok": False, "backend": "postgresql", "error": str(exc)}


def close_connections() -> None:
    """Dispose all pooled connections during graceful shutdown."""
    engine.dispose()
    logger.info("Database connections closed")
