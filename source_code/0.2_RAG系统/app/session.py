"""Database session management with context manager pattern.

All database operations should use get_db() context manager to ensure
proper connection cleanup and avoid session leaks.
"""
from contextlib import contextmanager
from sqlalchemy.orm import Session
from .db import SessionLocal

@contextmanager
def get_db() -> Session:
    """Thread-safe database session context manager.

    Usage:
        with get_db() as db:
            db.query(Model).all()
        # Session automatically closed after exit
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """Get a new database session without context manager.

    Caller is responsible for calling db.close().
    Prefer using get_db() context manager when possible.

    Returns:
        Session: A new SQLAlchemy session instance.
    """
    return SessionLocal()
