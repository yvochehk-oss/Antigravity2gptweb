"""Database connection and session management."""
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError, DBAPIError
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import DB_URL, IS_POSTGRES
from .logging_config import get_logger

logger = get_logger(__name__)

kwargs = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(
    DB_URL,
    connect_args=kwargs,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db():
    """Initialize database with extensions and tables."""
    # pgvector extension must exist before tables with vector columns are created.
    if IS_POSTGRES:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    Base.metadata.create_all(engine)

    if IS_POSTGRES:
        # HNSW supports immediate use without a training phase.
        with engine.begin() as conn:
            try:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw "
                    "ON chunks USING hnsw (embedding vector_cosine_ops) "
                    "WITH (m = 16, ef_construction = 64)"
                ))
            except (SQLAlchemyError, DBAPIError) as e:
                logger.warning(f"HNSW index creation skipped: {e}")

        # Create PostgreSQL full-text search index for BM25 optimization
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS ix_chunks_search_text_fts
                    ON chunks USING gin(to_tsvector('simple', search_text))
                    WHERE search_text IS NOT NULL AND search_text != ''
                """))
        except (SQLAlchemyError, DBAPIError) as e:
            logger.warning(f"GIN FTS index creation skipped: {e}")


def db_health() -> dict:
    """Check database health status.

    Returns:
        Dict with health status, backend type, and pgvector version if applicable.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            vector = None
            if IS_POSTGRES:
                result = conn.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname='vector'")
                ).scalar()
                vector = result
        return {
            "ok": True,
            "backend": "postgresql" if IS_POSTGRES else "sqlite",
            "pgvector": vector,
            "pool_size": engine.pool.size(),
            "pool_checked_out": engine.pool.checkedout(),
        }
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {
            "ok": False,
            "backend": "postgresql" if IS_POSTGRES else "sqlite",
            "error": str(e)
        }


def close_connections():
    """Explicitly close all pooled connections.

    Call this during graceful shutdown.
    """
    engine.dispose()
    logger.info("Database connections closed")
