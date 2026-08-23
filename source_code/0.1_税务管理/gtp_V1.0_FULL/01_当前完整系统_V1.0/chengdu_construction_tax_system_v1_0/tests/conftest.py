"""PostgreSQL-only pytest safety rails for Tax."""
from __future__ import annotations
import os,sys
from pathlib import Path
import pytest
from sqlalchemy.engine import make_url
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
TEST_DATABASE_URL=os.getenv('TEST_DATABASE_URL','').strip()
os.environ['DATABASE_URL']=TEST_DATABASE_URL or 'postgresql+psycopg://invalid:invalid@127.0.0.1:1/projectrag_invalid_test'
os.environ.setdefault('APP_ENV','test'); os.environ.setdefault('JWT_SECRET_KEY','tax-test-secret-at-least-32-characters'); os.environ.setdefault('RAG_SHARED_API_KEY','rag-test-secret-at-least-32-characters')
def require_test_database():
 if not TEST_DATABASE_URL: pytest.skip('PostgreSQL integration test requires TEST_DATABASE_URL')
 u=make_url(TEST_DATABASE_URL)
 if u.get_backend_name() not in {'postgresql','postgres'} or 'test' not in (u.database or '').lower(): pytest.fail("TEST_DATABASE_URL must be disposable PostgreSQL and database name must contain 'test'")
 return TEST_DATABASE_URL
@pytest.fixture(scope='session')
def postgres_test_database_url(): return require_test_database()


@pytest.fixture(scope="session")
def seeded_app(postgres_test_database_url):
    """Rebuild and seed a disposable PostgreSQL database for integration tests.

    The database name safety check is enforced by ``require_test_database``.
    No SQLite fallback exists.
    """
    from sqlalchemy import create_engine, text
    from alembic import command
    from alembic.config import Config

    engine = create_engine(postgres_test_database_url, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", postgres_test_database_url)
    command.upgrade(cfg, "head")

    # Imports occur only after DATABASE_URL is already bound to the safe test DB.
    from app.seed import run as seed_run
    seed_run()
    yield
