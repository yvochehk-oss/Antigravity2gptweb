"""Canonical entity master and migration acceptance tests."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_CODES = {
    *(f"A{i:02d}" for i in range(1, 12)),
    *(f"B{i:02d}" for i in range(1, 11)),
    "C01", "C02", "D01", "D02", "D03",
}
VIRTUAL_CODES = {"A", "B", "C", "D", "甲", "乙", "丙", "丁"}


def test_fresh_seed_has_only_canonical_entities(seeded_app):
    from app.db import SessionLocal
    from app.models import Entity, ExternalParty

    db = SessionLocal()
    try:
        entities = db.query(Entity).order_by(Entity.code).all()
        assert {entity.code for entity in entities} == CANONICAL_CODES
        assert not ({entity.code for entity in entities} & VIRTUAL_CODES)
        assert len(entities) == 26
        assert sum(bool(entity.legal_entity) for entity in entities) == 25

        branch = db.query(Entity).filter(Entity.code == "A04").one()
        assert branch.legal_entity is False
        assert branch.parent_entity_code == "A03"
        assert branch.active is True

        tax_ids = [entity.tax_id for entity in entities if entity.tax_id]
        assert len(tax_ids) == len(set(tax_ids)) == 26
        assert db.query(ExternalParty).count() == 5
        assert not db.query(Entity).filter(Entity.business_role == Entity.code).count()
    finally:
        db.close()


def test_b04_seed_uses_cross_system_canonical_tax_id(seeded_app):
    from app.db import SessionLocal
    from app.models import Entity
    from app.seed import ENTITIES_MASTER

    seed_row = next(row for row in ENTITIES_MASTER if row[0] == "B04")
    assert seed_row[4] == "91511526MA67UN7C2G"

    db = SessionLocal()
    try:
        assert db.query(Entity).filter(Entity.code == "B04").one().tax_id == seed_row[4]
    finally:
        db.close()


def test_supporting_entity_schema_is_available(seeded_app):
    from app.db import SessionLocal
    from app.models import CashFlow, EntityBankAccount, TaxLedger, TaxPaymentRecord

    assert {"transaction_date", "bank_reference", "source_fingerprint"}.issubset(
        CashFlow.__table__.columns.keys()
    )
    assert {"entity_code", "account_no"}.issubset(EntityBankAccount.__table__.columns.keys())
    assert {"entity_code", "receipt_no", "source_fingerprint"}.issubset(
        TaxPaymentRecord.__table__.columns.keys()
    )
    assert {"period", "entity_code"}.issubset(TaxLedger.__table__.columns.keys())
    assert any(
        constraint.name == "uq_tax_ledger_period_entity"
        for constraint in TaxLedger.__table__.constraints
    )
    SessionLocal().close()


def test_sqlite_foreign_keys_are_enabled_for_application_connections(seeded_app):
    from app.db import engine

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_legacy_database_migration_is_safe_and_idempotent(tmp_path):
    source = ROOT / "data" / "demo.db"
    target = tmp_path / "migration-copy.db"
    shutil.copy2(source, target)

    from scripts.migrate_canonical_entities import migrate_database

    migrate_database(target, backup=False)
    with sqlite3.connect(target) as conn:
        first_dump = list(conn.iterdump())
        codes = {row[0] for row in conn.execute("SELECT code FROM entities")}
        assert codes == CANONICAL_CODES
        assert not codes & VIRTUAL_CODES
        assert conn.execute(
            "SELECT COUNT(*) FROM entities WHERE legal_entity=1"
        ).fetchone()[0] == 25
        assert conn.execute(
            "SELECT parent_entity_code FROM entities WHERE code='A04'"
        ).fetchone()[0] == "A03"
        assert conn.execute(
            "SELECT COUNT(*) FROM entities WHERE tax_id IS NOT NULL GROUP BY tax_id HAVING COUNT(*)>1"
        ).fetchone() is None
        assert conn.execute(
            "SELECT COUNT(*) FROM external_parties WHERE code LIKE 'EXT-%'"
        ).fetchone()[0] == 5
        assert conn.execute(
            "SELECT COUNT(*) FROM cashflows WHERE entity_code IN ('A','B','C','D')"
        ).fetchone()[0] == 0

    # A second up must preserve the complete logical SQLite dump.
    migrate_database(target, backup=False)
    with sqlite3.connect(target) as conn:
        assert list(conn.iterdump()) == first_dump


def test_failed_migration_rolls_back_without_partial_schema_or_data(tmp_path):
    source = ROOT / "data" / "demo.db"
    target = tmp_path / "migration-rollback-copy.db"
    shutil.copy2(source, target)

    with sqlite3.connect(target) as conn:
        conn.execute(
            "UPDATE invoices SET entity_code='UNRESOLVED' "
            "WHERE id=(SELECT MIN(id) FROM invoices)"
        )
        conn.commit()
        before_dump = list(conn.iterdump())

    from scripts.migrate_canonical_entities import MigrationError, migrate_database

    with pytest.raises(MigrationError, match="unresolved entity/counterparty"):
        migrate_database(target, backup=False)

    with sqlite3.connect(target) as conn:
        assert list(conn.iterdump()) == before_dump
        assert conn.execute(
            "SELECT entity_code FROM invoices WHERE id=(SELECT MIN(id) FROM invoices)"
        ).fetchone()[0] == "UNRESOLVED"
