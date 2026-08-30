"""V3 Phase A4 / v1.2 S0-03 — legacy party-code boundary hotfix tests.

These tests require a real PostgreSQL instance (env TEST_DATABASE_URL).
They are NOT meant to run on SQLite.  They verify the v1.2 Step 0.2 contract:

  * Legacy buyer / seller / entity / counterparty string references accept
    up to 64 characters until Party foreign keys replace them.
  * Canonical ``entities`` master identifiers are not changed by this hotfix.
  * The database must be at the exact Alembic head set before assertions run.
  * Pre-flight length checks stay within the temporary 64-character boundary.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

URL_STR = os.environ.get("TEST_DATABASE_URL", "").strip()
if not URL_STR:
    pytest.skip("TEST_DATABASE_URL is required for boundary hotfix tests", allow_module_level=True)
if not URL_STR.startswith("postgresql"):
    pytest.skip("PostgreSQL-only tests", allow_module_level=True)

URL_OBJ = make_url(URL_STR)

LEGACY_PARTY_CODE_COLUMNS = [
    ("contracts", "buyer_code"),
    ("contracts", "seller_code"),
    ("invoices", "entity_code"),
    ("invoices", "counterparty_code"),
    ("cashflows", "entity_code"),
    ("cashflows", "counterparty_code"),
    ("fulfillment", "counterparty_code"),
    ("real_costs", "entity_code"),
    ("real_costs", "counterparty_code"),
]


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(URL_OBJ, future=True, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _exec_all(engine, stmt, params=None):
    with engine.connect() as conn:
        return conn.execute(stmt, params or {}).fetchall()


def _exec_one(engine, stmt, params=None):
    rows = _exec_all(engine, stmt, params)
    return rows[0] if rows else None


def _exec_scalar(engine, stmt, params=None):
    with engine.connect() as conn:
        return conn.execute(stmt, params or {}).scalar()


def _exec_write(engine, stmt, params=None):
    with engine.begin() as conn:
        conn.execute(stmt, params or {})


def _alembic_heads_from_disk() -> set[str]:
    """Use Alembic's native revision graph instead of parsing Python text."""
    cfg = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    return set(script.get_heads())


@pytest.fixture(scope="module")
def migrated(engine):
    """Hard-fail unless DB revision rows exactly equal the Alembic head set."""
    expected_heads = _alembic_heads_from_disk()
    if not expected_heads:
        pytest.fail("No Alembic heads found")

    rows = _exec_all(engine, text("SELECT version_num FROM alembic_version_tax"))
    actual_heads = {str(row[0]) for row in rows if row and row[0]}
    if not actual_heads:
        pytest.fail(
            "alembic_version_tax is empty — migrations were never applied. "
            "Run: alembic upgrade head"
        )
    if actual_heads != expected_heads:
        pytest.fail(
            f"Stale/partial DB revision set: actual={sorted(actual_heads)}, "
            f"expected={sorted(expected_heads)}. Run: alembic upgrade head"
        )
    return actual_heads


def _table_max_len(engine, table: str, col: str) -> int:
    return int(
        _exec_scalar(
            engine,
            text(f'SELECT COALESCE(MAX(LENGTH("{col}")), 0) FROM "{table}"'),
        )
        or 0
    )


def test_legacy_party_code_columns_are_varchar_64(engine, migrated):
    """All nine v1.2 Step 0.2 legacy party-code columns are VARCHAR(64)."""
    for table, col in LEGACY_PARTY_CODE_COLUMNS:
        info = _exec_one(
            engine,
            text(
                "SELECT data_type, character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": col},
        )
        assert info is not None, f"{table}.{col} does not exist"
        dtype, length = info[0], info[1]
        assert dtype == "character varying", f"{table}.{col} must be VARCHAR, got {dtype}"
        assert length == 64, f"{table}.{col} must be VARCHAR(64), got VARCHAR({length})"


def test_canonical_entity_master_stays_varchar_16(engine, migrated):
    """The temporary legacy widening must not widen the canonical entity master."""
    for col in ("code", "entity_code"):
        info = _exec_one(
            engine,
            text(
                "SELECT data_type, character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='entities' AND column_name=:c"
            ),
            {"c": col},
        )
        assert info is not None
        assert info[0] == "character varying"
        assert info[1] == 16, f"entities.{col} canonical boundary drifted to {info[1]}"


def test_64_char_legacy_party_codes_are_accepted(engine, migrated):
    """Both legacy local-party and counterparty references accept 64 chars."""
    local_code = "ENTITY_64_CHARS_" + "E" * 48
    counterparty_code = "PARTY_64_CHARS__" + "P" * 48
    assert len(local_code) == 64
    assert len(counterparty_code) == 64

    project_id = _exec_scalar(engine, text("SELECT id FROM projects LIMIT 1"))
    if project_id is None:
        pytest.skip("no projects available")

    invoice_no = "V3S003-LEN64"
    try:
        _exec_write(
            engine,
            text(
                """
                INSERT INTO invoices (
                    project_id, invoice_no, period, entity_code, direction,
                    counterparty_code, category, net, vat, rate, deductible, note
                ) VALUES (
                    :pid, :inv, '2099-01', :entity_code, 'in',
                    :counterparty_code, 'TEST', 0, 0, 0, true, 'v3-s0-03-length-test'
                )
                """
            ),
            {
                "pid": int(project_id),
                "inv": invoice_no,
                "entity_code": local_code,
                "counterparty_code": counterparty_code,
            },
        )
        row = _exec_one(
            engine,
            text(
                "SELECT entity_code, counterparty_code "
                "FROM invoices WHERE invoice_no=:inv"
            ),
            {"inv": invoice_no},
        )
        assert row is not None
        assert row[0] == local_code
        assert row[1] == counterparty_code
    finally:
        _exec_write(
            engine,
            text("DELETE FROM invoices WHERE invoice_no=:inv"),
            {"inv": invoice_no},
        )


def test_preflight_max_len_does_not_exceed_64(engine, migrated):
    """Temporary legacy string references must not already exceed 64 chars."""
    for table, col in LEGACY_PARTY_CODE_COLUMNS:
        max_len = _table_max_len(engine, table, col)
        assert max_len <= 64, f"{table}.{col} has LENGTH {max_len} > 64"


def test_preflight_sentinel_scan_is_well_formed(engine, migrated):
    """Expose the v1.2 sentinel classes without silently normalising them."""
    sentinels = ("", "UNKNOWN", "A", "B", "C", "D")
    for table, col in LEGACY_PARTY_CODE_COLUMNS:
        row = _exec_one(
            engine,
            text(
                f'SELECT COUNT(*) FILTER (WHERE "{col}" = \'\') AS blank_count, '
                f'COUNT(*) FILTER (WHERE UPPER("{col}") = \'UNKNOWN\') AS unknown_count, '
                f'COUNT(*) FILTER (WHERE "{col}" IN (\'A\',\'B\',\'C\',\'D\')) AS virtual_role_count '
                f'FROM "{table}"'
            ),
        )
        assert row is not None
        assert all(int(value or 0) >= 0 for value in row), (table, col, row, sentinels)
