"""Alembic lifecycle and failed-migration recovery proof for Tax SQLite.

SQLite DDL is not treated as transactional by Alembic.  The migration env
therefore takes a SQLite backup and restores it when a migration raises.  This
test deliberately mutates schema, indexes, and data before raising, then
compares the complete SQLite schema/data/version state with the pre-failure
snapshot.  The source database is never used; each case runs in a temporary
copied migration tree and database.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from _alembic_test_utils import alembic_call
from alembic.config import Config

from alembic import command

ROOT = Path(__file__).resolve().parents[1]


def _copy_migration_tree(tmp_path: Path) -> Path:
    destination = tmp_path / "tax-migration-tree"
    shutil.copytree(
        ROOT,
        destination,
        ignore=shutil.ignore_patterns(
            ".venv",
            ".pytest_cache",
            ".ruff_cache",
            ".env",
            "data",
            "*.db",
            "*.bak",
            "*.pyc",
            "__pycache__",
        ),
    )
    return destination


def _config(root: Path, database: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


def _sqlite_state(database: Path) -> tuple[tuple, dict[str, tuple], tuple]:
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


def _add_sentinel(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE rollback_sentinel (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("CREATE INDEX ix_rollback_sentinel_value ON rollback_sentinel(value)")
        connection.execute("INSERT INTO rollback_sentinel(id, value) VALUES (1, 'before-failure')")
        connection.commit()


def _write_failure_revision(root: Path) -> None:
    (root / "alembic" / "versions" / "004_controlled_failure.py").write_text(
        '''"""Temporary test-only revision; never installed in the source tree."""
from alembic import op
import sqlalchemy as sa

revision = "004_controlled_failure"
down_revision = "003_b04_tax_id"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rollback_probe",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("value", sa.String(40), nullable=False),
    )
    op.create_index("ix_rollback_probe_value", "rollback_probe", ["value"])
    op.execute(
        sa.text("INSERT INTO rollback_probe(id, value) VALUES (1, 'must-disappear')")
    )
    op.execute(
        sa.text(
            "UPDATE rollback_sentinel SET value = 'must-be-restored' WHERE id = 1"
        )
    )
    raise RuntimeError("controlled migration failure for rollback proof")


def downgrade():
    op.drop_index("ix_rollback_probe_value", table_name="rollback_probe")
    op.drop_table("rollback_probe")
''',
        encoding="utf-8",
    )


def _write_incompatible_entities(database: Path) -> None:
    """Create a legacy-looking table with one incompatible field definition."""
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);
            CREATE TABLE entities (
                id INTEGER PRIMARY KEY,
                code VARCHAR(4) NOT NULL,
                name VARCHAR(100) NOT NULL,
                kind VARCHAR(30) NOT NULL,
                internal BOOLEAN NOT NULL,
                tax_id VARCHAR(40),
                short_name VARCHAR(60) NOT NULL,
                business_role VARCHAR(1) NOT NULL,
                legal_entity BOOLEAN NOT NULL,
                parent_entity_code VARCHAR(8),
                active BOOLEAN NOT NULL
            );
            INSERT INTO entities(
                id, code, name, kind, internal, tax_id, short_name,
                business_role, legal_entity, parent_entity_code, active
            ) VALUES (1, 'A01', '冲突测试', 'construction', 1, NULL, '冲突',
                      'A', 1, NULL, 1);
            """
        )
        connection.commit()


def test_incompatible_existing_field_aborts_without_stamping_or_data_loss(tmp_path, monkeypatch):
    root = _copy_migration_tree(tmp_path)
    database = tmp_path / "tax-field-conflict.db"
    _write_incompatible_entities(database)
    config = _config(root, database, monkeypatch)
    before = _sqlite_state(database)

    with pytest.raises(RuntimeError, match="entities.*code.*type"):
        alembic_call(command.upgrade, config, "head")

    # The environment restores the SQLite snapshot after the failed schema
    # check.  In particular, Alembic must not stamp 001_initial or leave a
    # half-created table/index behind.
    assert _sqlite_state(database) == before


def test_failed_migration_restores_schema_indexes_data_and_version(tmp_path, monkeypatch):
    root = _copy_migration_tree(tmp_path)
    database = tmp_path / "tax-failure.db"
    config = _config(root, database, monkeypatch)

    alembic_call(command.upgrade, config, "001_initial")
    _add_sentinel(database)
    before = _sqlite_state(database)
    _write_failure_revision(root)

    with pytest.raises(RuntimeError, match="controlled migration failure"):
        alembic_call(command.upgrade, config, "head")

    assert _sqlite_state(database) == before


def test_upgrade_downgrade_second_upgrade_is_idempotent(tmp_path, monkeypatch):
    root = _copy_migration_tree(tmp_path)
    database = tmp_path / "tax-lifecycle.db"
    config = _config(root, database, monkeypatch)

    alembic_call(command.upgrade, config, "head")
    first = _sqlite_state(database)
    alembic_call(command.downgrade, config, "base")
    with sqlite3.connect(database) as connection:
        remaining = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        assert remaining == [("alembic_version",)]
        assert connection.execute("SELECT * FROM alembic_version").fetchall() == []
    alembic_call(command.upgrade, config, "head")
    second = _sqlite_state(database)
    alembic_call(command.upgrade, config, "head")
    third = _sqlite_state(database)

    assert second == third
    assert second[2] == (("003_b04_tax_id",),)
    assert first[0] == second[0]
