#!/usr/bin/env python3
"""Reviewed simulation-fixture bootstrap for the Task14 VAT Ledger pilot.

This command exists only for the project's synthetic/demo business dataset.  It
never weakens production validation rules and never promotes a legacy migration
Fact in place.  Instead it creates a deterministic document-backed DIGITAL_V1
Invoice Fact, supersedes the legacy migration artifact/claim, and records the
reviewed simulation opening/output completeness facts required by Task14.

PLAN is read-only and binds to the exact source-file SHA256.  APPLY requires the
exact saved plan, exact database name, and exact simulation fixture id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.domain.invoice.identity import (  # noqa: E402
    build_invoice_business_identity_key,
    build_invoice_identity_key,
)
from app.domain.invoice.validation import (  # noqa: E402
    InvoiceEvidenceSnapshot,
    evaluate_invoice_evidence,
)
from app.v3_fact_models import (  # noqa: E402
    Fact,
    FactProvenance,
    FactRelationship,
    InvoiceFact,
    InvoiceLine,
)
from app.v3_party_models import (  # noqa: E402
    InternalEntity,
    Party,
    PartyIdentifier,
    SourceDocument,
)
from app.v3_tax_models import InputVatClaim  # noqa: E402
from app.v3_vat_ledger_models import VatOpeningBalanceSeed  # noqa: E402
from app.v3_vat_review_models import VatOutputPeriodAssertion  # noqa: E402

EXPECTED_HEAD = "86_v3_input_vat_claim_review_resolution"
MANIFEST_KIND = "V3_TASK14_SIMULATION_PILOT_MANIFEST"
PLAN_KIND = "V3_TASK14_SIMULATION_PILOT_PLAN"
RESULT_KIND = "V3_TASK14_SIMULATION_PILOT_RESULT"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task14 simulation pilot is PostgreSQL-only")
    return value


def _canonical_hash(payload: Any) -> str:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _month(value: str) -> date:
    parsed = date.fromisoformat(f"{value}-01" if len(value) == 7 else value)
    if parsed.day != 1:
        raise ValueError("tax/claim period must be month-start")
    return parsed


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _load_manifest(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("kind") != MANIFEST_KIND or payload.get("version") != 1:
        raise ValueError("unsupported Task14 simulation manifest")
    if payload.get("simulation_fixture") is not True:
        raise ValueError("simulation_fixture=true is mandatory")
    if payload.get("reviewed") is not True:
        raise ValueError("reviewed=true is mandatory")
    for field in ("fixture_id", "reviewed_by", "reviewed_at", "entity_code"):
        if not str(payload.get(field) or "").strip():
            raise ValueError(f"{field} is required")
    datetime.fromisoformat(str(payload["reviewed_at"]).replace("Z", "+00:00"))
    if _money(payload["opening_input_credit"]) < 0:
        raise ValueError("opening_input_credit cannot be negative")
    if _money(payload["asserted_output_vat_total"]) != Decimal("0.00"):
        raise ValueError(
            "this zero-output simulation fixture must explicitly assert 0.00 Output VAT"
        )
    return payload


def _head(session: Session) -> str:
    return str(
        session.connection()
        .exec_driver_sql("SELECT version_num FROM alembic_version_tax")
        .scalar_one()
    )


def _fact_snapshot(fact: Fact, invoice: InvoiceFact) -> dict[str, Any]:
    return {
        "fact_id": fact.id,
        "fact_type": fact.fact_type,
        "business_identity_key": fact.business_identity_key,
        "version_no": fact.version_no,
        "is_current": fact.is_current,
        "validation_status": fact.validation_status,
        "invoice_identity_key": invoice.invoice_identity_key,
        "invoice_identity_version": invoice.invoice_identity_version,
        "invoice_number": invoice.invoice_number,
        "seller_party_id": invoice.seller_party_id,
        "buyer_party_id": invoice.buyer_party_id,
        "invoice_date": str(invoice.invoice_date) if invoice.invoice_date else None,
        "invoice_status": invoice.invoice_status,
        "net_amount": str(invoice.net_amount) if invoice.net_amount is not None else None,
        "vat_amount": str(invoice.vat_amount) if invoice.vat_amount is not None else None,
        "gross_amount": str(invoice.gross_amount) if invoice.gross_amount is not None else None,
    }


def _claim_snapshot(claim: InputVatClaim) -> dict[str, Any]:
    return {
        "id": claim.id,
        "invoice_fact_id": claim.invoice_fact_id,
        "reporting_party_id": claim.reporting_party_id,
        "claim_period": str(claim.claim_period),
        "claim_amount": str(claim.claim_amount),
        "event_type": claim.event_type,
        "claim_status": claim.claim_status,
        "evidence_type": claim.evidence_type,
        "confidence": claim.confidence,
        "source_document_id": claim.source_document_id,
        "source_system": claim.source_system,
        "external_claim_id": claim.external_claim_id,
        "reviewed_by": claim.reviewed_by,
        "reviewed_at": str(claim.reviewed_at) if claim.reviewed_at else None,
    }


def _validate_manifest_against_db(
    session: Session,
    manifest: dict[str, Any],
    source_file_sha256: str,
) -> dict[str, Any]:
    if _head(session) != EXPECTED_HEAD:
        raise ValueError(f"formal DB head must be {EXPECTED_HEAD}")

    expected_sha = str(manifest["source_document"]["file_sha256"]).lower()
    if source_file_sha256.lower() != expected_sha:
        raise ValueError(
            f"source invoice SHA256 mismatch: expected {expected_sha}, got {source_file_sha256}"
        )

    entity = session.scalar(
        select(InternalEntity).where(
            InternalEntity.canonical_code == str(manifest["entity_code"])
        )
    )
    if entity is None or not entity.active or not entity.legal_entity:
        raise ValueError("simulation entity must be an active legal internal entity")

    legacy_fact_id = int(manifest["legacy_invoice_fact_id"])
    legacy_row = session.execute(
        select(Fact, InvoiceFact)
        .join(InvoiceFact, InvoiceFact.fact_id == Fact.id)
        .where(Fact.id == legacy_fact_id)
    ).one_or_none()
    if legacy_row is None:
        raise ValueError("legacy invoice Fact not found")
    legacy_fact, legacy_invoice = legacy_row
    if (
        legacy_fact.fact_type != "INVOICE"
        or legacy_fact.validation_status != "NEEDS_REVIEW"
        or not legacy_fact.is_current
        or legacy_invoice.invoice_identity_version != "LEGACY_MIGRATION_V1"
    ):
        raise ValueError("legacy invoice Fact is no longer the expected current review artifact")

    invoice_cfg = manifest["invoice"]
    if str(legacy_invoice.invoice_number) != str(invoice_cfg["invoice_number"]):
        raise ValueError("legacy invoice number does not match simulation reviewed invoice")
    if int(legacy_invoice.buyer_party_id or 0) != int(entity.party_id):
        raise ValueError("legacy invoice buyer Party does not match simulation entity")
    if int(legacy_invoice.seller_party_id or 0) != int(invoice_cfg["seller_party_id"]):
        raise ValueError("legacy invoice seller Party does not match simulation reviewed invoice")
    for field in ("net_amount", "vat_amount", "gross_amount"):
        if _money(getattr(legacy_invoice, field)) != _money(invoice_cfg[field]):
            raise ValueError(f"legacy invoice {field} does not match reviewed simulation evidence")

    seller = session.get(Party, int(invoice_cfg["seller_party_id"]))
    if seller is None or not seller.active:
        raise ValueError("simulation seller Party is missing or inactive")

    legacy_claim = session.get(InputVatClaim, int(manifest["legacy_input_claim_id"]))
    if legacy_claim is None:
        raise ValueError("legacy Input VAT claim not found")
    if legacy_claim.invoice_fact_id != legacy_fact_id:
        raise ValueError("legacy Input VAT claim does not belong to legacy invoice Fact")
    if legacy_claim.reporting_party_id != entity.party_id:
        raise ValueError("legacy Input VAT claim reporting Party mismatch")
    if (
        legacy_claim.claim_status != "NEEDS_REVIEW"
        or legacy_claim.evidence_type != "LEGACY_ASSUMPTION"
        or legacy_claim.confidence != "LOW"
    ):
        raise ValueError("legacy Input VAT claim is no longer the expected fail-closed candidate")

    claim_cfg = manifest["input_claim"]
    claim_period = _month(str(claim_cfg["claim_period"]))
    if legacy_claim.claim_period != claim_period:
        raise ValueError("legacy claim period differs from reviewed simulation claim period")
    if _money(legacy_claim.claim_amount) != _money(claim_cfg["claim_amount"]):
        raise ValueError("legacy claim amount differs from reviewed simulation claim amount")

    identity_key = build_invoice_identity_key(
        "DIGITAL_V1", invoice_number=str(invoice_cfg["invoice_number"])
    )
    business_identity_key = build_invoice_business_identity_key(identity_key)

    existing_identity = session.scalar(
        select(InvoiceFact).where(InvoiceFact.invoice_identity_key == identity_key)
    )
    if existing_identity is not None:
        raise ValueError(
            f"validated DIGITAL_V1 invoice identity already exists as Fact {existing_identity.fact_id}"
        )

    source_cfg = manifest["source_document"]
    existing_document = session.scalar(
        select(SourceDocument).where(
            SourceDocument.source_system == str(source_cfg["source_system"]),
            SourceDocument.external_document_id == str(source_cfg["external_document_id"]),
        )
    )
    if existing_document is not None:
        raise ValueError(
            f"simulation source document already exists as source_document_id={existing_document.id}"
        )

    seller_tax_id = str(invoice_cfg["seller_tax_registration_id"]).strip()
    active_tax_ids = session.scalars(
        select(PartyIdentifier).where(
            PartyIdentifier.party_id == seller.id,
            PartyIdentifier.identifier_type == "TAX_REGISTRATION_ID",
            PartyIdentifier.active.is_(True),
        )
    ).all()
    active_values = sorted({str(row.identifier_value) for row in active_tax_ids})
    if active_values and active_values != [seller_tax_id]:
        raise ValueError(
            f"seller has conflicting active TAX_REGISTRATION_ID values: {active_values}"
        )

    period = _month(str(manifest["tax_period"]))
    if period != claim_period:
        raise ValueError("pilot tax_period and Input VAT claim_period must match")
    existing_seed = session.scalar(
        select(VatOpeningBalanceSeed).where(
            VatOpeningBalanceSeed.reporting_party_id == entity.party_id,
            VatOpeningBalanceSeed.tax_period == period,
        )
    )
    if existing_seed is not None:
        raise ValueError("opening balance seed already exists for simulation pilot scope")
    existing_assertion = session.scalar(
        select(VatOutputPeriodAssertion).where(
            VatOutputPeriodAssertion.reporting_party_id == entity.party_id,
            VatOutputPeriodAssertion.tax_period == period,
        )
    )
    if existing_assertion is not None:
        raise ValueError("Output VAT assertion already exists for simulation pilot scope")

    return {
        "entity_party_id": int(entity.party_id),
        "legacy_fact": _fact_snapshot(legacy_fact, legacy_invoice),
        "legacy_claim": _claim_snapshot(legacy_claim),
        "seller_party_id": int(seller.id),
        "seller_tax_identifier_action": "NO_CHANGE" if active_values else "INSERT",
        "invoice_identity_key": identity_key,
        "business_identity_key": business_identity_key,
        "tax_period": str(period),
    }


def make_plan(
    session: Session,
    manifest: dict[str, Any],
    *,
    database: str,
    source_file: str | Path,
) -> dict[str, Any]:
    source_path = Path(source_file)
    if not source_path.is_file():
        raise ValueError(f"source invoice file not found: {source_path}")
    source_sha = _file_sha256(source_path)
    db_state = _validate_manifest_against_db(session, manifest, source_sha)
    core = {
        "kind": PLAN_KIND,
        "version": 1,
        "database": database,
        "alembic_head": EXPECTED_HEAD,
        "fixture_id": str(manifest["fixture_id"]),
        "simulation_fixture": True,
        "reviewed_by": str(manifest["reviewed_by"]),
        "reviewed_at": str(manifest["reviewed_at"]),
        "manifest_sha256": _canonical_hash(manifest),
        "source_file": {
            "filename": Path(source_file).name,
            "sha256": source_sha,
        },
        "manifest": manifest,
        "db_state": db_state,
    }
    return {**core, "plan_digest": _canonical_hash(core)}


def _load_plan(path: str | Path) -> dict[str, Any]:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if plan.get("kind") != PLAN_KIND or plan.get("version") != 1:
        raise ValueError("unsupported Task14 simulation saved plan")
    digest = plan.get("plan_digest")
    core = {key: value for key, value in plan.items() if key != "plan_digest"}
    if digest != _canonical_hash(core):
        raise ValueError("Task14 simulation plan_digest mismatch")
    if plan.get("simulation_fixture") is not True:
        raise ValueError("saved plan is not a simulation fixture")
    return plan


def _expected_invoice_validation_snapshot(
    *,
    fact_id: int,
    manifest: dict[str, Any],
    entity_party_id: int,
    seller_tax_id: str,
    identity_key: str,
    business_identity_key: str,
) -> InvoiceEvidenceSnapshot:
    cfg = manifest["invoice"]
    return InvoiceEvidenceSnapshot(
        fact_id=fact_id,
        current_validation_status="DRAFT",
        business_identity_key=business_identity_key,
        invoice_identity_key=identity_key,
        invoice_identity_version="DIGITAL_V1",
        invoice_number=str(cfg["invoice_number"]),
        invoice_code=None,
        invoice_date=date.fromisoformat(str(cfg["invoice_date"])),
        invoice_status="VALID",
        seller_party_id=int(cfg["seller_party_id"]),
        buyer_party_id=entity_party_id,
        gross_amount=_money(cfg["gross_amount"]),
        net_amount=_money(cfg["net_amount"]),
        vat_amount=_money(cfg["vat_amount"]),
        currency=str(cfg.get("currency") or "CNY"),
        line_count=1,
        incomplete_line_count=0,
        line_missing_tax_rate_count=0,
        line_tax_rates=(Decimal(str(cfg["line"]["tax_rate"])),),
        line_net_sum=_money(cfg["line"]["net_amount"]),
        line_vat_sum=_money(cfg["line"]["vat_amount"]),
        provenance_count=1,
        document_provenance_count=1,
        validated_document_provenance_count=1,
        seller_tax_identifier_count=1,
        seller_tax_identifier_value=seller_tax_id,
        reversal_relation_count=0,
        void_relation_count=0,
    )


def apply_plan(session: Session, plan: dict[str, Any]) -> dict[str, Any]:
    if _head(session) != EXPECTED_HEAD or plan["alembic_head"] != EXPECTED_HEAD:
        raise ValueError("Task14 simulation Alembic head changed after PLAN review")
    manifest = plan["manifest"]
    if _canonical_hash(manifest) != plan["manifest_sha256"]:
        raise ValueError("Task14 simulation manifest changed after PLAN review")

    # Recheck all mutable source rows without relying on the local source file at APPLY time.
    state = _validate_manifest_against_db(
        session,
        manifest,
        str(plan["source_file"]["sha256"]),
    )
    if state != plan["db_state"]:
        raise ValueError("stale Task14 simulation plan: database evidence changed after PLAN review")

    reviewed_at = datetime.fromisoformat(str(plan["reviewed_at"]).replace("Z", "+00:00"))
    source_cfg = manifest["source_document"]
    invoice_cfg = manifest["invoice"]
    claim_cfg = manifest["input_claim"]
    entity_party_id = int(state["entity_party_id"])
    seller_party_id = int(state["seller_party_id"])

    document = SourceDocument(
        source_system=str(source_cfg["source_system"]),
        external_document_id=str(source_cfg["external_document_id"]),
        filename=str(source_cfg["filename"]),
        mime_type=str(source_cfg.get("mime_type") or "application/pdf"),
        file_sha256=str(source_cfg["file_sha256"]).lower(),
        document_type="VAT_SPECIAL_INVOICE",
        processed_at=reviewed_at,
        source_uri=str(source_cfg["source_uri"]),
        status="VALIDATED",
    )
    session.add(document)
    session.flush()

    seller_tax_id = str(invoice_cfg["seller_tax_registration_id"])
    if state["seller_tax_identifier_action"] == "INSERT":
        session.add(
            PartyIdentifier(
                party_id=seller_party_id,
                identifier_type="TAX_REGISTRATION_ID",
                identifier_value=seller_tax_id,
                source_system="SIMULATION_PROJECT_ARCHIVE",
                active=True,
            )
        )
        session.flush()

    fact = Fact(
        fact_type="INVOICE",
        business_identity_key=str(state["business_identity_key"]),
        version_no=1,
        is_current=True,
        validation_status="DRAFT",
    )
    session.add(fact)
    session.flush()

    invoice = InvoiceFact(
        fact_id=fact.id,
        seller_party_id=seller_party_id,
        buyer_party_id=entity_party_id,
        invoice_identity_key=str(state["invoice_identity_key"]),
        invoice_identity_version="DIGITAL_V1",
        invoice_number=str(invoice_cfg["invoice_number"]),
        invoice_code=None,
        invoice_medium="DIGITAL",
        invoice_category="SPECIAL",
        invoice_date=date.fromisoformat(str(invoice_cfg["invoice_date"])),
        invoice_status="VALID",
        document_type="VAT_SPECIAL_INVOICE",
        gross_amount=_money(invoice_cfg["gross_amount"]),
        net_amount=_money(invoice_cfg["net_amount"]),
        vat_amount=_money(invoice_cfg["vat_amount"]),
        currency=str(invoice_cfg.get("currency") or "CNY"),
    )
    session.add(invoice)
    line_cfg = invoice_cfg["line"]
    session.add(
        InvoiceLine(
            invoice_fact_id=fact.id,
            line_no=1,
            item_name=str(line_cfg["item_name"]),
            category="SERVICE",
            net_amount=_money(line_cfg["net_amount"]),
            vat_amount=_money(line_cfg["vat_amount"]),
            tax_rate=Decimal(str(line_cfg["tax_rate"])),
        )
    )
    session.add(
        FactProvenance(
            fact_id=fact.id,
            document_id=document.id,
            page_start=1,
            page_end=1,
            confidence=Decimal("1.00000"),
            extraction_model="SIMULATION_REVIEW",
            extraction_model_version="V1",
            original_extracted_value=json.dumps(invoice_cfg, ensure_ascii=False, sort_keys=True),
            verified_value=json.dumps(invoice_cfg, ensure_ascii=False, sort_keys=True),
            verifier_user_id=str(plan["reviewed_by"]),
            verification_reason=(
                "User-authorized synthetic project archive treated as reviewed simulation evidence"
            ),
        )
    )
    session.flush()

    decision = evaluate_invoice_evidence(
        _expected_invoice_validation_snapshot(
            fact_id=fact.id,
            manifest=manifest,
            entity_party_id=entity_party_id,
            seller_tax_id=seller_tax_id,
            identity_key=str(state["invoice_identity_key"]),
            business_identity_key=str(state["business_identity_key"]),
        ),
        allowed_tax_rates=(Decimal(str(line_cfg["tax_rate"])),),
    )
    if decision.desired_status != "VALID":
        raise ValueError(
            "simulation reviewed Invoice Fact failed deterministic validation: "
            + json.dumps(decision.as_dict(), ensure_ascii=False, default=str)
        )
    fact.validation_status = "VALID"

    legacy_fact = session.get(Fact, int(manifest["legacy_invoice_fact_id"]))
    legacy_claim = session.get(InputVatClaim, int(manifest["legacy_input_claim_id"]))
    assert legacy_fact is not None and legacy_claim is not None
    legacy_fact.is_current = False
    legacy_fact.validation_status = "SUPERSEDED"
    session.add(
        FactRelationship(
            source_fact_id=fact.id,
            target_fact_id=legacy_fact.id,
            relationship_type="REPLACES",
            reason="Task14 reviewed simulation invoice evidence replaces Task08 legacy migration artifact",
        )
    )

    new_claim = InputVatClaim(
        invoice_fact_id=fact.id,
        reporting_party_id=entity_party_id,
        claim_period=_month(str(claim_cfg["claim_period"])),
        claim_amount=_money(claim_cfg["claim_amount"]),
        event_type="CLAIM",
        claim_status="CONFIRMED",
        evidence_type="DOCUMENT_EVIDENCE",
        confidence="HIGH",
        source_document_id=document.id,
        source_system="SIMULATION_PROJECT_ARCHIVE",
        external_claim_id=(
            f"{plan['fixture_id']}:INPUT_VAT:{invoice_cfg['invoice_number']}"
        ),
        reviewed_by=str(plan["reviewed_by"]),
        reviewed_at=reviewed_at,
        note=(
            "Reviewed synthetic Task14 pilot claim; replaces LEGACY_ASSUMPTION claim "
            f"{legacy_claim.id}"
        ),
    )
    session.add(new_claim)
    session.flush()
    legacy_claim.claim_status = "SUPERSEDED"
    legacy_claim.reviewed_by = str(plan["reviewed_by"])
    legacy_claim.reviewed_at = reviewed_at
    legacy_claim.note = (
        (legacy_claim.note or "").rstrip()
        + f" | Task14 simulation reviewed SUPERSEDED by claim {new_claim.id}"
    ).strip(" |")

    period = _month(str(manifest["tax_period"]))
    opening = VatOpeningBalanceSeed(
        reporting_party_id=entity_party_id,
        tax_period=period,
        opening_input_credit=_money(manifest["opening_input_credit"]),
        source_document_id=None,
        source=(
            "SIMULATION_FIXTURE: user-authorized reviewed project scenario opening Input VAT credit"
        ),
        reviewed=True,
        reviewed_by=str(plan["reviewed_by"]),
        reviewed_at=reviewed_at,
        note=(
            f"fixture={plan['fixture_id']}; synthetic business evidence, not a real-world tax filing"
        ),
    )
    session.add(opening)

    assertion = VatOutputPeriodAssertion(
        reporting_party_id=entity_party_id,
        tax_period=period,
        asserted_output_vat_total=_money(manifest["asserted_output_vat_total"]),
        source_document_id=None,
        source=(
            "SIMULATION_FIXTURE: user-authorized reviewed project scenario confirms zero monthly Output VAT"
        ),
        reviewed=True,
        reviewed_by=str(plan["reviewed_by"]),
        reviewed_at=reviewed_at,
        note=(
            f"fixture={plan['fixture_id']}; explicit 0.00 completeness assertion for synthetic pilot"
        ),
    )
    session.add(assertion)
    session.flush()

    return {
        "kind": RESULT_KIND,
        "version": 1,
        "fixture_id": plan["fixture_id"],
        "simulation_fixture": True,
        "source_document_id": document.id,
        "validated_invoice_fact_id": fact.id,
        "superseded_legacy_invoice_fact_id": legacy_fact.id,
        "confirmed_input_vat_claim_id": new_claim.id,
        "superseded_legacy_input_claim_id": legacy_claim.id,
        "opening_balance_seed_id": opening.id,
        "output_period_assertion_id": assertion.id,
        "tax_period": str(period),
        "opening_input_credit": str(opening.opening_input_credit),
        "asserted_output_vat_total": str(assertion.asserted_output_vat_total),
        "confirmed_input_vat_amount": str(new_claim.claim_amount),
        "plan_digest": plan["plan_digest"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest")
    parser.add_argument("--source-file")
    parser.add_argument("--plan")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--confirm-simulation-fixture")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    database_url = _database_url()
    database = str(make_url(database_url).database)
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    with Session(engine) as session:
        if not args.apply:
            if not args.manifest or not args.source_file or args.plan:
                raise SystemExit(
                    "PLAN mode requires --manifest and --source-file, and does not accept --plan"
                )
            manifest = _load_manifest(args.manifest)
            result = make_plan(
                session,
                manifest,
                database=database,
                source_file=args.source_file,
            )
            session.rollback()
        else:
            if not args.plan or args.manifest or args.source_file:
                raise SystemExit(
                    "APPLY mode requires exact saved --plan and does not accept --manifest/--source-file"
                )
            current_db = str(
                session.connection().exec_driver_sql("SELECT current_database()").scalar_one()
            )
            if args.confirm_database != current_db:
                raise SystemExit("--confirm-database must exactly match current_database()")
            plan = _load_plan(args.plan)
            if plan.get("database") != current_db:
                raise SystemExit("saved simulation plan targets a different database")
            if args.confirm_simulation_fixture != plan.get("fixture_id"):
                raise SystemExit(
                    "--confirm-simulation-fixture must exactly match saved fixture_id"
                )
            result = apply_plan(session, plan)
            session.commit()
            result = {"database": current_db, **result}

    engine.dispose()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
