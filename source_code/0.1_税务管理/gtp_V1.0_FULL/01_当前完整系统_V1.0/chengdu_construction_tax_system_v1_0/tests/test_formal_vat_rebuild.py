"""FVAT-2 deterministic rebuild convergence contracts."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db import engine
from app.services.formal_vat_rebuild import (
    RULESET_VERSION,
    rebuild_formal_vat_statutory_resource,
)
from app.services.formal_vat_statutory import get_formal_vat_statutory_resource
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity, Party
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_vat_ledger_models import OutputVatEvent, VatOpeningBalanceSeed
from app.v3_vat_review_models import VatOutputPeriodAssertion


def _seed_rebuild_source(
    session: Session,
    *,
    code: str,
    period: date,
    output_vat: Decimal = Decimal("130.00"),
    opening_credit: Decimal = Decimal("10.00"),
):
    party = Party(
        code=f"FVAT-REBUILD-{code}",
        name=f"Formal VAT Rebuild {code}",
        short_name=code,
        party_type="internal",
        active=True,
    )
    session.add(party)
    session.flush()
    session.add(
        InternalEntity(
            party_id=party.id,
            canonical_code=code,
            business_role="A",
            legal_entity=True,
            active=True,
        )
    )
    session.flush()

    reviewed_at = datetime.now(timezone.utc)
    identity = f"pytest:fvat-rebuild:{code}:{period.isoformat()}"
    fact = Fact(
        fact_type="INVOICE",
        business_identity_key=identity,
        version_no=1,
        is_current=True,
        validation_status="VALID",
    )
    session.add(fact)
    session.flush()
    session.add(
        InvoiceFact(
            fact_id=fact.id,
            seller_party_id=party.id,
            buyer_party_id=None,
            invoice_identity_key=identity,
            invoice_identity_version="V1",
            invoice_number=f"FVAT-{code}-{period:%Y%m}",
            invoice_date=period,
            invoice_status="VALID",
            vat_amount=output_vat,
        )
    )
    session.flush()

    event = OutputVatEvent(
        invoice_fact_id=fact.id,
        reporting_party_id=party.id,
        output_vat_period=period,
        vat_amount=output_vat,
        event_type="OUTPUT",
        event_status="CONFIRMED",
        evidence_type="MANUAL_REVIEW",
        confidence="HIGH",
        source_system="pytest-fvat-rebuild",
        external_event_id=f"{identity}:output",
        reviewed_by="pytest",
        reviewed_at=reviewed_at,
    )
    assertion = VatOutputPeriodAssertion(
        reporting_party_id=party.id,
        tax_period=period,
        asserted_output_vat_total=output_vat,
        source="pytest reviewed Output VAT completeness",
        reviewed=True,
        reviewed_by="pytest",
        reviewed_at=reviewed_at,
    )
    opening = VatOpeningBalanceSeed(
        reporting_party_id=party.id,
        tax_period=period,
        opening_input_credit=opening_credit,
        source="pytest reviewed opening credit",
        reviewed=True,
        reviewed_by="pytest",
        reviewed_at=reviewed_at,
    )
    session.add_all([event, assertion, opening])
    session.flush()
    return party, event, assertion


def test_formal_vat_rebuild_converges_and_second_run_is_no_change(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party, _event, _assertion = _seed_rebuild_source(
                session,
                code="FVR1",
                period=date(2026, 4, 1),
            )

            built = rebuild_formal_vat_statutory_resource(
                session,
                entity_code="fvr1",
                period="2026-04",
                created_by="pytest",
            )
            official = get_formal_vat_statutory_resource(session, "FVR1", "2026-04")

            assert built["status"] == "BUILT"
            assert built["resource_type"] == "FORMAL_VAT_STATUTORY_V1"
            assert built["calculation_run_id"] == official["calculation_run"]["id"]
            assert built["ledger_id"] == official["vat_ledger"]["id"]
            assert built["input_snapshot_sha256"] == official["calculation_run"]["input_snapshot_sha256"]
            assert built["result_sha256"] == official["calculation_run"]["result_sha256"]
            assert official["calculation_run"]["ruleset_version"] == RULESET_VERSION
            assert official["vat_ledger"]["output_vat"] == "130.00"
            assert official["vat_ledger"]["opening_input_credit"] == "10.00"
            assert official["vat_ledger"]["vat_payable_after_prepayment"] == "120.00"

            unchanged = rebuild_formal_vat_statutory_resource(
                session,
                entity_code="FVR1",
                period="2026-04",
                created_by="pytest",
            )
            assert unchanged["status"] == "NO_CHANGE"
            assert unchanged["calculation_run_id"] == built["calculation_run_id"]
            assert unchanged["ledger_id"] == built["ledger_id"]
            assert unchanged["input_snapshot_sha256"] == built["input_snapshot_sha256"]
            assert unchanged["result_sha256"] == built["result_sha256"]

            runs = session.query(CalculationRun).filter_by(
                reporting_party_id=party.id,
                tax_type="VAT",
                tax_period=date(2026, 4, 1),
            ).all()
            assert len(runs) == 1
        finally:
            session.close()
            tx.rollback()


def test_closed_vat_restatement_repins_current_and_closed_run(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party, event, assertion = _seed_rebuild_source(
                session,
                code="FVR2",
                period=date(2026, 4, 1),
            )
            first = rebuild_formal_vat_statutory_resource(
                session,
                entity_code="FVR2",
                period="2026-04",
                created_by="pytest",
            )

            state = session.query(TaxPeriodState).filter_by(
                reporting_party_id=party.id,
                tax_type="VAT",
                tax_period=date(2026, 4, 1),
            ).one()
            closed_at = datetime.now(timezone.utc)
            state.state = "CLOSED"
            state.closed_run_id = state.current_run_id
            state.closed_by = "pytest-close"
            state.closed_at = closed_at
            session.flush()

            event.vat_amount = Decimal("140.00")
            assertion.asserted_output_vat_total = Decimal("140.00")
            session.flush()

            restated = rebuild_formal_vat_statutory_resource(
                session,
                entity_code="FVR2",
                period="2026-04",
                created_by="pytest-restatement",
                allow_restatement=True,
            )
            session.refresh(state)
            official = get_formal_vat_statutory_resource(session, "FVR2", "2026-04")

            assert restated["status"] == "BUILT"
            assert restated["run_kind"] == "RESTATEMENT"
            assert restated["calculation_run_id"] != first["calculation_run_id"]
            assert state.state == "CLOSED"
            assert state.current_run_id == restated["calculation_run_id"]
            assert state.closed_run_id == restated["calculation_run_id"]
            assert official["calculation_run"]["id"] == restated["calculation_run_id"]
            assert official["vat_ledger"]["output_vat"] == "140.00"
            assert official["vat_ledger"]["vat_payable_after_prepayment"] == "130.00"
        finally:
            session.close()
            tx.rollback()
