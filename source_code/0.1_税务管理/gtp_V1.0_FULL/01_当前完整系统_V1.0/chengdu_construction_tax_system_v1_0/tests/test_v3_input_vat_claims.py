"""Task 10 Input VAT Claim schema, projection and PostgreSQL contracts."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import engine
from app.domain.input_vat import InputVatClaimView, confirmed_input_vat_total
from app.models import Invoice, Project
from app.v3_fact_models import Fact, InvoiceFact, LegacyInvoiceMap
from app.v3_party_models import InternalEntity, Party
from app.v3_tax_models import InputVatClaim
from scripts.v3.input_vat_pilot import apply_plan, build_plan

ROOT = Path(__file__).resolve().parents[1]


def test_input_vat_projection_uses_claim_period_not_invoice_period():
    claims = (
        InputVatClaimView(7, date(2026, 8, 1), Decimal("13.00"), "CONFIRMED"),
        InputVatClaimView(7, date(2026, 9, 1), Decimal("9.00"), "CONFIRMED"),
        InputVatClaimView(7, date(2026, 8, 1), Decimal("6.00"), "NEEDS_REVIEW"),
        InputVatClaimView(8, date(2026, 8, 1), Decimal("3.00"), "CONFIRMED"),
    )
    assert confirmed_input_vat_total(
        claims,
        reporting_party_id=7,
        claim_period=date(2026, 8, 31),
    ) == Decimal("13.00")
    assert confirmed_input_vat_total(
        claims,
        reporting_party_id=7,
        claim_period=date(2026, 9, 15),
    ) == Decimal("9.00")


def test_revision_80_is_additive_and_claim_period_is_explicit():
    migration = (
        ROOT / "alembic" / "versions" / "80_v3_input_vat_claims.py"
    ).read_text(encoding="utf-8")
    upper = migration.upper()
    assert 'down_revision = "79_v3_legacy_invoice_pilot_bridge"' in migration
    assert '"input_vat_claims"' in migration
    assert '"claim_period"' in migration
    assert "LEGACY_ASSUMPTION" in migration
    assert "DROP TABLE INVOICES" not in upper
    assert "ALTER TABLE INVOICES" not in upper


def test_input_vat_claim_schema_does_not_mix_invoice_or_project_axes():
    table = InputVatClaim.__table__
    prohibited = {"project_id", "invoice_date", "deductible", "legacy_period"}
    assert prohibited.isdisjoint(table.c.keys())
    assert "claim_period" in table.c
    assert "reporting_party_id" in table.c
    fk_targets = {
        element.target_fullname
        for constraint in table.foreign_key_constraints
        for element in constraint.elements
    }
    assert "invoice_facts.fact_id" in fk_targets
    assert "internal_entities.party_id" in fk_targets
    assert "source_documents.id" in fk_targets


def _create_invoice_context(session: Session, suffix: str):
    buyer = Party(
        code=f"T10-BUYER-{suffix}",
        name=f"Task10 Buyer {suffix}",
        short_name="T10B",
        party_type="internal",
        active=True,
    )
    seller = Party(
        code=f"T10-SELLER-{suffix}",
        name=f"Task10 Seller {suffix}",
        short_name="T10S",
        party_type="external",
        active=True,
    )
    session.add_all([buyer, seller])
    session.flush()
    session.add(
        InternalEntity(
            party_id=buyer.id,
            canonical_code=f"T10{suffix[-4:]}",
            business_role="A",
            legal_entity=True,
            active=True,
        )
    )
    fact = Fact(
        fact_type="INVOICE",
        business_identity_key=f"INVOICE|DIGITAL_V1|T10-{suffix}",
        validation_status="NEEDS_REVIEW",
    )
    session.add(fact)
    session.flush()
    session.add(
        InvoiceFact(
            fact_id=fact.id,
            seller_party_id=seller.id,
            buyer_party_id=buyer.id,
            invoice_identity_key=f"DIGITAL_V1|T10-{suffix}",
            invoice_identity_version="DIGITAL_V1",
            invoice_number=f"T10-{suffix}",
            invoice_date=date(2026, 7, 15),
            invoice_status="VALID",
            gross_amount=Decimal("113.00"),
            net_amount=Decimal("100.00"),
            vat_amount=Decimal("13.00"),
            currency="CNY",
        )
    )
    session.flush()
    return buyer, seller, fact


def test_postgresql_legacy_assumption_is_forced_low_review(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            buyer, _, fact = _create_invoice_context(session, "CHECK-001")
            session.add(
                InputVatClaim(
                    invoice_fact_id=fact.id,
                    reporting_party_id=buyer.id,
                    claim_period=date(2026, 8, 1),
                    claim_amount=Decimal("13.00"),
                    event_type="CLAIM",
                    claim_status="NEEDS_REVIEW",
                    evidence_type="LEGACY_ASSUMPTION",
                    confidence="LOW",
                    source_system="pytest",
                    external_claim_id="T10-CHECK-OK",
                )
            )
            session.flush()

            nested = session.begin_nested()
            session.add(
                InputVatClaim(
                    invoice_fact_id=fact.id,
                    reporting_party_id=buyer.id,
                    claim_period=date(2026, 8, 1),
                    claim_amount=Decimal("13.00"),
                    event_type="CLAIM",
                    claim_status="CONFIRMED",
                    evidence_type="LEGACY_ASSUMPTION",
                    confidence="LOW",
                    reviewed_by="pytest",
                    reviewed_at=datetime.now(timezone.utc),
                    source_system="pytest",
                    external_claim_id="T10-CHECK-BAD",
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()
            nested.rollback()
        finally:
            session.close()
            tx.rollback()


def test_postgresql_confirmed_claim_requires_review_and_month_start(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            buyer, _, fact = _create_invoice_context(session, "CHECK-002")

            nested = session.begin_nested()
            session.add(
                InputVatClaim(
                    invoice_fact_id=fact.id,
                    reporting_party_id=buyer.id,
                    claim_period=date(2026, 8, 15),
                    claim_amount=Decimal("13.00"),
                    event_type="CLAIM",
                    claim_status="CONFIRMED",
                    evidence_type="MANUAL_REVIEW",
                    confidence="HIGH",
                    reviewed_by="pytest",
                    reviewed_at=datetime.now(timezone.utc),
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()
            nested.rollback()

            nested = session.begin_nested()
            session.add(
                InputVatClaim(
                    invoice_fact_id=fact.id,
                    reporting_party_id=buyer.id,
                    claim_period=date(2026, 8, 1),
                    claim_amount=Decimal("13.00"),
                    event_type="CLAIM",
                    claim_status="CONFIRMED",
                    evidence_type="MANUAL_REVIEW",
                    confidence="HIGH",
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()
            nested.rollback()
        finally:
            session.close()
            tx.rollback()


def test_postgresql_task10_pilot_creates_only_review_claim_and_reconciles(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            suffix = "PILOT-001"
            buyer, _, fact = _create_invoice_context(session, suffix)
            internal = session.get(InternalEntity, buyer.id)
            project = Project(
                code=f"T10-PROJECT-{suffix}",
                name="Task10 Pilot Project",
                city="Chengdu",
                contract_total=Decimal("1000.00"),
                tax_method="general",
                entity_code=internal.canonical_code,
            )
            session.add(project)
            session.flush()
            legacy = Invoice(
                project_id=project.id,
                invoice_no=f"LEGACY-{suffix}",
                period="2026-08",
                entity_code=internal.canonical_code,
                direction="in",
                counterparty_code="EXT-T10",
                category="material",
                net=Decimal("100.00"),
                vat=Decimal("13.00"),
                rate=Decimal("0.1300"),
                deductible=True,
                note="Task10 pilot fixture",
            )
            session.add(legacy)
            session.flush()
            session.add(
                LegacyInvoiceMap(
                    legacy_invoice_id=legacy.id,
                    invoice_fact_id=fact.id,
                    legacy_direction="in",
                    migration_status="MIGRATED_SINGLE_PERSPECTIVE",
                    reason="pytest",
                )
            )
            session.flush()

            wrapper = build_plan(conn, internal.canonical_code, "2026-08")
            assert wrapper["plan"]["blockers"] == []
            assert wrapper["plan"]["legacy_candidate_count"] == 1
            candidate = wrapper["plan"]["candidates"][0]
            assert candidate["claim_status"] == "NEEDS_REVIEW"
            assert candidate["evidence_type"] == "LEGACY_ASSUMPTION"
            assert candidate["confidence"] == "LOW"
            assert candidate["claim_period"] == "2026-08-01"

            result = apply_plan(conn, wrapper)
            assert result["created_claim_count"] == 1
            assert result["legacy_candidate_vat_total"] == "13.00"
            assert result["review_candidate_vat_total"] == "13.00"
            assert result["confirmed_linked_vat_total"] == "0.00"
            assert result["explained_residual"] == "0.00"
            assert result["reporting_period_confirmed_input_vat"] == "0.00"

            claim = session.get(InputVatClaim, result["claim_ids"][0])
            assert claim.claim_status == "NEEDS_REVIEW"
            assert claim.evidence_type == "LEGACY_ASSUMPTION"
            assert claim.claim_period == date(2026, 8, 1)
        finally:
            session.close()
            tx.rollback()
