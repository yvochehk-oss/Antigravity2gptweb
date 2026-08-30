"""V3 Phase A4 — counterparty / buyer / seller column relaxation.

These tests require a real PostgreSQL instance (env DATABASE_URL).  They
are NOT meant to run on SQLite.  They verify the v1.1 §A4 contract:

  * Counterparty / buyer / seller columns accept up to 64 characters.
  * Internal canonical ``entity_code`` columns stay at VARCHAR(16) — a
    legacy 16-char constraint rejecter (e.g. char(16)) must remain
    strictly enforced by PostgreSQL.
  * Pre-flight report rows are well-formed (length / sentinel counts).
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[1]   # TAX_APP root
sys.path.insert(0, str(ROOT))

URL_STR = os.environ.get("TEST_DATABASE_URL", "").strip()
if not URL_STR:
    pytest.skip("TEST_DATABASE_URL is required for boundary hotfix tests", allow_module_level=True)
if not URL_STR.startswith("postgresql"):
    pytest.skip("PostgreSQL-only tests", allow_module_level=True)

URL_OBJ = make_url(URL_STR)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(URL_OBJ, future=True, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _exec_one(engine, stmt, params=None):
    """SQLAlchemy 2.0 compatible single-row fetch helper."""
    with engine.connect() as conn:
        return conn.execute(stmt, params).fetchone()


def _exec_scalar(engine, stmt, params=None):
    with engine.connect() as conn:
        return conn.execute(stmt, params).scalar()


def _exec_write(engine, stmt, params=None):
    with engine.begin() as conn:
        conn.execute(stmt, params)


@pytest.fixture(scope="module")
def migrated(engine):
    """Ensure alembic head 72 is applied; otherwise skip."""
    row = _exec_one(engine, text("SELECT version_num FROM alembic_version_tax LIMIT 1"))
    if row is None:
        pytest.skip("alembic_version_tax empty — run migrations first")
    return row[0]


def _table_max_len(engine, table: str, col: str) -> int:
    return int(_exec_scalar(
        engine, text(f"SELECT COALESCE(MAX(LENGTH({col})), 0) FROM {table}")
    ) or 0)


def test_counterparty_columns_are_varchar_64(engine, migrated):
    """After alembic 72, these columns must accept 64-char strings."""
    cols = [
        ("contracts", "buyer_code"),
        ("contracts", "seller_code"),
        ("invoices", "counterparty_code"),
        ("cashflows", "counterparty_code"),
        ("fulfillment", "counterparty_code"),
        ("real_costs", "counterparty_code"),
    ]
    for table, col in cols:
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
        assert dtype == "character varying", (
            f"{table}.{col} must be VARCHAR, got {dtype}"
        )
        assert length == 64, (
            f"{table}.{col} must be VARCHAR(64), got VARCHAR({length})"
        )


def test_internal_entity_code_unchanged_at_varchar_16(engine, migrated):
    """Internal canonical entity_code MUST stay VARCHAR(16)."""
    for table, col in [
        ("invoices", "entity_code"),
        ("cashflows", "entity_code"),
        ("real_costs", "entity_code"),
    ]:
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
        assert dtype == "character varying", (
            f"{table}.{col} must be VARCHAR, got {dtype}"
        )
        assert length == 16, (
            f"INTERNAL entity_code {table}.{col} must stay VARCHAR(16); "
            f"v1.1 §纠偏 #4 forbids relaxing it.  Got VARCHAR({length})."
        )


def test_64_char_counterparty_is_accepted(engine, migrated):
    """Insert a 64-char counterparty into invoices — must not error."""
    long_code = "VENDOR_64_CHARS_" + "X" * 48  # 16 + 48 = 64
    assert len(long_code) == 64, len(long_code)
    # Use a real project_id; create a transient test row in a transaction
    project_id = _exec_scalar(engine, text("SELECT id FROM projects LIMIT 1"))
    if project_id is None:
        pytest.skip("no projects available")
    try:
        _exec_write(
            engine,
            text(
                """
                INSERT INTO invoices (
                    project_id, invoice_no, period, entity_code, direction,
                    counterparty_code, category, net, vat, rate, deductible, note
                ) VALUES (
                    :pid, :inv, '2099-01', 'A01', 'in',
                    :code, 'TEST', 0, 0, 0, true, 'v3-a4-length-test'
                )
                """
            ),
            {"pid": int(project_id), "inv": "V3A4LEN64", "code": long_code},
        )
        row = _exec_one(
            engine,
            text("SELECT counterparty_code FROM invoices WHERE invoice_no=:inv"),
            {"inv": "V3A4LEN64"},
        )
        assert row is not None
        assert row[0] == long_code, (
            f"64-char counterparty was silently truncated to {len(row[0])}"
        )
    finally:
        _exec_write(
            engine,
            text("DELETE FROM invoices WHERE invoice_no = :inv"),
            {"inv": "V3A4LEN64"},
        )


def test_preflight_max_len_does_not_exceed_64(engine, migrated):
    """After migration, no column should have max_len > 64."""
    cols = [
        ("contracts", "buyer_code"),
        ("contracts", "seller_code"),
        ("invoices", "counterparty_code"),
        ("cashflows", "counterparty_code"),
        ("fulfillment", "counterparty_code"),
        ("real_costs", "counterparty_code"),
    ]
    for table, col in cols:
        max_len = _table_max_len(engine, table, col)
        assert max_len <= 64, (
            f"{table}.{col} has rows with LENGTH > 64 ({max_len}); "
            "the migration must have run."
        )
