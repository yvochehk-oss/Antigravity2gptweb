"""Formal VAT deterministic rebuild and completeness-assertion contracts."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.db import engine
from app.services.formal_vat_rebuild import (
    RULESET_VERSION,
    FormalVatRebuildBlockedError,
    rebuild_formal_vat_statutory_resource,
)
from app.services.formal_vat_statutory import get_formal_vat_statutory_resource
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity, Party
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_tax_models import InputVatClaim
from app.v3_vat_ledger_models import OutputVatEvent, VatOpeningBalanceSeed
from app.v3_vat_review_models import VatInputPeriodAssertion, VatOutputPeriodAssertion


def _seed_rebuild_source(
    session: Session,
    *,
    code: str,
    period: date,
    output_vat: Decimal = Decimal("130.00"),
    opening_credit: Decimal = Decimal("10.00"),
    input_asserted_total: Decimal | None = Decimal("0.00"),
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
    rows = [event, assertion, opening]
    if input_asserted_total is not None:
        rows.append(
            VatInputPeriodAssertion(
                reporting_party_id=party.id,
                tax_period=period,
                asserted_input_vat_total=input_asserted_total,
                source="pytest reviewed Input VAT completeness",
                reviewed=True,
                reviewed_by="pytest",
                reviewed_at=reviewed_at,
            )
        )
    session.add_all(rows)
    session.flush()
    return party, event, assertion


def _seed_confirmed_input_claim(
    session: Session,
    *,
    party: Party,
    code: str,
    period: date,
    amount: Decimal,
) -> InputVatClaim:
    reviewed_at = datetime.now(timezone.utc)
    identity = f"pytest:fvat-input:{code}:{period.isoformat()}"
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
            seller_party_id=None,
            buyer_party_id=party.id,
            invoice_identity_key=identity,
            invoice_identity_version="V1",
            invoice_number=f"FVAT-IN-{code}-{period:%Y%m}",
            invoice_date=period,
            invoice_status="VALID",
            vat_amount=amount,
        )
    )
    session.flush()
    claim = InputVatClaim(
        invoice_fact_id=fact.id,
        reporting_party_id=party.id,
        claim_period=period,
        claim_amount=amount,
        event_type="CLAIM",
        claim_status="CONFIRMED",
        evidence_type="MANUAL_REVIEW",
        confidence="HIGH",
        source_system="pytest-fvat-rebuild",
        external_claim_id=f"{identity}:claim",
        reviewed_by="pytest",
        reviewed_at=reviewed_at,
    )
    session.add(claim)
    session.flush()
    return claim


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
            assert official["vat_ledger"]["input_vat"] == "0.00"
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


def test_closed_vat_restatement_preserves_immutable_close_anchor(seeded_app):
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

            anchor_run_id = int(state.closed_run_id)
            anchor_closed_by = state.closed_by
            anchor_closed_at = state.closed_at
            anchor_state_version = int(state.state_version)

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
            restated_run = session.get(CalculationRun, restated["calculation_run_id"])

            assert restated["status"] == "BUILT"
            assert restated["run_kind"] == "RESTATEMENT"
            assert restated["calculation_run_id"] != first["calculation_run_id"]
            assert restated_run is not None
            assert restated_run.run_kind == "RESTATEMENT"
            assert restated_run.supersedes_run_id == first["calculation_run_id"]

            assert state.state == "CLOSED"
            assert state.current_run_id == restated["calculation_run_id"]
            assert state.closed_run_id == anchor_run_id == first["calculation_run_id"]
            assert state.closed_by == anchor_closed_by
            assert state.closed_at == anchor_closed_at
            assert state.state_version == anchor_state_version + 2

            assert official["closed_anchor_run_id"] == anchor_run_id
            assert official["calculation_run"]["id"] == restated["calculation_run_id"]
            assert official["calculation_run"]["run_kind"] == "RESTATEMENT"
            assert official["calculation_run"]["supersedes_run_id"] == first["calculation_run_id"]
            assert official["vat_ledger"]["output_vat"] == "140.00"
            assert official["vat_ledger"]["vat_payable_after_prepayment"] == "130.00"
        finally:
            session.close()
            tx.rollback()


def test_output_completeness_assertion_is_required(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            _party, _event, assertion = _seed_rebuild_source(
                session,
                code="FVR3",
                period=date(2026, 5, 1),
            )
            session.delete(assertion)
            session.flush()

            with pytest.raises(
                FormalVatRebuildBlockedError,
                match="reviewed Output VAT completeness assertion is required",
            ):
                rebuild_formal_vat_statutory_resource(
                    session,
                    entity_code="FVR3",
                    period="2026-05",
                    created_by="pytest",
                )
        finally:
            session.close()
            tx.rollback()


def test_input_completeness_assertion_is_required_even_for_zero_claims(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            _seed_rebuild_source(
                session,
                code="FVR4",
                period=date(2026, 5, 1),
                input_asserted_total=None,
            )

            with pytest.raises(
                FormalVatRebuildBlockedError,
                match="reviewed Input VAT completeness assertion is required",
            ):
                rebuild_formal_vat_statutory_resource(
                    session,
                    entity_code="FVR4",
                    period="2026-05",
                    created_by="pytest",
                )
        finally:
            session.close()
            tx.rollback()


def test_input_completeness_assertion_must_match_confirmed_claim_total(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            _seed_rebuild_source(
                session,
                code="FVR5",
                period=date(2026, 5, 1),
                input_asserted_total=Decimal("1.00"),
            )

            with pytest.raises(
                FormalVatRebuildBlockedError,
                match="confirmed Input VAT claims do not match the reviewed completeness assertion",
            ):
                rebuild_formal_vat_statutory_resource(
                    session,
                    entity_code="FVR5",
                    period="2026-05",
                    created_by="pytest",
                )
        finally:
            session.close()
            tx.rollback()


def test_matching_input_completeness_assertion_materializes_confirmed_claim(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party, _event, _assertion = _seed_rebuild_source(
                session,
                code="FVR6",
                period=date(2026, 5, 1),
                input_asserted_total=Decimal("50.00"),
            )
            _seed_confirmed_input_claim(
                session,
                party=party,
                code="FVR6",
                period=date(2026, 5, 1),
                amount=Decimal("50.00"),
            )

            built = rebuild_formal_vat_statutory_resource(
                session,
                entity_code="FVR6",
                period="2026-05",
                created_by="pytest",
            )
            official = get_formal_vat_statutory_resource(session, "FVR6", "2026-05")

            assert built["status"] == "BUILT"
            assert official["vat_ledger"]["input_vat"] == "50.00"
            assert official["vat_ledger"]["vat_payable_after_prepayment"] == "70.00"
        finally:
            session.close()
            tx.rollback()
