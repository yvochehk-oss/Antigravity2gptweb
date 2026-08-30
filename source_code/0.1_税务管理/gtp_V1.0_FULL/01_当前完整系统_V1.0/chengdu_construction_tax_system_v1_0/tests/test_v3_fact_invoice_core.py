"""Task 07a Fact/Invoice identity, schema and PostgreSQL contract tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import engine
from app.domain.invoice.identity import (
    InvoiceIdentityError,
    build_invoice_business_identity_key,
    build_invoice_identity_key,
)
from app.domain.invoice.service import validate_invoice_fact
from app.domain.invoice.validation import (
    InvoiceLineInput,
    InvoiceValidationInput,
    validate_invoice_values,
)
from app.v3_fact_models import Fact, InvoiceFact, InvoiceLine
from app.v3_party_models import InternalEntity

ROOT = Path(__file__).resolve().parents[1]


def test_identity_rules_are_versioned_and_null_safe():
    digital = build_invoice_identity_key(
        "DIGITAL_V1",
        invoice_number=" １２３ 456 ",
    )
    assert digital == "DIGITAL_V1|123456"

    legacy = build_invoice_identity_key(
        "LEGACY_V1",
        invoice_number=" 0008 ",
        invoice_code=" 5100 01 ",
        seller_tax_identity=" 9151 abc ",
    )
    assert legacy == "LEGACY_V1|9151ABC|510001|0008"
    assert build_invoice_business_identity_key(digital) == "INVOICE|DIGITAL_V1|123456"

    with pytest.raises(InvoiceIdentityError):
        build_invoice_identity_key("LEGACY_V1", invoice_number="1", invoice_code=None)
    with pytest.raises(InvoiceIdentityError):
        build_invoice_identity_key("UNKNOWN_V9", invoice_number="1")


def test_pure_validation_requires_balanced_header_lines_and_versioned_rate_set():
    good = InvoiceValidationInput(
        seller_party_id=1,
        buyer_party_id=2,
        invoice_number="D-1",
        invoice_date=date(2026, 8, 30),
        net_amount=Decimal("100.00"),
        vat_amount=Decimal("13.00"),
        gross_amount=Decimal("113.00"),
        lines=(
            InvoiceLineInput(
                line_no=1,
                net_amount=Decimal("100.00"),
                vat_amount=Decimal("13.00"),
                tax_rate=Decimal("0.13"),
            ),
        ),
    )
    result = validate_invoice_values(good, allowed_tax_rates={Decimal("0.13")})
    assert result.valid is True
    assert result.violations == ()

    bad = replace(good, gross_amount=Decimal("112.00"))
    result = validate_invoice_values(bad, allowed_tax_rates={Decimal("0.09")})
    assert result.valid is False
    assert "HEADER_AMOUNT_UNBALANCED" in result.violations
    assert "LINE_GROSS_SUM_MISMATCH" in result.violations
    assert "LINE_1_TAX_RATE_NOT_ALLOWED" in result.violations

    result = validate_invoice_values(good, allowed_tax_rates=())
    assert "VERSIONED_TAX_RATE_RULES_REQUIRED" in result.violations


def test_task07a_schema_has_single_fact_semantics_and_real_fks():
    invoice_table = InvoiceFact.__table__
    line_table = InvoiceLine.__table__
    fact_table = Fact.__table__

    prohibited = {"direction", "internal_trade", "deductible", "project_id"}
    assert prohibited.isdisjoint(invoice_table.c.keys())
    assert "deductible" not in line_table.c
    assert tuple(fact_table.primary_key.columns.keys()) == ("id",)
    assert tuple(invoice_table.primary_key.columns.keys()) == ("fact_id",)

    invoice_fk_targets = {
        element.target_fullname
        for constraint in invoice_table.foreign_key_constraints
        for element in constraint.elements
    }
    assert "facts.id" in invoice_fk_targets
    assert "parties.id" in invoice_fk_targets


def test_revision_77_is_additive_and_follows_revision_76():
    migration = (
        ROOT / "alembic" / "versions" / "77_v3_fact_core_invoice.py"
    ).read_text(encoding="utf-8")
    upper = migration.upper()
    assert 'down_revision = "76_v3_party_migration_conflicts"' in migration
    assert "DROP TABLE INVOICES" not in upper
    assert "CREATE VIEW INVOICES_COMPAT" not in upper
    assert "FACT_TYPE + FACT_ID" not in upper


def _internal_party_ids(session: Session) -> tuple[int, int]:
    ids = session.execute(
        select(InternalEntity.party_id).order_by(InternalEntity.party_id).limit(2)
    ).scalars().all()
    assert len(ids) == 2
    return int(ids[0]), int(ids[1])


def test_postgresql_internal_invoice_is_one_fact_and_identity_is_unique(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            seller_id, buyer_id = _internal_party_ids(session)
            key = "DIGITAL_V1|TASK07A-UNIQUE-001"

            fact = Fact(
                fact_type="INVOICE",
                business_identity_key=f"INVOICE|{key}",
                validation_status="DRAFT",
            )
            session.add(fact)
            session.flush()
            session.add(
                InvoiceFact(
                    fact_id=fact.id,
                    seller_party_id=seller_id,
                    buyer_party_id=buyer_id,
                    invoice_identity_key=key,
                    invoice_identity_version="DIGITAL_V1",
                    invoice_number="TASK07A-UNIQUE-001",
                    currency="CNY",
                )
            )
            session.flush()

            assert session.scalar(
                select(func.count()).select_from(InvoiceFact).where(
                    InvoiceFact.invoice_identity_key == key
                )
            ) == 1

            duplicate_fact = Fact(
                fact_type="INVOICE",
                business_identity_key="INVOICE|DIGITAL_V1|TASK07A-DIFFERENT-BUSINESS-KEY",
                validation_status="DRAFT",
            )
            session.add(duplicate_fact)
            session.flush()
            session.add(
                InvoiceFact(
                    fact_id=duplicate_fact.id,
                    seller_party_id=seller_id,
                    buyer_party_id=buyer_id,
                    invoice_identity_key=key,
                    invoice_identity_version="DIGITAL_V1",
                    invoice_number="TASK07A-UNIQUE-001",
                    currency="CNY",
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()
        finally:
            session.close()
            tx.rollback()


def test_postgresql_unbalanced_invoice_cannot_promote_to_valid(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            seller_id, buyer_id = _internal_party_ids(session)
            key = "DIGITAL_V1|TASK07A-VALIDATE-001"
            fact = Fact(
                fact_type="INVOICE",
                business_identity_key=f"INVOICE|{key}",
                validation_status="DRAFT",
            )
            session.add(fact)
            session.flush()
            session.add(
                InvoiceFact(
                    fact_id=fact.id,
                    seller_party_id=seller_id,
                    buyer_party_id=buyer_id,
                    invoice_identity_key=key,
                    invoice_identity_version="DIGITAL_V1",
                    invoice_number="TASK07A-VALIDATE-001",
                    invoice_date=date(2026, 8, 30),
                    net_amount=Decimal("100.00"),
                    vat_amount=Decimal("13.00"),
                    gross_amount=Decimal("112.00"),
                    currency="CNY",
                )
            )
            session.add(
                InvoiceLine(
                    invoice_fact_id=fact.id,
                    line_no=1,
                    net_amount=Decimal("100.00"),
                    vat_amount=Decimal("13.00"),
                    tax_rate=Decimal("0.13"),
                )
            )
            session.flush()

            result = validate_invoice_fact(
                session,
                fact.id,
                allowed_tax_rates={Decimal("0.13")},
                promote=True,
            )
            assert result.valid is False
            assert "HEADER_AMOUNT_UNBALANCED" in result.violations
            assert fact.validation_status == "NEEDS_REVIEW"
        finally:
            session.close()
            tx.rollback()
