"""Task14c reviewed Input VAT claim resolution contracts."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import engine
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity, Party
from app.v3_tax_models import InputVatClaim
from scripts.v3.vat_input_claim_resolution import apply_plan, make_plan

ROOT = Path(__file__).resolve().parents[1]


def _legacy_claim(session: Session) -> InputVatClaim:
    suffix = uuid4().hex[:8]
    buyer = Party(code=f"T14C-B-{suffix}", name=f"Task14c Buyer {suffix}", short_name="T14CB", party_type="internal", active=True)
    seller = Party(code=f"T14C-S-{suffix}", name=f"Task14c Seller {suffix}", short_name="T14CS", party_type="external", active=True)
    session.add_all([buyer, seller])
    session.flush()
    session.add(InternalEntity(party_id=buyer.id, canonical_code=f"C{suffix[:7]}".upper(), business_role="A", legal_entity=True, active=True))
    fact = Fact(fact_type="INVOICE", business_identity_key=f"INVOICE|LEGACY_MIGRATION_V1|T14C-{suffix}", validation_status="NEEDS_REVIEW")
    session.add(fact)
    session.flush()
    session.add(InvoiceFact(
        fact_id=fact.id,
        seller_party_id=seller.id,
        buyer_party_id=buyer.id,
        invoice_identity_key=f"LEGACY_MIGRATION_V1|T14C-{suffix}",
        invoice_identity_version="LEGACY_MIGRATION_V1",
        invoice_number=f"T14C-{suffix}",
        gross_amount=Decimal("113.00"),
        net_amount=Decimal("100.00"),
        vat_amount=Decimal("13.00"),
        currency="CNY",
    ))
    session.flush()
    claim = InputVatClaim(
        invoice_fact_id=fact.id,
        reporting_party_id=buyer.id,
        claim_period=date(2026, 8, 1),
        claim_amount=Decimal("13.00"),
        event_type="CLAIM",
        claim_status="NEEDS_REVIEW",
        evidence_type="LEGACY_ASSUMPTION",
        confidence="LOW",
        source_system="pytest-task14c",
        external_claim_id=f"LEGACY-{suffix}",
    )
    session.add(claim)
    session.flush()
    return claim


def test_revision_86_is_additive_and_keeps_legacy_confirmation_blocked():
    migration = (ROOT / "alembic" / "versions" / "86_v3_input_vat_claim_review_resolution.py").read_text(encoding="utf-8")
    assert 'down_revision = "85_v3_vat_output_period_assertions"' in migration
    assert "claim_status IN ('NEEDS_REVIEW','REJECTED','SUPERSEDED')" in migration
    assert "ck_input_vat_claims_resolution_reviewed" in migration
    assert "claim_status IN ('NEEDS_REVIEW','REJECTED','SUPERSEDED','CONFIRMED')" not in migration


def test_postgresql_reviewed_legacy_assumption_can_be_rejected(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            claim = _legacy_claim(session)
            claim.claim_status = "REJECTED"
            claim.reviewed_by = "pytest"
            claim.reviewed_at = datetime.now(timezone.utc)
            session.flush()
            assert claim.claim_status == "REJECTED"
        finally:
            session.close()
            tx.rollback()


def test_postgresql_legacy_assumption_still_cannot_be_confirmed(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            claim = _legacy_claim(session)
            nested = session.begin_nested()
            claim.claim_status = "CONFIRMED"
            claim.reviewed_by = "pytest"
            claim.reviewed_at = datetime.now(timezone.utc)
            with pytest.raises(IntegrityError):
                session.flush()
            nested.rollback()
        finally:
            session.close()
            tx.rollback()


def test_postgresql_resolution_requires_reviewer_and_timestamp(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            claim = _legacy_claim(session)
            nested = session.begin_nested()
            claim.claim_status = "REJECTED"
            with pytest.raises(IntegrityError):
                session.flush()
            nested.rollback()
        finally:
            session.close()
            tx.rollback()


def test_review_workflow_reject_plan_and_apply_is_atomic(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            claim = _legacy_claim(session)
            manifest = {
                "kind": "V3_TASK14C_INPUT_VAT_CLAIM_REVIEW",
                "version": 1,
                "reviewed": True,
                "reviewed_by": "pytest",
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "legacy_claim_id": claim.id,
                "action": "REJECT",
                "reason": "reviewed source evidence says claim is not deductible",
                "replacement": None,
            }
            plan = make_plan(session, manifest, "projectrag_test")
            result = apply_plan(session, plan)
            assert result["legacy_claim_status"] == "REJECTED"
            assert result["replacement_claim_id"] is None
            assert session.get(InputVatClaim, claim.id).reviewed_by == "pytest"
        finally:
            session.close()
            tx.rollback()
