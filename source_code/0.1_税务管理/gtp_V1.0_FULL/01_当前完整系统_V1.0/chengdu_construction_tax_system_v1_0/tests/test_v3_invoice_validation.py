"""Task 09 deterministic Invoice Fact validation contracts."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db import engine
from app.domain.invoice.validation import (
    InvoiceEvidenceSnapshot,
    evaluate_invoice_evidence,
)
from app.v3_fact_models import Fact, FactProvenance, InvoiceFact, InvoiceLine
from app.v3_party_models import Party, PartyIdentifier, SourceDocument
from scripts.v3.invoice_validation import (
    RATE_RULES_KIND,
    _apply,
    _build_wrapper,
    _fetch_snapshots,
    load_rate_rules,
)

ROOT = Path(__file__).resolve().parents[1]


def _complete_snapshot(**overrides) -> InvoiceEvidenceSnapshot:
    base = InvoiceEvidenceSnapshot(
        fact_id=9001,
        current_validation_status="NEEDS_REVIEW",
        business_identity_key="INVOICE|DIGITAL_V1|T09-001",
        invoice_identity_key="DIGITAL_V1|T09-001",
        invoice_identity_version="DIGITAL_V1",
        invoice_number="T09-001",
        invoice_code=None,
        invoice_date=date(2026, 8, 30),
        invoice_status="VALID",
        seller_party_id=1,
        buyer_party_id=2,
        gross_amount=Decimal("113.00"),
        net_amount=Decimal("100.00"),
        vat_amount=Decimal("13.00"),
        currency="CNY",
        line_count=1,
        incomplete_line_count=0,
        line_missing_tax_rate_count=0,
        line_tax_rates=(Decimal("0.13"),),
        line_net_sum=Decimal("100.00"),
        line_vat_sum=Decimal("13.00"),
        provenance_count=1,
        document_provenance_count=1,
        validated_document_provenance_count=1,
        seller_tax_identifier_count=1,
        seller_tax_identifier_value="91510100TASK09",
        reversal_relation_count=0,
        void_relation_count=0,
    )
    return replace(base, **overrides)


def _codes(decision) -> set[str]:
    return {item.code for item in decision.findings}


def _rules() -> dict[str, object]:
    return {
        "kind": RATE_RULES_KIND,
        "version": 1,
        "rule_version": "task09-test-rates-v1",
        "reviewed": True,
        "reviewed_by": "pytest",
        "source": "Task09 deterministic test fixture",
        "allowed_tax_rates": ["0.13"],
    }


def test_complete_supported_digital_invoice_can_be_valid():
    decision = evaluate_invoice_evidence(
        _complete_snapshot(),
        allowed_tax_rates={Decimal("0.13")},
    )
    assert decision.desired_status == "VALID"
    assert decision.findings == ()


def test_missing_rate_rules_is_review_not_silent_promotion():
    decision = evaluate_invoice_evidence(_complete_snapshot())
    assert decision.desired_status == "NEEDS_REVIEW"
    assert "VERSIONED_TAX_RATE_RULES_MISSING" in _codes(decision)


def test_task08_migration_identity_never_becomes_valid():
    snapshot = _complete_snapshot(
        business_identity_key="INVOICE|LEGACY_MIGRATION_V1|ROW|247",
        invoice_identity_key="LEGACY_MIGRATION_V1|ROW|247",
        invoice_identity_version="LEGACY_MIGRATION_V1",
        invoice_date=None,
        invoice_status=None,
        line_count=0,
        line_tax_rates=(),
        line_net_sum=None,
        line_vat_sum=None,
        provenance_count=0,
        document_provenance_count=0,
        validated_document_provenance_count=0,
    )
    decision = evaluate_invoice_evidence(snapshot)
    assert decision.desired_status == "NEEDS_REVIEW"
    assert "LEGACY_MIGRATION_IDENTITY" in _codes(decision)


def test_supported_identity_key_mismatch_is_invalid():
    decision = evaluate_invoice_evidence(
        _complete_snapshot(invoice_identity_key="DIGITAL_V1|OTHER"),
        allowed_tax_rates={Decimal("0.13")},
    )
    assert decision.desired_status == "INVALID"
    assert "INVOICE_IDENTITY_KEY_MISMATCH" in _codes(decision)
    assert "BUSINESS_IDENTITY_KEY_MISMATCH" in _codes(decision)


def test_header_or_line_contradiction_is_invalid_not_review():
    decision = evaluate_invoice_evidence(
        _complete_snapshot(gross_amount=Decimal("112.00")),
        allowed_tax_rates={Decimal("0.13")},
    )
    assert decision.desired_status == "INVALID"
    assert "HEADER_AMOUNT_EQUATION_MISMATCH" in _codes(decision)

    decision = evaluate_invoice_evidence(
        _complete_snapshot(line_vat_sum=Decimal("12.00")),
        allowed_tax_rates={Decimal("0.13")},
    )
    assert decision.desired_status == "INVALID"
    assert "LINE_VAT_SUM_MISMATCH" in _codes(decision)


def test_red_and_void_relationship_invariants_are_deterministic():
    red = evaluate_invoice_evidence(
        _complete_snapshot(
            invoice_status="RED",
            gross_amount=Decimal("-113.00"),
            net_amount=Decimal("-100.00"),
            vat_amount=Decimal("-13.00"),
            line_net_sum=Decimal("-100.00"),
            line_vat_sum=Decimal("-13.00"),
        ),
        allowed_tax_rates={Decimal("0.13")},
    )
    assert red.desired_status == "INVALID"
    assert "RED_REVERSAL_RELATION_MISSING" in _codes(red)

    voided = evaluate_invoice_evidence(
        _complete_snapshot(invoice_status="VOIDED"),
        allowed_tax_rates={Decimal("0.13")},
    )
    assert voided.desired_status == "INVALID"
    assert "VOID_RELATION_MISSING" in _codes(voided)


def test_rate_manifest_requires_explicit_review_metadata(tmp_path):
    valid = tmp_path / "rates.json"
    valid.write_text(json.dumps(_rules()), encoding="utf-8")
    loaded = load_rate_rules(valid)
    assert loaded is not None
    assert loaded["allowed_tax_rates"] == ["0.13"]

    bad = dict(_rules())
    bad["reviewed"] = False
    invalid = tmp_path / "rates-unreviewed.json"
    invalid.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed=true"):
        load_rate_rules(invalid)


def test_applied_revision_78_stays_immutable_and_env_owns_version_length_bootstrap():
    migration = (
        ROOT / "alembic" / "versions" / "78_v3_invoice_fact_relationships.py"
    ).read_text(encoding="utf-8")
    env = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "alembic_version_tax" not in migration
    assert "version_table_col_length=64" in env.replace(" ", "")
    assert "ALTER TABLE IF EXISTS alembic_version_tax" in env


def _create_complete_fact(session: Session, *, suffix: str) -> Fact:
    seller = Party(
        code=f"T09-SELLER-{suffix}",
        name=f"Task09 Seller {suffix}",
        short_name="T09S",
        party_type="external",
        active=True,
    )
    buyer = Party(
        code=f"T09-BUYER-{suffix}",
        name=f"Task09 Buyer {suffix}",
        short_name="T09B",
        party_type="external",
        active=True,
    )
    session.add_all([seller, buyer])
    session.flush()
    session.add(
        PartyIdentifier(
            party_id=seller.id,
            identifier_type="TAX_REGISTRATION_ID",
            identifier_value=f"91510100{suffix}",
            source_system="pytest",
            active=True,
        )
    )
    document = SourceDocument(
        source_system="pytest-task09",
        external_document_id=f"DOC-{suffix}",
        filename=f"invoice-{suffix}.pdf",
        mime_type="application/pdf",
        document_type="INVOICE",
        status="VALIDATED",
    )
    session.add(document)
    session.flush()

    identity = f"DIGITAL_V1|T09-{suffix}"
    fact = Fact(
        fact_type="INVOICE",
        business_identity_key=f"INVOICE|{identity}",
        validation_status="NEEDS_REVIEW",
    )
    session.add(fact)
    session.flush()
    session.add(
        InvoiceFact(
            fact_id=fact.id,
            seller_party_id=seller.id,
            buyer_party_id=buyer.id,
            invoice_identity_key=identity,
            invoice_identity_version="DIGITAL_V1",
            invoice_number=f"T09-{suffix}",
            invoice_date=date(2026, 8, 30),
            invoice_status="VALID",
            document_type="DIGITAL_INVOICE",
            gross_amount=Decimal("113.00"),
            net_amount=Decimal("100.00"),
            vat_amount=Decimal("13.00"),
            currency="CNY",
        )
    )
    session.add(
        InvoiceLine(
            invoice_fact_id=fact.id,
            line_no=1,
            item_name="Task09 material",
            net_amount=Decimal("100.00"),
            vat_amount=Decimal("13.00"),
            tax_rate=Decimal("0.13"),
        )
    )
    session.add(
        FactProvenance(
            fact_id=fact.id,
            document_id=document.id,
            confidence=Decimal("1.00000"),
            extraction_model="pytest",
            extraction_model_version="1",
        )
    )
    session.flush()
    return fact


def test_postgresql_task09_plan_apply_promotes_only_complete_evidence(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            fact = _create_complete_fact(session, suffix="VALID-001")
            snapshots = _fetch_snapshots(conn, ids=[fact.id])
            wrapper = _build_wrapper(conn, snapshots, rate_rules=_rules())
            decision = wrapper["plan"]["items"][0]["decision"]
            assert decision["desired_status"] == "VALID"
            assert decision["findings"] == []

            result = _apply(conn, wrapper)
            assert result["changed_status_count"] == 1
            session.expire_all()
            assert session.get(Fact, fact.id).validation_status == "VALID"
        finally:
            session.close()
            tx.rollback()


def test_postgresql_task09_apply_rejects_stale_evidence(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            fact = _create_complete_fact(session, suffix="STALE-001")
            snapshots = _fetch_snapshots(conn, ids=[fact.id])
            wrapper = _build_wrapper(conn, snapshots, rate_rules=_rules())
            line = session.query(InvoiceLine).filter_by(invoice_fact_id=fact.id).one()
            line.vat_amount = Decimal("12.00")
            session.flush()
            with pytest.raises(RuntimeError, match="stale"):
                _apply(conn, wrapper)
            session.expire_all()
            assert session.get(Fact, fact.id).validation_status == "NEEDS_REVIEW"
        finally:
            session.close()
            tx.rollback()
