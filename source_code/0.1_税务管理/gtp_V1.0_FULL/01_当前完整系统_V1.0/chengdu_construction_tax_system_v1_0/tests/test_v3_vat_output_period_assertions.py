"""Task14b reviewed Output VAT period assertion contracts."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db import engine
from app.v3_party_models import InternalEntity, Party
from app.v3_period_models import CalculationRun
from app.v3_vat_ledger_models import EntityVatLedger
from app.v3_vat_review_models import VatOutputPeriodAssertion

ROOT = Path(__file__).resolve().parents[1]


def _scope(session: Session, suffix: str) -> tuple[int, int]:
    party = Party(
        code=f"T14B-{suffix}",
        name=f"Task14b {suffix}",
        short_name="T14B",
        party_type="internal",
        active=True,
    )
    session.add(party)
    session.flush()
    entity = InternalEntity(
        party_id=party.id,
        canonical_code=f"B{suffix[-6:]}",
        business_role="A",
        legal_entity=True,
        active=True,
    )
    session.add(entity)
    session.flush()
    run = CalculationRun(
        reporting_party_id=party.id,
        tax_type="VAT",
        tax_period=date(2026, 1, 1),
        run_kind="STANDARD",
        run_status="SUCCEEDED",
        ruleset_version="TEST_VAT_OUTPUT_ASSERTION",
        input_snapshot_sha256="a" * 64,
        result_sha256="b" * 64,
        created_by="pytest",
        completed_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()
    return party.id, run.id


def _ledger(run_id: int, party_id: int, output_vat: str) -> EntityVatLedger:
    output = Decimal(output_vat)
    return EntityVatLedger(
        calculation_run_id=run_id,
        reporting_party_id=party_id,
        tax_period=date(2026, 1, 1),
        opening_input_credit=Decimal("0.00"),
        output_vat=output,
        input_vat=Decimal("0.00"),
        tax_prepayment=Decimal("0.00"),
        vat_payable_before_prepayment=max(output, Decimal("0.00")),
        closing_input_credit=max(-output, Decimal("0.00")),
        vat_payable_after_prepayment=max(output, Decimal("0.00")),
        unapplied_tax_prepayment=Decimal("0.00"),
    )


def test_revision_85_is_additive_and_does_not_rewrite_revision_84():
    migration = (ROOT / "alembic" / "versions" / "85_v3_vat_output_period_assertions.py").read_text(encoding="utf-8")
    assert 'down_revision = "84_v3_entity_vat_ledgers"' in migration
    assert '"vat_output_period_assertions"' in migration
    assert "trg_v3_guard_entity_vat_ledger_output_completeness" in migration
    assert "ALTER TABLE OUTPUT_VAT_EVENTS" not in migration.upper()


def test_output_assertion_schema_allows_explicit_reviewed_zero():
    table = VatOutputPeriodAssertion.__table__
    assert "asserted_output_vat_total" in table.c
    assert "invoice_date" not in table.c
    assert "project_id" not in table.c
    assert "deductible" not in table.c


def test_database_rejects_ledger_without_reviewed_output_assertion():
    with Session(engine) as session:
        party_id, run_id = _scope(session, "NOASSERT")
        session.flush()
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.add(_ledger(run_id, party_id, "0.00"))
                session.flush()
        session.rollback()


def test_reviewed_zero_assertion_allows_zero_output_ledger():
    with Session(engine) as session:
        party_id, run_id = _scope(session, "ZEROOK")
        session.add(
            VatOutputPeriodAssertion(
                reporting_party_id=party_id,
                tax_period=date(2026, 1, 1),
                asserted_output_vat_total=Decimal("0.00"),
                source="reviewed monthly VAT source confirms zero Output VAT",
                reviewed=True,
                reviewed_by="pytest",
                reviewed_at=datetime.now(timezone.utc),
            )
        )
        session.flush()
        ledger = _ledger(run_id, party_id, "0.00")
        session.add(ledger)
        session.flush()
        assert ledger.id is not None
        session.rollback()


def test_database_rejects_assertion_when_confirmed_event_total_does_not_match():
    with Session(engine) as session:
        party_id, run_id = _scope(session, "MISMATCH")
        session.add(
            VatOutputPeriodAssertion(
                reporting_party_id=party_id,
                tax_period=date(2026, 1, 1),
                asserted_output_vat_total=Decimal("10.00"),
                source="reviewed total",
                reviewed=True,
                reviewed_by="pytest",
                reviewed_at=datetime.now(timezone.utc),
            )
        )
        session.flush()
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.add(_ledger(run_id, party_id, "10.00"))
                session.flush()
        session.rollback()
