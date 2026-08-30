"""Task14 VAT review packet contracts."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import engine
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity, Party
from app.v3_tax_models import InputVatClaim
from app.v3_vat_ledger_models import OutputVatEvent, VatOpeningBalanceSeed
from scripts.v3.vat_review_packet import build_packet


def test_review_packet_exposes_unresolved_claim_without_mutation(seeded_app):
    suffix = uuid4().hex[:8]
    period = date(2026, 8, 1)
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            buyer = Party(
                code=f"T14R-B-{suffix}",
                name=f"Task14 Review Buyer {suffix}",
                short_name="T14RB",
                party_type="internal",
                active=True,
            )
            seller = Party(
                code=f"T14R-S-{suffix}",
                name=f"Task14 Review Seller {suffix}",
                short_name="T14RS",
                party_type="external",
                active=True,
            )
            session.add_all([buyer, seller])
            session.flush()
            entity_code = f"R{suffix[:7]}".upper()
            session.add(
                InternalEntity(
                    party_id=buyer.id,
                    canonical_code=entity_code,
                    business_role="A",
                    legal_entity=True,
                    active=True,
                )
            )
            fact = Fact(
                fact_type="INVOICE",
                business_identity_key=f"INVOICE|LEGACY_MIGRATION_V1|REVIEW-{suffix}",
                validation_status="NEEDS_REVIEW",
            )
            session.add(fact)
            session.flush()
            session.add(
                InvoiceFact(
                    fact_id=fact.id,
                    seller_party_id=seller.id,
                    buyer_party_id=buyer.id,
                    invoice_identity_key=f"LEGACY_MIGRATION_V1|REVIEW-{suffix}",
                    invoice_identity_version="LEGACY_MIGRATION_V1",
                    invoice_number=f"REVIEW-{suffix}",
                    invoice_date=None,
                    invoice_status=None,
                    gross_amount=Decimal("113.00"),
                    net_amount=Decimal("100.00"),
                    vat_amount=Decimal("13.00"),
                    currency="CNY",
                )
            )
            session.flush()
            claim = InputVatClaim(
                invoice_fact_id=fact.id,
                reporting_party_id=buyer.id,
                claim_period=period,
                claim_amount=Decimal("13.00"),
                event_type="CLAIM",
                claim_status="NEEDS_REVIEW",
                evidence_type="LEGACY_ASSUMPTION",
                confidence="LOW",
                source_system="pytest-review-packet",
                external_claim_id=f"CLAIM-{suffix}",
            )
            session.add(claim)
            session.flush()

            before_claims = session.scalar(select(func.count()).select_from(InputVatClaim))
            before_outputs = session.scalar(select(func.count()).select_from(OutputVatEvent))
            before_seeds = session.scalar(select(func.count()).select_from(VatOpeningBalanceSeed))

            packet = build_packet(session, entity_code=entity_code, period=period)

            assert packet["kind"] == "V3_TASK14_VAT_REVIEW_PACKET"
            assert packet["scope"]["reporting_party_id"] == buyer.id
            assert packet["review_summary"]["unresolved_input_claim_ids"] == [claim.id]
            assert packet["review_summary"]["unresolved_output_event_ids"] == []
            assert packet["review_summary"]["opening_source_available"] is False
            assert packet["review_summary"]["ledger_build_blocked"] is True
            assert packet["input_vat_claims"][0]["claim"]["evidence_type"] == "LEGACY_ASSUMPTION"
            assert packet["input_vat_claims"][0]["invoice_fact_context"]["fact"]["validation_status"] == "NEEDS_REVIEW"

            assert session.scalar(select(func.count()).select_from(InputVatClaim)) == before_claims
            assert session.scalar(select(func.count()).select_from(OutputVatEvent)) == before_outputs
            assert session.scalar(select(func.count()).select_from(VatOpeningBalanceSeed)) == before_seeds
        finally:
            session.close()
            tx.rollback()
