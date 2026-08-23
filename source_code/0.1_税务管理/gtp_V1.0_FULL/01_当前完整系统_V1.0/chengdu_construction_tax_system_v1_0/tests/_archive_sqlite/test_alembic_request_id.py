"""Acceptance tests for the Tax request-id migration and recovery boundary."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from _alembic_test_utils import alembic_call, isolated_import_state
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from alembic import command

ROOT = Path(__file__).resolve().parents[1]


def _config(database: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


def _copy_live_database(tmp_path: Path) -> Path:
    source = ROOT / "data" / "demo.db"
    target = tmp_path / "request-id-copy.db"
    # Use SQLite's online backup API so the test copy is consistent even when
    # the source is using WAL mode.  The source is opened read-only by this
    # operation and is never replaced or mutated.
    with sqlite3.connect(source) as source_connection, sqlite3.connect(target) as target_connection:
        source_connection.backup(target_connection)
    # The checked-in/live database may already be at the new head after the
    # operational migration.  Build a private 001_initial baseline for these
    # tests without touching the live file or any backup.  This keeps the
    # migration tests repeatable before and after rollout.
    with sqlite3.connect(target) as connection:
        has_request_id = {
            table_name: any(
                column[1] == "request_id"
                for column in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            )
            for table_name in ("audit_logs", "facts_request_logs")
        }
        if any(has_request_id.values()):
            for table_name in ("audit_logs", "facts_request_logs"):
                if not has_request_id[table_name]:
                    raise AssertionError(f"partial request_id rollout in test source: {table_name}")
                index_name = f"ix_{table_name}_request_id"
                connection.execute(f'DROP INDEX IF EXISTS "{index_name}"')
                connection.execute(f'ALTER TABLE "{table_name}" DROP COLUMN request_id')
            connection.execute("UPDATE alembic_version SET version_num = '001_initial'")
            connection.commit()
    return target


def _state(database: Path) -> tuple[tuple, dict[str, tuple], tuple]:
    with sqlite3.connect(database) as connection:
        schema = tuple(
            connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
        )
        tables = [row[1] for row in schema if row[0] == "table"]
        data: dict[str, tuple] = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            data[table] = tuple(
                connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid").fetchall()
            )
        version = tuple(
            connection.execute(
                "SELECT version_num FROM alembic_version ORDER BY version_num"
            ).fetchall()
        )
    return schema, data, version


def test_request_id_upgrade_preserves_data_and_allows_audit_and_facts_writes(tmp_path, monkeypatch):
    database = _copy_live_database(tmp_path)
    config = _config(database, monkeypatch)

    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO audit_logs(action, object_type, object_id, message, actor, ip)
            VALUES ('before_migration', 'test', '1', '保留审计', 'tester', '127.0.0.1')
            """
        )
        connection.execute(
            """
            INSERT INTO facts_request_logs(
                project_code, endpoint, require_fresh, max_age, as_of_param,
                response_status, facts_snapshot_id, latency_ms, error_message,
                actor, ip, created_at
            ) VALUES ('P-001', '/facts', 0, 60, '', 200, NULL, 2, '',
                      'tester', '127.0.0.1', '2026-08-20T00:00:00Z')
            """
        )
        connection.commit()

    alembic_call(command.upgrade, config, "head")
    alembic_call(command.upgrade, config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    for table_name in ("audit_logs", "facts_request_logs"):
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert str(columns["request_id"]["type"]) == "VARCHAR(64)"
        assert columns["request_id"]["nullable"] is False
        indexes = {item["name"]: item for item in inspector.get_indexes(table_name)}
        assert indexes[f"ix_{table_name}_request_id"]["column_names"] == ["request_id"]
        assert not indexes[f"ix_{table_name}_request_id"]["unique"]

    # ``app.db`` creates a module-global engine from DATABASE_URL at import
    # time.  Keep the ORM proof inside the same import-isolation boundary as
    # the migration call, otherwise this test leaves a temporary-copy engine
    # and ``app.models`` in sys.modules for the session-scoped seeded_app
    # fixture that follows it.
    with isolated_import_state():
        from app.models import AuditLog, FactsRequestLog

        engine = create_engine(f"sqlite:///{database}")
        try:
            with Session(engine) as session:
                session.add(
                    AuditLog(
                        action="after_migration",
                        object_type="test",
                        object_id="2",
                        message="request id write",
                        actor="tester",
                        ip="127.0.0.1",
                        request_id="audit-request-001",
                    )
                )
                session.add(
                    FactsRequestLog(
                        project_code="P-001",
                        endpoint="/facts",
                        require_fresh=False,
                        max_age=60,
                        as_of_param="",
                        response_status=200,
                        facts_snapshot_id=None,
                        latency_ms=3,
                        error_message="",
                        actor="tester",
                        ip="127.0.0.1",
                        request_id="facts-request-001",
                        created_at="2026-08-20T00:01:00Z",
                    )
                )
                session.commit()
                assert (
                    session.query(AuditLog)
                    .filter_by(request_id="audit-request-001")
                    .count()
                    == 1
                )
                assert (
                    session.query(FactsRequestLog)
                    .filter_by(request_id="facts-request-001")
                    .count()
                    == 1
                )
        finally:
            engine.dispose()

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM facts_request_logs").fetchone()[0] == 2
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            "003_b04_tax_id"
        )


