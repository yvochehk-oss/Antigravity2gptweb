#!/usr/bin/env python3
"""Reviewed Task14c resolution workflow for legacy Input VAT claim assumptions.

The workflow never promotes a LEGACY_ASSUMPTION in place. A reviewed action may:
- REJECT the legacy claim; or
- atomically create a new evidence-backed CONFIRMED claim and mark the legacy
  assumption SUPERSEDED.

PLAN is read-only. APPLY binds to the reviewed saved plan and rechecks source state.
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

from app.domain.party.resolver import TaxProfileWindow, resolve_reporting_party  # noqa: E402
from app.v3_fact_models import Fact, InvoiceFact  # noqa: E402
from app.v3_party_models import PartyTaxProfile  # noqa: E402
from app.v3_tax_models import InputVatClaim  # noqa: E402

EXPECTED_HEAD = "86_v3_input_vat_claim_review_resolution"
MANIFEST_KIND = "V3_TASK14C_INPUT_VAT_CLAIM_REVIEW"
PLAN_KIND = "V3_TASK14C_INPUT_VAT_CLAIM_REVIEW_PLAN"
RESULT_KIND = "V3_TASK14C_INPUT_VAT_CLAIM_REVIEW_RESULT"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task14c Input VAT resolution is PostgreSQL-only")
    return value


def _canonical_hash(payload: Any) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _period(value: str) -> date:
    parsed = date.fromisoformat(f"{value}-01" if len(value) == 7 else value)
    if parsed.day != 1:
        raise ValueError("claim_period must be a month-start date")
    return parsed


def _load_manifest(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("kind") != MANIFEST_KIND or payload.get("version") != 1:
        raise ValueError("unsupported Task14c Input VAT review manifest")
    if payload.get("reviewed") is not True:
        raise ValueError("manifest must be explicitly reviewed=true")
    if not str(payload.get("reviewed_by") or "").strip():
        raise ValueError("reviewed_by is required")
    reviewed_at = str(payload.get("reviewed_at") or "").strip()
    if not reviewed_at:
        raise ValueError("reviewed_at is required")
    datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    if payload.get("action") not in {"REJECT", "REPLACE_CONFIRMED"}:
        raise ValueError("action must be REJECT or REPLACE_CONFIRMED")
    if not str(payload.get("reason") or "").strip():
        raise ValueError("review reason is required")
    return payload


def _profiles(session: Session) -> tuple[TaxProfileWindow, ...]:
    return tuple(
        TaxProfileWindow(
            party_id=row.party_id,
            tax_type=row.tax_type,
            reporting_party_id=row.reporting_party_id,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            reviewed=row.reviewed,
        )
        for row in session.scalars(select(PartyTaxProfile)).all()
    )


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
        "note": claim.note,
    }


def _validate_replacement(
    session: Session,
    *,
    legacy: InputVatClaim,
    replacement: dict[str, Any],
) -> dict[str, Any]:
    row = session.execute(
        select(InvoiceFact, Fact)
        .join(Fact, Fact.id == InvoiceFact.fact_id)
        .where(InvoiceFact.fact_id == legacy.invoice_fact_id)
    ).one_or_none()
    if row is None:
        raise ValueError("linked InvoiceFact is missing")
    invoice, fact = row
    if fact.fact_type != "INVOICE" or not fact.is_current or fact.validation_status != "VALID":
        raise ValueError("replacement CONFIRMED claim requires a current VALID InvoiceFact")
    if invoice.buyer_party_id is None:
        raise ValueError("replacement CONFIRMED claim requires resolved buyer Party")
    if invoice.invoice_status in {"VOIDED", "RED"}:
        raise ValueError("replacement CLAIM does not support VOIDED/RED invoices")

    claim_period = _period(str(replacement["claim_period"]))
    reporting_party_id = resolve_reporting_party(
        invoice.buyer_party_id,
        claim_period,
        _profiles(session),
        tax_type="VAT",
        require_reviewed=True,
    )
    if reporting_party_id != legacy.reporting_party_id:
        raise ValueError(
            f"reviewed reporting Party {reporting_party_id} does not match legacy claim Party {legacy.reporting_party_id}"
        )

    amount = Decimal(str(replacement["claim_amount"]))
    invoice_vat = Decimal(invoice.vat_amount or Decimal("0.00"))
    if amount <= 0:
        raise ValueError("replacement CLAIM amount must be positive")
    if invoice_vat <= 0 or amount > invoice_vat:
        raise ValueError("replacement CLAIM amount cannot exceed positive InvoiceFact VAT")

    evidence_type = str(replacement["evidence_type"])
    confidence = str(replacement["confidence"])
    if evidence_type not in {"DOCUMENT_EVIDENCE", "MANUAL_REVIEW"}:
        raise ValueError("replacement evidence_type must be DOCUMENT_EVIDENCE or MANUAL_REVIEW")
    if confidence not in {"HIGH", "MEDIUM"}:
        raise ValueError("replacement confidence must be HIGH or MEDIUM")
    source_document_id = replacement.get("source_document_id")
    if evidence_type == "DOCUMENT_EVIDENCE" and source_document_id is None:
        raise ValueError("DOCUMENT_EVIDENCE replacement requires source_document_id")
    source_system = str(replacement.get("source_system") or "").strip()
    external_claim_id = str(replacement.get("external_claim_id") or "").strip()
    if not source_system or not external_claim_id:
        raise ValueError("replacement source_system and external_claim_id are required")

    duplicate = session.scalar(
        select(InputVatClaim.id).where(
            InputVatClaim.invoice_fact_id == legacy.invoice_fact_id,
            InputVatClaim.reporting_party_id == legacy.reporting_party_id,
            InputVatClaim.claim_period == claim_period,
            InputVatClaim.claim_status == "CONFIRMED",
        )
    )
    if duplicate is not None:
        raise ValueError(f"a CONFIRMED claim already exists for this invoice/party/period: {duplicate}")
    source_duplicate = session.scalar(
        select(InputVatClaim.id).where(
            InputVatClaim.source_system == source_system,
            InputVatClaim.external_claim_id == external_claim_id,
        )
    )
    if source_duplicate is not None:
        raise ValueError(f"replacement source identity already exists: claim {source_duplicate}")

    return {
        "invoice_fact_id": legacy.invoice_fact_id,
        "reporting_party_id": legacy.reporting_party_id,
        "claim_period": str(claim_period),
        "claim_amount": str(amount.quantize(Decimal("0.01"))),
        "event_type": "CLAIM",
        "evidence_type": evidence_type,
        "confidence": confidence,
        "source_document_id": int(source_document_id) if source_document_id is not None else None,
        "source_system": source_system,
        "external_claim_id": external_claim_id,
        "note": replacement.get("note"),
    }


def make_plan(session: Session, manifest: dict[str, Any], database: str) -> dict[str, Any]:
    head = str(session.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").scalar_one())
    if head != EXPECTED_HEAD:
        raise ValueError(f"formal DB head must be {EXPECTED_HEAD}")
    legacy = session.get(InputVatClaim, int(manifest["legacy_claim_id"]))
    if legacy is None:
        raise ValueError("legacy claim not found")
    if legacy.evidence_type != "LEGACY_ASSUMPTION" or legacy.confidence != "LOW":
        raise ValueError("target claim is not a LOW LEGACY_ASSUMPTION")
    if legacy.claim_status != "NEEDS_REVIEW":
        raise ValueError("target legacy claim is no longer NEEDS_REVIEW")

    replacement_plan = None
    if manifest["action"] == "REPLACE_CONFIRMED":
        replacement = manifest.get("replacement")
        if not isinstance(replacement, dict):
            raise ValueError("REPLACE_CONFIRMED requires replacement object")
        replacement_plan = _validate_replacement(session, legacy=legacy, replacement=replacement)
    elif manifest.get("replacement") not in {None, {}}:
        raise ValueError("REJECT must not include a replacement claim")

    core = {
        "kind": PLAN_KIND,
        "version": 1,
        "database": database,
        "alembic_head": head,
        "reviewed_by": str(manifest["reviewed_by"]),
        "reviewed_at": str(manifest["reviewed_at"]),
        "reason": str(manifest["reason"]),
        "action": manifest["action"],
        "legacy_claim": _claim_snapshot(legacy),
        "replacement": replacement_plan,
    }
    return {**core, "plan_digest": _canonical_hash(core)}


def _load_plan(path: str) -> dict[str, Any]:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if plan.get("kind") != PLAN_KIND or plan.get("version") != 1:
        raise ValueError("unsupported Task14c saved plan")
    digest = plan.get("plan_digest")
    core = {k: v for k, v in plan.items() if k != "plan_digest"}
    if digest != _canonical_hash(core):
        raise ValueError("Task14c plan_digest mismatch")
    return plan


def apply_plan(session: Session, plan: dict[str, Any]) -> dict[str, Any]:
    head = str(session.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").scalar_one())
    if head != EXPECTED_HEAD or plan["alembic_head"] != head:
        raise ValueError("Task14c Alembic head changed after PLAN review")
    legacy = session.get(InputVatClaim, int(plan["legacy_claim"]["id"]))
    if legacy is None or _claim_snapshot(legacy) != plan["legacy_claim"]:
        raise ValueError("stale Task14c plan: legacy claim changed after review")

    reviewed_at = datetime.fromisoformat(str(plan["reviewed_at"]).replace("Z", "+00:00"))
    replacement_claim_id = None
    if plan["action"] == "REPLACE_CONFIRMED":
        replacement = plan["replacement"]
        # Re-run semantic validation against current invoice/tax-profile evidence.
        validated = _validate_replacement(session, legacy=legacy, replacement=replacement)
        if validated != replacement:
            raise ValueError("stale Task14c plan: replacement evidence changed")
        new_claim = InputVatClaim(
            invoice_fact_id=replacement["invoice_fact_id"],
            reporting_party_id=replacement["reporting_party_id"],
            claim_period=date.fromisoformat(replacement["claim_period"]),
            claim_amount=Decimal(replacement["claim_amount"]),
            event_type="CLAIM",
            claim_status="CONFIRMED",
            evidence_type=replacement["evidence_type"],
            confidence=replacement["confidence"],
            source_document_id=replacement["source_document_id"],
            source_system=replacement["source_system"],
            external_claim_id=replacement["external_claim_id"],
            reviewed_by=plan["reviewed_by"],
            reviewed_at=reviewed_at,
            note=(replacement.get("note") or "") + f" | replaces legacy claim {legacy.id}",
        )
        session.add(new_claim)
        session.flush()
        replacement_claim_id = new_claim.id
        legacy.claim_status = "SUPERSEDED"
        resolution_note = f"Task14c reviewed SUPERSEDED by claim {new_claim.id}: {plan['reason']}"
    else:
        legacy.claim_status = "REJECTED"
        resolution_note = f"Task14c reviewed REJECTED: {plan['reason']}"

    legacy.reviewed_by = plan["reviewed_by"]
    legacy.reviewed_at = reviewed_at
    legacy.note = ((legacy.note or "").rstrip() + " | " + resolution_note).strip(" |")
    session.flush()
    return {
        "kind": RESULT_KIND,
        "version": 1,
        "action": plan["action"],
        "legacy_claim_id": legacy.id,
        "legacy_claim_status": legacy.claim_status,
        "replacement_claim_id": replacement_claim_id,
        "plan_digest": plan["plan_digest"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest")
    parser.add_argument("--plan")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    database_url = _database_url()
    database = str(make_url(database_url).database)
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    with Session(engine) as session:
        if not args.apply:
            if not args.manifest or args.plan:
                raise SystemExit("PLAN mode requires --manifest and does not accept --plan")
            result = make_plan(session, _load_manifest(args.manifest), database)
        else:
            if not args.plan or args.manifest:
                raise SystemExit("APPLY mode requires exact saved --plan and no --manifest")
            current_db = session.connection().exec_driver_sql("SELECT current_database()").scalar_one()
            if args.confirm_database != current_db:
                raise SystemExit("--confirm-database must exactly match current_database()")
            plan = _load_plan(args.plan)
            if plan.get("database") != current_db:
                raise SystemExit("saved Task14c plan targets a different database")
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
