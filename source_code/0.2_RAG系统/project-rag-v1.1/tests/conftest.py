"""Pytest safety rails for the PostgreSQL-only ProjectRAG runtime.

The test suite has one database contract: callers provide a disposable
PostgreSQL URL through ``TEST_DATABASE_URL``. The application-level
``PROJECT_RAG_DB_URL`` is then overwritten before any application module is
imported, so a developer's ``.env``/shell value for the live ``projectrag``
database can never become the test database by accident.

Tests that do not need a database can still run without PostgreSQL. Database
tests request :func:`postgres_test_database_url` and are skipped when the
explicit disposable URL is not configured. SQLite is intentionally not a
supported test fallback; the historical SQLite tests live under
``tests/_archive_sqlite`` and are excluded by the default pytest options.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TEST_DATABASE_ENV = "TEST_DATABASE_URL"
_UNCONFIGURED_DATABASE_URL = (
    "postgresql+psycopg://invalid:invalid@127.0.0.1:1/projectrag_test_unconfigured"
)
_PRODUCTION_DATABASE_NAMES = frozenset({"projectrag", "projectrag_xianyu"})
_DATABASE_BACKED_TEST_MODULES = frozenset(
    {ROOT / "tests" / "test_executive_facts_integration.py"}
)


def _database_name(url: URL) -> str:
    return (url.database or "").strip().lower()


def _validate_disposable_database_url(raw_url: str) -> str:
    """Validate the one supported integration-test database contract."""
    value = raw_url.strip()
    try:
        parsed = make_url(value)
    except Exception as exc:  # pragma: no cover - SQLAlchemy supplies detail
        raise pytest.UsageError(
            f"{_TEST_DATABASE_ENV} must be a valid PostgreSQL URL"
        ) from exc

    if parsed.get_backend_name() not in {"postgresql", "postgres"}:
        raise pytest.UsageError(
            f"{_TEST_DATABASE_ENV} must use PostgreSQL; SQLite and other backends are not supported"
        )

    name = _database_name(parsed)
    if not name:
        raise pytest.UsageError(f"{_TEST_DATABASE_ENV} must include a database name")
    if name in _PRODUCTION_DATABASE_NAMES:
        raise pytest.UsageError(
            f"{_TEST_DATABASE_ENV} points to the formal database {name!r}; "
            "use a disposable database whose name contains 'test'"
        )
    if "test" not in name:
        raise pytest.UsageError(
            f"{_TEST_DATABASE_ENV} database name must contain 'test' so the formal projectrag database "
            "cannot be used by the test suite"
        )
    return value


_RAW_TEST_DATABASE_URL = os.environ.get(_TEST_DATABASE_ENV, "").strip()
TEST_DATABASE_URL = (
    _validate_disposable_database_url(_RAW_TEST_DATABASE_URL)
    if _RAW_TEST_DATABASE_URL
    else _UNCONFIGURED_DATABASE_URL
)

# Application modules read PROJECT_RAG_DB_URL during import. This assignment
# makes the test process independent from the live .env database; the sentinel
# is unreachable and is replaced by the validated disposable URL only when
# TEST_DATABASE_URL was explicitly supplied.
os.environ["PROJECT_RAG_DB_URL"] = TEST_DATABASE_URL

# Do not let a shell/.env service credential leak into test modules. This is
# not an authentication bypass: individual authentication tests set the key
# (and AUTH_REQUIRED) explicitly and therefore exercise the real middleware.
# Keeping the variable present but blank also prevents python-dotenv from
# loading a local .env value after this conftest is imported.
os.environ["RAG_SHARED_API_KEY"] = ""
os.environ["RAG_SHARED_API_KEY_FILE"] = ""
os.environ["PROJECT_RAG_AUTH_REQUIRED"] = "0"

os.environ.setdefault("PROJECT_RAG_DATA_DIR", str(ROOT / ".pytest-data"))
os.environ.setdefault(
    "PROJECT_RAG_IMPORT_ROOT",
    str(Path(os.environ["PROJECT_RAG_DATA_DIR"]).resolve() / "imports"),
)
os.environ.setdefault("PROJECT_RAG_EMBEDDING_BACKEND", "hash_v1")
os.environ.setdefault("PROJECT_RAG_RERANKER_BACKEND", "off")
os.environ.setdefault("PROJECT_RAG_AUTO_START_WORKER", "0")
# APP_ENV=test only enables the deliberately isolated legacy token fixtures in
# app.auth; it does not alter the API-key middleware policy.
os.environ.setdefault("APP_ENV", "test")


def require_test_database() -> str:
    """Return the explicit disposable URL or skip a DB integration test."""
    if not _RAW_TEST_DATABASE_URL:
        pytest.skip(
            f"PostgreSQL integration test requires explicit {_TEST_DATABASE_ENV}"
        )
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def postgres_test_database_url() -> str:
    """The single disposable PostgreSQL URL shared by DB integration tests."""
    return require_test_database()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Attach the shared DB fixture to modules that open SessionLocal directly."""
    del config
    for item in items:
        path = Path(str(item.fspath)).resolve()
        if path in _DATABASE_BACKED_TEST_MODULES or item.nodeid.startswith(
            "tests/test_executive_facts_integration.py::"
        ):
            item.add_marker(pytest.mark.postgresql)


@pytest.fixture(autouse=True)
def isolate_shared_api_key(monkeypatch: pytest.MonkeyPatch):
    """Reset the service key between tests unless a test opts in explicitly."""
    monkeypatch.setenv("RAG_SHARED_API_KEY", "")
    monkeypatch.setenv("RAG_SHARED_API_KEY_FILE", "")
    monkeypatch.setenv("PROJECT_RAG_AUTH_REQUIRED", "0")


@pytest.fixture(autouse=True)
def require_database_for_marked_tests(request: pytest.FixtureRequest):
    """Skip marked DB tests before any application fixture opens a connection."""
    if request.node.get_closest_marker("postgresql") is not None:
        require_test_database()


@pytest.fixture(autouse=True)
def isolate_marked_postgresql_data(
    request: pytest.FixtureRequest,
    require_database_for_marked_tests: None,
):
    """Clear copied project rows before marked integration tests.

    The executive Facts integration module predates this shared fixture and
    attempts plain ``DELETE`` statements. Those statements are rejected by
    the copied database's dependent foreign keys and the module intentionally
    catches the error, which would leave the copied production projects in
    the aggregation. A PostgreSQL-only ``TRUNCATE ... CASCADE`` here keeps the
    disposable clone hermetic without changing the test's assertions or any
    application code.
    """
    if request.node.get_closest_marker("postgresql") is None:
        return

    # The marked module has a TestClient fixture which imports the real app
    # and initializes the shared schema. Resolve it before opening SessionLocal.
    request.getfixturevalue("client")
    from sqlalchemy import text

    from app.db import SessionLocal

    with SessionLocal() as db:
        db.execute(text("TRUNCATE TABLE documents, projects RESTART IDENTITY CASCADE"))
        db.commit()