def test_explicit_alembic_config_url_is_not_redirected_by_dotenv(tmp_path, monkeypatch):
    """A temporary-copy migration must never fall through to the live .env DB."""
    database = _copy_live_database(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")

    alembic_call(command.upgrade, config, "head")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "003_b04_tax_id",
        )
        assert any(
            column[1] == "request_id"
            for column in connection.execute("PRAGMA table_info(audit_logs)").fetchall()
        )


def test_request_id_field_conflict_restores_schema_data_and_version(tmp_path, monkeypatch):
    database = _copy_live_database(tmp_path)
    config = _config(database, monkeypatch)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "ALTER TABLE audit_logs ADD COLUMN request_id VARCHAR(32) NOT NULL DEFAULT ''"
        )
        connection.execute(
            "INSERT INTO audit_logs(action, object_type, object_id, message, actor, ip) "
            "VALUES ('conflict', 'test', '1', 'should remain', 'tester', '127.0.0.1')"
        )
        connection.commit()
    before = _state(database)

    with pytest.raises(RuntimeError, match="audit_logs.request_id type"):
        alembic_call(command.upgrade, config, "head")

    assert _state(database) == before


def test_request_id_partial_failure_restores_first_table_and_version(tmp_path, monkeypatch):
    """A failure after the first request-id column is added is recoverable."""

    database = _copy_live_database(tmp_path)
    config = _config(database, monkeypatch)
    with sqlite3.connect(database) as connection:
        connection.execute('DROP TABLE "facts_request_logs"')
        connection.commit()
    before = _state(database)

    with pytest.raises(RuntimeError, match="missing required table 'facts_request_logs'"):
        alembic_call(command.upgrade, config, "head")

    assert _state(database) == before


def test_request_id_downgrade_keeps_nonempty_log_database_safe(tmp_path, monkeypatch):
    database = _copy_live_database(tmp_path)
    config = _config(database, monkeypatch)
    alembic_call(command.upgrade, config, "head")

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO audit_logs(action, object_type, object_id, message, actor, ip, request_id) "
            "VALUES ('guard', 'test', '1', 'must remain', 'tester', '127.0.0.1', 'guard-001')"
        )
        connection.commit()
    before = _state(database)

    # The newer B04 data revision must be explicitly allowed to reverse before
    # the request-id downgrade guard can be exercised.
    monkeypatch.setenv("ALLOW_B04_TAX_ID_DOWNGRADE", "1")
    with pytest.raises(RuntimeError, match="refusing to drop request_id"):
        alembic_call(command.downgrade, config, "001_initial")

    assert _state(database) == before


def test_populated_business_database_cannot_downgrade_to_base(tmp_path, monkeypatch):
    """A populated Tax database is protected even when log tables are empty.

    ``002_request_id`` can safely remove its own columns from an empty log
    table, but the following ``001_initial`` downgrade would drop every
    application table.  The migration environment must restore the complete
    pre-downgrade snapshot when that second guard rejects the operation.
    """

    database = _copy_live_database(tmp_path)
    config = _config(database, monkeypatch)
    alembic_call(command.upgrade, config, "head")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] > 0
        assert connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM facts_request_logs").fetchone()[0] == 0
    before = _state(database)

    monkeypatch.setenv("ALLOW_B04_TAX_ID_DOWNGRADE", "1")
    with pytest.raises(RuntimeError, match="refusing destructive Tax downgrade"):
        alembic_call(command.downgrade, config, "base")

    assert _state(database) == before
