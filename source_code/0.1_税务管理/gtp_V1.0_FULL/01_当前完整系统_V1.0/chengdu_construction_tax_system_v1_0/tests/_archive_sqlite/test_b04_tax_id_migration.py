"""Acceptance tests for the B04 canonical tax-id data migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from _alembic_test_utils import alembic_call
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
OLD_TAX_ID = "91510115MA67UN7C2G"
CANONICAL_TAX_ID = "91511526MA67UN7C2G"


def _copy_live_database(tmp_path: Path) -> Path:
    source = ROOT / "data" / "demo.db"
    target = tmp_path / "b04-tax-id-copy.db"
    with (
        sqlite3.connect(source) as source_connection,
        sqlite3.connect(target) as target_connection,
    ):
        source_connection.backup(target_connection)
    return target


def _config(database: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.delenv("ALLOW_B04_TAX_ID_DOWNGRADE", raising=False)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


def _set_tax_id_and_version(
    database: Path, tax_id: str, version: str = "002_request_id"
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE entities SET tax_id = ? WHERE code = 'B04'",
            (tax_id,),
        )
        connection.execute(
            "UPDATE alembic_version SET version_num = ?",
            (version,),
        )
        connection.commit()


def _business_counts(database: Path) -> dict[str, int]:
    with sqlite3.connect(database) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name <> 'alembic_version' "
                "ORDER BY name"
            )
        ]
        return {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }


def _state(database: Path) -> tuple[tuple[str, ...], tuple[tuple, ...]]:
    with sqlite3.connect(database) as connection:
        dump = tuple(connection.iterdump())
        version = tuple(
            connection.execute(
                "SELECT version_num FROM alembic_version ORDER BY version_num"
            ).fetchall()
        )
    return dump, version


def _b04_tax_id(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT tax_id FROM entities WHERE code = 'B04'"
        ).fetchone()[0]


def test_upgrade_repairs_known_old_value_without_changing_business_counts(tmp_path, monkeypatch):
    database = _copy_live_database(tmp_path)
    _set_tax_id_and_version(database, OLD_TAX_ID)
    config = _config(database, monkeypatch)
    before_counts = _business_counts(database)

    alembic_call(command.upgrade, config, "head")

    assert _b04_tax_id(database) == CANONICAL_TAX_ID
    assert _business_counts(database) == before_counts
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("003_b04_tax_id",)


def test_upgrade_accepts_already_canonical_value_and_is_idempotent(tmp_path, monkeypatch):
    database = _copy_live_database(tmp_path)
    _set_tax_id_and_version(database, CANONICAL_TAX_ID)
    config = _config(database, monkeypatch)

    alembic_call(command.upgrade, config, "head")
    first_dump, first_version = _state(database)
    alembic_call(command.upgrade, config, "head")
    second_dump, second_version = _state(database)

    assert _b04_tax_id(database) == CANONICAL_TAX_ID
    assert first_dump == second_dump
    assert first_version == second_version == (("003_b04_tax_id",),)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            "UPDATE entities SET tax_id = 'UNEXPECTED' WHERE code = 'B04'",
            "unexpected current value",
        ),
        (
            "UPDATE entities SET name = '另一家公司' WHERE code = 'B04'",
            "name conflict",
        ),
    ],
)
def test_untrusted_b04_state_fails_closed_and_restores_snapshot(
    tmp_path, monkeypatch, mutation: str, message: str
):
    database = _copy_live_database(tmp_path)
    _set_tax_id_and_version(database, CANONICAL_TAX_ID)
    with sqlite3.connect(database) as connection:
        connection.execute(mutation)
        connection.commit()
    config = _config(database, monkeypatch)
    before = _state(database)

    with pytest.raises(RuntimeError, match=message):
        alembic_call(command.upgrade, config, "head")

    assert _state(database) == before


def test_downgrade_requires_explicit_opt_in_and_can_be_reupgraded(tmp_path, monkeypatch):
    database = _copy_live_database(tmp_path)
    config = _config(database, monkeypatch)
    alembic_call(command.upgrade, config, "head")
    before = _state(database)

    with pytest.raises(RuntimeError, match="refusing destructive Tax downgrade"):
        alembic_call(command.downgrade, config, "002_request_id")
    assert _state(database) == before

    monkeypatch.setenv("ALLOW_B04_TAX_ID_DOWNGRADE", "1")
    alembic_call(command.downgrade, config, "002_request_id")
    assert _b04_tax_id(database) == OLD_TAX_ID
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("002_request_id",)

    monkeypatch.delenv("ALLOW_B04_TAX_ID_DOWNGRADE", raising=False)
    alembic_call(command.upgrade, config, "head")
    assert _b04_tax_id(database) == CANONICAL_TAX_ID
