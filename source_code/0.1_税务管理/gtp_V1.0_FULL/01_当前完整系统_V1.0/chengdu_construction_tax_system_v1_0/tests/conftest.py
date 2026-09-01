import os
import sys
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# Register psycopg string loaders for cross-platform string decoding on Windows
try:
    import psycopg
    from psycopg.adapt import Loader

    class _UniversalStrLoader(Loader):
        def load(self, data):
            if isinstance(data, (bytes, bytearray, memoryview)):
                return bytes(data).decode("utf-8", errors="ignore")
            return str(data)

    for _oid in [19, 25, 705, 1042, 1043, 2275]:
        psycopg.adapters.register_loader(_oid, _UniversalStrLoader)
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
os.environ["DATABASE_URL"] = (
    TEST_DATABASE_URL
    or "postgresql+psycopg://invalid:invalid@127.0.0.1:1/projectrag_invalid_test"
)

TEST_LOGIN_PASSWORD = "888888"
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET_KEY", "tax-test-secret-at-least-32-characters")
os.environ.setdefault("RAG_SHARED_API_KEY", "rag-test-secret-at-least-32-characters")
_TEMP_DIR = Path(tempfile.gettempdir())
_USER_CENTER_TEST_DB = _TEMP_DIR / f"chengdu_user_center_test_{os.getpid()}.db"
os.environ.setdefault("USER_CENTER_DB_URL", f"sqlite:///{_USER_CENTER_TEST_DB.as_posix()}")
os.environ.pop("INITIAL_ADMIN_PASSWORD", None)
os.environ.pop("INITIAL_OPERATOR_PASSWORD", None)


def pytest_sessionfinish(session, exitstatus):
    """Remove only the per-process disposable user-center SQLite file."""
    try:
        _USER_CENTER_TEST_DB.unlink(missing_ok=True)
    except OSError:
        pass


def require_test_database() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("PostgreSQL integration test requires TEST_DATABASE_URL")
    url = make_url(TEST_DATABASE_URL)
    if url.get_backend_name() not in {"postgresql", "postgres"} or "test" not in (
        url.database or ""
    ).lower():
        pytest.fail(
            "TEST_DATABASE_URL must be disposable PostgreSQL and database name must contain 'test'"
        )
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def postgres_test_database_url() -> str:
    return require_test_database()


@pytest.fixture(scope="session")
def seeded_app(postgres_test_database_url: str):
    """Rebuild and seed a disposable PostgreSQL database for integration tests.

    The database name safety check is enforced by ``require_test_database``.
    No SQLite fallback exists.
    """
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
