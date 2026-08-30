"""Task14 Entity VAT Ledger schema, formula and PostgreSQL contracts."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import engine
from app.domain.vat_ledger import calculate_vat_ledger, earliest_changed_period, rebuild_periods
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity, Party
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_vat_ledger_models import EntityVatLedger, OutputVatEvent, VatOpeningBalanceSeed
from app.v3_vat_review_models import VatOutputPeriodAssertion
from scripts.v3 import entity_vat_ledger_85 as ledger85

build_one = ledger85.base.build_one
ROOT = Path(__file__).resolve().parents[1]


def test_vat_formula_keeps_prepayment_out_of_input_credit():
    result = calculate_vat_ledger(
        opening_input_credit=Decimal("20.00"),
        output_vat=Decimal("100.00"),
        input_vat=Decimal("30.00"),
        tax_prepayment=Decimal("40.00"),
    )
    assert result.vat_payable_before_prepayment == Decimal("50.00")
    assert result.closing_input_credit == Decimal("0.00")
    assert result.vat_payable_after_prepayment == Decimal("10.00")
    assert result.unapplied_tax_prepayment == Decimal("0.00")

    credit = calculate_vat_ledger(
        opening_input_credit=Decimal("20.00"),
        output_vat=Decimal("10.00"),
        input_vat=Decimal("30.00"),
        tax_prepayment=Decimal("7.00"),
    )
    assert credit.vat_payable_before_prepayment == Decimal("0.00")
    assert credit.closing_input_credit == Decimal("40.00")
    assert credit.vat_payable_after_prepayment == Decimal("0.00")
    assert credit.unapplied_tax_prepayment == Decimal("7.00")


def test_rebuild_starts_from_earliest_changed_period():
    assert earliest_changed_period([date(2026, 9, 15), date(2026, 7, 31), date(2026, 8, 1)]) == date(2026, 7, 1)
    assert rebuild_periods(date(2026, 7, 20), date(2026, 10, 2)) == (
        date(2026, 7, 1),
        date(2026, 8, 1),
        date(2026, 9, 1),
        date(2026, 10, 1),
    )


def test_revision_84_separates_output_period_and_uses_typed_component_sources():
    migration = (ROOT / "alembic" / "versions" / "84_v3_entity_vat_ledgers.py").read_text(encoding="utf-8")
    assert 'down_revision = "83_v3_calculation_runs_period_states"' in migration
    assert '"output_vat_events"' in migration
    assert '"output_vat_period"' in migration
    assert '"entity_vat_ledgers"' in migration
    assert '"entity_vat_ledger_components"' in migration
    assert '"prior_ledger_id"' in migration
    assert '"opening_balance_seed_id"' in migration
    upper = migration.upper()
    assert "ALTER TABLE INVOICE_FACTS" not in upper
    assert "INVOICE_DATE" not in migration


def test_vat_ledger_schema_has_no_project_or_invoice_period_axis():
    from app.v3_vat_ledger_models import EntityVatLedgerComponent

    ledger = EntityVatLedger.__table__
    event = OutputVatEvent.__table__
    prohibited_ledger = {"project_id", "invoice_date", "deductible", "cashflow_id", "recognized_revenue", "cost_amount"}
    prohibited_event = {"invoice_date", "project_id", "direction", "deductible", "legacy_period"}
    assert prohibited_ledger.isdisjoint(ledger.c.keys())
    assert prohibited_event.isdisjoint(event.c.keys())

    component = EntityVatLedgerComponent.__table__
    targets = {
        element.target_fullname
        for constraint in component.foreign_key_constraints
        for element in constraint.elements
    }
    assert "output_vat_events.id" in targets
    assert "input_vat_claims.id" in targets
    assert "tax_prepayment_facts.fact_id" in targets
    assert "entity_vat_ledgers.id" in targets
    assert "vat_opening_balance_seeds.id" in targets


def _create_output_context(session: Session, suffix: str):
    seller = Party(
        code=f"T14-SELLER-{suffix}",
        name=f"Task14 Seller {suffix}",
        short_name="T14S",
        party_type="internal",
        active=True,
    )
    buyer = Party(
        code=f"T14-BUYER-{suffix}",
        name=f"Task14 Buyer {suffix}",
        short_name="T14B",
        party_type="external",
        active=True,
    )
    session.add_all([seller, buyer])
    session.flush()
    entity = InternalEntity(
        party_id=seller.id,
        canonical_code=f"T14{suffix[-4:]}",
        business_role="A",
        legal_entity=True,
        active=True,
    )
    session.add(entity)
    fact = Fact(
        fact_type="INVOICE",
        business_identity_key=f"INVOICE|DIGITAL_V1|T14-{suffix}",
        validation_status="VALID",
    )
    session.add(fact)
    session.flush()
    invoice = InvoiceFact(
        fact_id=fact.id,
        seller_party_id=seller.id,
        buyer_party_id=buyer.id,
        invoice_identity_key=f"DIGITAL_V1|T14-{suffix}",
        invoice_identity_version="DIGITAL_V1",
        invoice_number=f"T14-{suffix}",
        invoice_date=date(2026, 1, 20),
        invoice_status="VALID",
        gross_amount=Decimal("113.00"),
        net_amount=Decimal("100.00"),
        vat_amount=Decimal("13.00"),
        currency="CNY",
    )
    session.add(invoice)
    session.flush()
    return entity, fact, invoice


def test_postgresql_legacy_output_assumption_cannot_be_confirmed(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            entity, _, invoice = _create_output_context(session, "CHK1")
            nested = session.begin_nested()
            session.add(
                OutputVatEvent(
                    invoice_fact_id=invoice.fact_id,
                    reporting_party_id=entity.party_id,
                    output_vat_period=date(2026, 2, 1),
                    vat_amount=Decimal("13.00"),
                    event_type="OUTPUT",
                    event_status="CONFIRMED",
                    evidence_type="LEGACY_ASSUMPTION",
                    confidence="LOW",
                    reviewed_by="pytest",
                    reviewed_at=datetime.now(timezone.utc),
                    source_system="pytest",
                    external_event_id="T14-LEGACY-BAD",
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()
            nested.rollback()
        finally:
            session.close()
            tx.rollback()


def test_postgresql_two_month_ledger_credit_continuity(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            entity, _, invoice = _create_output_context(session, "FLOW")
            reviewed_at = datetime.now(timezone.utc)
            session.add(
                VatOpeningBalanceSeed(
                    reporting_party_id=entity.party_id,
                    tax_period=date(2026, 2, 1),
                    opening_input_credit=Decimal("20.00"),
                    source="pytest reviewed opening",
                    reviewed=True,
                    reviewed_by="pytest",
                    reviewed_at=reviewed_at,
                )
            )
            session.add(
                OutputVatEvent(
                    invoice_fact_id=invoice.fact_id,
                    reporting_party_id=entity.party_id,
                    output_vat_period=date(2026, 2, 1),
                    vat_amount=Decimal("13.00"),
                    event_type="OUTPUT",
                    event_status="CONFIRMED",
                    evidence_type="MANUAL_REVIEW",
                    confidence="HIGH",
                    reviewed_by="pytest",
                    reviewed_at=reviewed_at,
                    source_system="pytest",
                    external_event_id="T14-FLOW-OUTPUT",
                )
            )
            session.add_all(
                [
                    VatOutputPeriodAssertion(
                        reporting_party_id=entity.party_id,
                        tax_period=date(2026, 2, 1),
                        asserted_output_vat_total=Decimal("13.00"),
                        source="pytest reviewed complete output VAT total",
                        reviewed=True,
                        reviewed_by="pytest",
                        reviewed_at=reviewed_at,
                    ),
                    VatOutputPeriodAssertion(
                        reporting_party_id=entity.party_id,
                        tax_period=date(2026, 3, 1),
                        asserted_output_vat_total=Decimal("0.00"),
                        source="pytest reviewed zero Output VAT month",
                        reviewed=True,
                        reviewed_by="pytest",
                        reviewed_at=reviewed_at,
                    ),
                ]
            )
            session.flush()

            first = build_one(
                session,
                entity_code=entity.canonical_code,
                period="2026-02",
                created_by="pytest",
            )
            first_ledger = session.get(EntityVatLedger, first["ledger_id"])
            assert first_ledger.opening_input_credit == Decimal("20.00")
            assert first_ledger.output_vat == Decimal("13.00")
            assert first_ledger.closing_input_credit == Decimal("7.00")
            assert first_ledger.vat_payable_after_prepayment == Decimal("0.00")

            second = build_one(
                session,
                entity_code=entity.canonical_code,
                period="2026-03",
                created_by="pytest",
            )
            second_ledger = session.get(EntityVatLedger, second["ledger_id"])
            assert second_ledger.opening_input_credit == Decimal("7.00")
            assert second_ledger.output_vat == Decimal("0.00")
            assert second_ledger.closing_input_credit == Decimal("7.00")

            first_state = session.query(TaxPeriodState).filter_by(
                reporting_party_id=entity.party_id,
                tax_type="VAT",
                tax_period=date(2026, 2, 1),
            ).one()
            second_state = session.query(TaxPeriodState).filter_by(
                reporting_party_id=entity.party_id,
                tax_type="VAT",
                tax_period=date(2026, 3, 1),
            ).one()
            assert first_state.current_run_id == first["calculation_run_id"]
            assert second_state.current_run_id == second["calculation_run_id"]
            assert session.get(CalculationRun, second_state.current_run_id).run_status == "SUCCEEDED"
        finally:
            session.close()
            tx.rollback()
