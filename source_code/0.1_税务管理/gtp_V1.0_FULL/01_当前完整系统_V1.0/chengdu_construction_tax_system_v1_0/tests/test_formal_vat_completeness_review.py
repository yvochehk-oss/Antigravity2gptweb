"""PostgreSQL contracts for explicit Formal VAT completeness review."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.db import engine
from app.services.formal_vat_completeness_review import (
    FormalVatCompletenessReviewError,
    get_formal_vat_completeness_review,
    review_formal_vat_completeness,
)
from app.services.formal_vat_rebuild import rebuild_formal_vat_statutory_resource
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity, Party
from app.v3_tax_models import InputVatClaim
from app.v3_vat_ledger_models import OutputVatEvent, VatOpeningBalanceSeed
from app.v3_vat_review_models import VatInputPeriodAssertion, VatOutputPeriodAssertion


def _seed_scope(
    session: Session,
    *,
    code: str,
    period: date,
    output_status: str = "CONFIRMED",
):
    party = Party(
        code=f"FVAT-COMPLETENESS-{code}",
        name=f"Formal VAT Completeness {code}",
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
    output_identity = f"pytest:fvat-completeness:output:{code}:{period.isoformat()}"
    output_fact = Fact(
        fact_type="INVOICE",
        business_identity_key=output_identity,
        version_no=1,
        is_current=True,
        validation_status="VALID",
    )
    session.add(output_fact)
    session.flush()
    session.add(
        InvoiceFact(
            fact_id=output_fact.id,
            seller_party_id=party.id,
            buyer_party_id=None,
            invoice_identity_key=output_identity,
            invoice_identity_version="V1",
            invoice_number=f"FVAT-CO-{code}-{period:%Y%m}",
            invoice_date=period,
            invoice_status="VALID",
            vat_amount=Decimal("130.00"),
        )
    )
    session.flush()
    session.add(
        OutputVatEvent(
            invoice_fact_id=output_fact.id,
            reporting_party_id=party.id,
            output_vat_period=period,
            vat_amount=Decimal("130.00"),
            event_type="OUTPUT",
            event_status=output_status,
            evidence_type="MANUAL_REVIEW",
            confidence="HIGH",
            source_system="pytest-fvat-completeness",
            external_event_id=f"{output_identity}:output",
            reviewed_by="pytest" if output_status == "CONFIRMED" else None,
            reviewed_at=reviewed_at if output_status == "CONFIRMED" else None,
        )
    )

    input_identity = f"pytest:fvat-completeness:input:{code}:{period.isoformat()}"
    input_fact = Fact(
        fact_type="INVOICE",
        business_identity_key=input_identity,
        version_no=1,
        is_current=True,
        validation_status="VALID",
    )
    session.add(input_fact)
    session.flush()
    session.add(
        InvoiceFact(
            fact_id=input_fact.id,
            seller_party_id=None,
            buyer_party_id=party.id,
            invoice_identity_key=input_identity,
            invoice_identity_version="V1",
            invoice_number=f"FVAT-CI-{code}-{period:%Y%m}",
            invoice_date=period,
            invoice_status="VALID",
            vat_amount=Decimal("40.00"),
        )
    )
    session.flush()
    session.add(
        InputVatClaim(
            invoice_fact_id=input_fact.id,
            reporting_party_id=party.id,
            claim_period=period,
            claim_amount=Decimal("40.00"),
            event_type="CLAIM",
            claim_status="CONFIRMED",
            evidence_type="MANUAL_REVIEW",
            confidence="HIGH",
            source_system="pytest-fvat-completeness",
            external_claim_id=f"{input_identity}:claim",
            reviewed_by="pytest",
            reviewed_at=reviewed_at,
        )
    )
    session.add(
        VatOpeningBalanceSeed(
            reporting_party_id=party.id,
            tax_period=period,
            opening_input_credit=Decimal("10.00"),
            source="pytest reviewed opening credit",
            reviewed=True,
            reviewed_by="pytest",
            reviewed_at=reviewed_at,
        )
    )
    session.flush()
    return party


def test_completeness_preview_is_read_only_and_reports_current_confirmed_totals(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party = _seed_scope(session, code="FVCP1", period=date(2026, 3, 1))

            result = get_formal_vat_completeness_review(session, "fvcp1", "2026-03")

            assert result["can_review"] is True
            assert result["review_required"] is True
            assert result["observed"]["output_vat_total"] == "130.00"
            assert result["observed"]["input_vat_total"] == "40.00"
            assert result["assertions"]["output"]["exists"] is False
            assert result["assertions"]["input"]["exists"] is False
            assert session.query(VatOutputPeriodAssertion).filter_by(
                reporting_party_id=party.id,
                tax_period=date(2026, 3, 1),
            ).count() == 0
            assert session.query(VatInputPeriodAssertion).filter_by(
                reporting_party_id=party.id,
                tax_period=date(2026, 3, 1),
            ).count() == 0
        finally:
            session.close()
            tx.rollback()


def test_explicit_review_writes_both_assertions_and_then_real_rebuild_succeeds(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party = _seed_scope(session, code="FVCP2", period=date(2026, 3, 1))

            reviewed = review_formal_vat_completeness(
                session,
                "FVCP2",
                "2026-03",
                expected_output_vat_total=Decimal("130.00"),
                expected_input_vat_total=Decimal("40.00"),
                reviewed_by="operator-a",
            )

            assert reviewed["review_required"] is False
            output_assertion = session.query(VatOutputPeriodAssertion).filter_by(
                reporting_party_id=party.id,
                tax_period=date(2026, 3, 1),
            ).one()
            input_assertion = session.query(VatInputPeriodAssertion).filter_by(
                reporting_party_id=party.id,
                tax_period=date(2026, 3, 1),
            ).one()
            assert output_assertion.reviewed is True
            assert input_assertion.reviewed is True
            assert output_assertion.reviewed_by == "operator-a"
            assert input_assertion.reviewed_by == "operator-a"
            assert Decimal(output_assertion.asserted_output_vat_total) == Decimal("130.00")
            assert Decimal(input_assertion.asserted_input_vat_total) == Decimal("40.00")

            built = rebuild_formal_vat_statutory_resource(
                session,
                entity_code="FVCP2",
                period="2026-03",
                created_by="operator-a",
            )
            assert built["status"] == "BUILT"
            assert built["resource"]["vat_ledger"]["output_vat"] == "130.00"
            assert built["resource"]["vat_ledger"]["input_vat"] == "40.00"
        finally:
            session.close()
            tx.rollback()


def test_stale_operator_preview_is_rejected_without_creating_assertions(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party = _seed_scope(session, code="FVCP3", period=date(2026, 3, 1))

            with pytest.raises(FormalVatCompletenessReviewError, match="stale VAT completeness review"):
                review_formal_vat_completeness(
                    session,
                    "FVCP3",
                    "2026-03",
                    expected_output_vat_total=Decimal("129.99"),
                    expected_input_vat_total=Decimal("40.00"),
                    reviewed_by="operator-b",
                )

            assert session.query(VatOutputPeriodAssertion).filter_by(
                reporting_party_id=party.id,
                tax_period=date(2026, 3, 1),
            ).count() == 0
            assert session.query(VatInputPeriodAssertion).filter_by(
                reporting_party_id=party.id,
                tax_period=date(2026, 3, 1),
            ).count() == 0
        finally:
            session.close()
            tx.rollback()


def test_unresolved_evidence_cannot_be_promoted_into_reviewed_completeness(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party = _seed_scope(
                session,
                code="FVCP4",
                period=date(2026, 3, 1),
                output_status="NEEDS_REVIEW",
            )
            preview = get_formal_vat_completeness_review(session, "FVCP4", "2026-03")
            assert preview["can_review"] is False
            assert preview["observed"]["output_needs_review_count"] == 1

            with pytest.raises(FormalVatCompletenessReviewError, match="unresolved VAT evidence"):
                review_formal_vat_completeness(
                    session,
                    "FVCP4",
                    "2026-03",
                    expected_output_vat_total=Decimal("0.00"),
                    expected_input_vat_total=Decimal("40.00"),
                    reviewed_by="operator-c",
                )

            assert session.query(VatOutputPeriodAssertion).filter_by(
                reporting_party_id=party.id,
                tax_period=date(2026, 3, 1),
            ).count() == 0
            assert session.query(VatInputPeriodAssertion).filter_by(
                reporting_party_id=party.id,
                tax_period=date(2026, 3, 1),
            ).count() == 0
        finally:
            session.close()
            tx.rollback()
