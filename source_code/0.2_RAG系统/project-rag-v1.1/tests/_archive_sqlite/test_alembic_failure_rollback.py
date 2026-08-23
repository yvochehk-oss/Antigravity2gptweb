"""Alembic lifecycle and failed-migration recovery proof for RAG SQLite.

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
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


ROOT = Path(__file__).resolve().parents[1]


def _run_migration(operation, config: Config, revision: str):
    """Run one Alembic operation and restore import state immediately.

    Alembic's command layer adds ``.`` to ``sys.path`` while loading a copied
    ``env.py``.  The migration env itself restores project modules, but the
    command layer can still leave that path entry behind for the next test.
    Keep each temporary migration tree hermetic so Tax/RAG package names never
    cross-contaminate a later command in the same pytest process.
    """
    prefixes = ("app", "ai_review", "facts_provider")

    def is_shared_base_module(name: str) -> bool:
        # ``app.db`` keeps a path-scoped SQLAlchemy registry in this private
        # module.  A copied migration tree reuses the same checkout path for
        # several Alembic calls; leaving that registry alive makes the next
        # import emit duplicate declarative-class warnings and can retain the
        # copied tree's metadata in the caller.
        return name.startswith("_project_rag_shared_base_")

    def is_project_module(name: str) -> bool:
        return any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes)

    original_path = list(sys.path)
    original_modules = {
        name: module
        for name, module in sys.modules.items()
        if is_project_module(name) or is_shared_base_module(name)
    }
    try:
        return operation(config, revision)
    finally:
        sys.path[:] = original_path
        for name in list(sys.modules):
            if is_project_module(name) or is_shared_base_module(name):
                sys.modules.pop(name, None)
        sys.modules.update(original_modules)


def _copy_migration_tree(tmp_path: Path) -> Path:
    destination = tmp_path / "rag-migration-tree"
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
    monkeypatch.setenv("PROJECT_RAG_DB_URL", f"sqlite:///{database}")
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
        connection.execute(
            "CREATE INDEX ix_rollback_sentinel_value ON rollback_sentinel(value)"
        )
        connection.execute(
            "INSERT INTO rollback_sentinel(id, value) VALUES (1, 'before-failure')"
        )
        connection.commit()


def _write_failure_revision(root: Path) -> None:
    (root / "alembic" / "versions" / "002_controlled_failure.py").write_text(
        '''"""Temporary test-only revision; never installed in the source tree."""
from alembic import op
import sqlalchemy as sa

revision = "002_controlled_failure"
down_revision = "001_initial"
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


def _write_conflict_revision(root: Path) -> None:
    """Add a revision that fails at a real SQL conflict, before stamping."""
    (root / "alembic" / "versions" / "002_conflicting_sql.py").write_text(
        '''"""Temporary test-only revision; never installed in the source tree."""
from alembic import op
import sqlalchemy as sa

revision = "002_conflicting_sql"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade():
    # 001_initial already owns this table.  This must fail loudly rather than
    # being interpreted as an idempotent duplicate and stamping the revision.
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
    )


def downgrade():
    pass
''',
        encoding="utf-8",
    )


def test_failed_migration_restores_schema_indexes_data_and_version(tmp_path, monkeypatch):
    root = _copy_migration_tree(tmp_path)
    database = tmp_path / "rag-failure.db"
    config = _config(root, database, monkeypatch)

    _run_migration(command.upgrade, config, "001_initial")
    _add_sentinel(database)
    before = _sqlite_state(database)
    _write_failure_revision(root)

    with pytest.raises(RuntimeError, match="controlled migration failure"):
        _run_migration(command.upgrade, config, "head")

    assert _sqlite_state(database) == before


def test_real_sql_conflict_fails_closed_without_stamping(tmp_path, monkeypatch):
    """A duplicate-table conflict is not swallowed as an idempotent success."""
    root = _copy_migration_tree(tmp_path)
    database = tmp_path / "rag-conflict.db"
    config = _config(root, database, monkeypatch)

    _run_migration(command.upgrade, config, "001_initial")
    _add_sentinel(database)
    before = _sqlite_state(database)
    _write_conflict_revision(root)

    with pytest.raises(Exception, match="projects"):
        _run_migration(command.upgrade, config, "head")

    assert _sqlite_state(database) == before


def test_upgrade_downgrade_second_upgrade_is_idempotent(tmp_path, monkeypatch):
    root = _copy_migration_tree(tmp_path)
    database = tmp_path / "rag-lifecycle.db"
    config = _config(root, database, monkeypatch)

    _run_migration(command.upgrade, config, "head")
    first = _sqlite_state(database)
    _run_migration(command.downgrade, config, "base")
    with sqlite3.connect(database) as connection:
        remaining = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        assert remaining == [("alembic_version",)]
        assert connection.execute("SELECT * FROM alembic_version").fetchall() == []
    _run_migration(command.upgrade, config, "head")
    second = _sqlite_state(database)
    _run_migration(command.upgrade, config, "head")
    third = _sqlite_state(database)

    assert second == third
    assert second[2] == (("001_initial",),)
    assert first[0] == second[0]


def test_incompatible_existing_schema_fails_without_stamping_head(tmp_path, monkeypatch):
    """A partial pre-existing table is an error, never a successful baseline."""
    root = _copy_migration_tree(tmp_path)
    database = tmp_path / "rag-incompatible.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY)")
        connection.commit()
    config = _config(root, database, monkeypatch)

    with pytest.raises(RuntimeError, match="incompatible"):
        _run_migration(command.upgrade, config, "head")

    with sqlite3.connect(database) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        assert tables == [("projects",)]
        assert connection.execute("PRAGMA table_info(projects)").fetchall()[0][1] == "id"


def test_populated_downgrade_requires_explicit_switch_and_backup(tmp_path, monkeypatch):
    """A populated runtime DB cannot be emptied by a bare ``downgrade base``."""
    root = _copy_migration_tree(tmp_path)
    database = tmp_path / "rag-populated.db"
    config = _config(root, database, monkeypatch)
    _run_migration(command.upgrade, config, "head")
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO metric_versions
              (metric_id, version, effective_from, effective_to, deprecates,
               change_reason, formula_engine, owner, approved_by)
            VALUES ('probe', 'v1', '2026-08-20', NULL, NULL, 'test', 'deterministic', 'qa', 'qa')
            """
        )
        connection.commit()
    before = _sqlite_state(database)

    with pytest.raises(RuntimeError, match="refusing downgrade of populated"):
        _run_migration(command.downgrade, config, "base")
    assert _sqlite_state(database) == before

    backup = tmp_path / "explicit-downgrade-backup.db"
    monkeypatch.setenv("PROJECT_RAG_ALLOW_DESTRUCTIVE_DOWNGRADE", "1")
    monkeypatch.setenv("PROJECT_RAG_DOWNGRADE_BACKUP", str(backup))
    _run_migration(command.downgrade, config, "base")

    assert backup.is_file() and backup.stat().st_size > 0
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall() == [("alembic_version",)]
        assert connection.execute("SELECT * FROM alembic_version").fetchall() == []
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT * FROM alembic_version").fetchall() == [("001_initial",)]
        assert connection.execute("SELECT COUNT(*) FROM metric_versions").fetchone()[0] == 1
