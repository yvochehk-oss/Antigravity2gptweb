"""Task14 reviewed simulation-pilot bootstrap contracts."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import engine
from app.v3_fact_models import Fact, FactRelationship, InvoiceFact
from app.v3_party_models import InternalEntity, Party, SourceDocument
from app.v3_tax_models import InputVatClaim
from app.v3_vat_ledger_models import VatOpeningBalanceSeed
from app.v3_vat_review_models import VatOutputPeriodAssertion
from scripts.v3.task14_simulation_pilot import apply_plan, make_plan


def _seed_legacy(session: Session):
    suffix = uuid4().hex[:8]
    buyer = Party(
        code=f"T14SIM-B-{suffix}",
        name=f"Task14 Simulation Buyer {suffix}",
        short_name="T14SIMB",
        party_type="internal",
        active=True,
    )
    seller = Party(
        code=f"T14SIM-S-{suffix}",
        name=f"Task14 Simulation Seller {suffix}",
        short_name="T14SIMS",
        party_type="external",
        active=True,
    )
    session.add_all([buyer, seller])
    session.flush()
    entity_code = f"S{suffix[:7]}".upper()
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
        business_identity_key=f"INVOICE|LEGACY_MIGRATION_V1|SIM-{suffix}",
        validation_status="NEEDS_REVIEW",
        is_current=True,
    )
    session.add(fact)
    session.flush()
    invoice_number = f"SIM{suffix}"
    session.add(
        InvoiceFact(
            fact_id=fact.id,
            seller_party_id=seller.id,
            buyer_party_id=buyer.id,
            invoice_identity_key=f"LEGACY_MIGRATION_V1|SIM-{suffix}",
            invoice_identity_version="LEGACY_MIGRATION_V1",
            invoice_number=invoice_number,
            net_amount=Decimal("100.00"),
            vat_amount=Decimal("6.00"),
            gross_amount=Decimal("106.00"),
            currency="CNY",
        )
    )
    session.flush()
    claim = InputVatClaim(
        invoice_fact_id=fact.id,
        reporting_party_id=buyer.id,
        claim_period=date(2026, 9, 1),
        claim_amount=Decimal("6.00"),
        event_type="CLAIM",
        claim_status="NEEDS_REVIEW",
        evidence_type="LEGACY_ASSUMPTION",
        confidence="LOW",
        source_system="pytest-task14-simulation",
        external_claim_id=f"LEGACY-{suffix}",
    )
    session.add(claim)
    session.flush()
    return entity_code, buyer, seller, fact, claim, invoice_number, suffix


def _manifest(*, entity_code, seller_id, fact_id, claim_id, invoice_number, sha256, suffix):
    return {
        "kind": "V3_TASK14_SIMULATION_PILOT_MANIFEST",
        "version": 1,
        "simulation_fixture": True,
        "fixture_id": f"PYTEST-T14SIM-{suffix}",
        "reviewed": True,
        "reviewed_by": "pytest",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "entity_code": entity_code,
        "tax_period": "2026-09",
        "legacy_invoice_fact_id": fact_id,
        "legacy_input_claim_id": claim_id,
        "source_document": {
            "source_system": "SIMULATION_PROJECT_ARCHIVE",
            "external_document_id": f"PYTEST:{suffix}:INVOICE",
            "filename": f"invoice-{suffix}.pdf",
            "mime_type": "application/pdf",
            "file_sha256": sha256,
            "source_uri": f"pytest/{suffix}/invoice.pdf",
        },
        "invoice": {
            "seller_party_id": seller_id,
            "seller_tax_registration_id": f"SIMTAX{suffix.upper()}",
            "invoice_number": invoice_number,
            "invoice_date": "2026-09-22",
            "net_amount": "100.00",
            "vat_amount": "6.00",
            "gross_amount": "106.00",
            "currency": "CNY",
            "line": {
                "item_name": "simulation reviewed service",
                "net_amount": "100.00",
                "vat_amount": "6.00",
                "tax_rate": "0.06",
            },
        },
        "input_claim": {"claim_period": "2026-09", "claim_amount": "6.00"},
        "opening_input_credit": "0.00",
        "asserted_output_vat_total": "0.00",
    }


def test_simulation_plan_binds_exact_source_file_sha(seeded_app, tmp_path):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            entity_code, _, seller, fact, claim, number, suffix = _seed_legacy(session)
            path = tmp_path / f"invoice-{suffix}.pdf"
            path.write_bytes(b"reviewed synthetic invoice bytes")
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = _manifest(
                entity_code=entity_code,
                seller_id=seller.id,
                fact_id=fact.id,
                claim_id=claim.id,
                invoice_number=number,
                sha256="0" * 64,
                suffix=suffix,
            )
            with pytest.raises(ValueError, match="SHA256 mismatch"):
                make_plan(session, manifest, database="projectrag_test", source_file=path)
            manifest["source_document"]["file_sha256"] = sha
            plan = make_plan(session, manifest, database="projectrag_test", source_file=path)
            assert plan["source_file"]["sha256"] == sha
            assert plan["simulation_fixture"] is True
        finally:
            session.close()
            tx.rollback()


def test_simulation_apply_creates_new_valid_fact_and_supersedes_legacy(seeded_app, tmp_path):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            entity_code, buyer, seller, fact, claim, number, suffix = _seed_legacy(session)
            path = tmp_path / f"invoice-{suffix}.pdf"
            path.write_bytes(b"reviewed synthetic invoice bytes")
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = _manifest(
                entity_code=entity_code,
                seller_id=seller.id,
                fact_id=fact.id,
                claim_id=claim.id,
                invoice_number=number,
                sha256=sha,
                suffix=suffix,
            )
            plan = make_plan(session, manifest, database="projectrag_test", source_file=path)
            result = apply_plan(session, plan)
            session.flush()

            new_fact = session.get(Fact, result["validated_invoice_fact_id"])
            old_fact = session.get(Fact, fact.id)
            new_claim = session.get(InputVatClaim, result["confirmed_input_vat_claim_id"])
            old_claim = session.get(InputVatClaim, claim.id)
            assert new_fact.validation_status == "VALID"
            assert new_fact.is_current is True
            assert old_fact.validation_status == "SUPERSEDED"
            assert old_fact.is_current is False
            assert new_claim.invoice_fact_id == new_fact.id
            assert new_claim.reporting_party_id == buyer.id
            assert new_claim.claim_status == "CONFIRMED"
            assert new_claim.evidence_type == "DOCUMENT_EVIDENCE"
            assert new_claim.claim_amount == Decimal("6.00")
            assert old_claim.claim_status == "SUPERSEDED"

            relation = session.scalar(
                select(FactRelationship).where(
                    FactRelationship.source_fact_id == new_fact.id,
                    FactRelationship.target_fact_id == old_fact.id,
                    FactRelationship.relationship_type == "REPLACES",
                )
            )
            assert relation is not None
            document = session.get(SourceDocument, result["source_document_id"])
            assert document.status == "VALIDATED"
            assert document.file_sha256 == sha
            seed = session.get(VatOpeningBalanceSeed, result["opening_balance_seed_id"])
            assertion = session.get(VatOutputPeriodAssertion, result["output_period_assertion_id"])
            assert seed.opening_input_credit == Decimal("0.00")
            assert seed.reviewed is True
            assert assertion.asserted_output_vat_total == Decimal("0.00")
            assert assertion.reviewed is True
        finally:
            session.close()
            tx.rollback()
