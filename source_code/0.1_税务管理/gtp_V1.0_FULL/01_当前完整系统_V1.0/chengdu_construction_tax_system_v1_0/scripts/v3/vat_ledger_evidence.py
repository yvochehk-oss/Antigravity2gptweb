#!/usr/bin/env python3
"""Reviewed evidence loader for Task14 Output VAT attribution/opening seeds.

No fuzzy inference is performed. The manifest is human-reviewed evidence and the
loader validates it against current V3 Facts/Party tax profiles before mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.domain.party.resolver import TaxProfileWindow, resolve_reporting_party
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity, PartyTaxProfile
from app.v3_vat_ledger_models import OutputVatEvent, VatOpeningBalanceSeed

EXPECTED_HEAD = "84_v3_entity_vat_ledgers"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task14 evidence loader is PostgreSQL-only")
    return value


def _load_manifest(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("kind") != "V3_TASK14_VAT_EVIDENCE_MANIFEST" or payload.get("version") != 1:
        raise ValueError("unsupported Task14 evidence manifest")
    if payload.get("reviewed") is not True:
        raise ValueError("manifest must be explicitly reviewed=true")
    if not str(payload.get("reviewed_by") or "").strip():
        raise ValueError("reviewed_by is required")
    if not str(payload.get("reviewed_at") or "").strip():
        raise ValueError("reviewed_at is required")
    datetime.fromisoformat(str(payload["reviewed_at"]).replace("Z", "+00:00"))
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


def _period(value: str) -> date:
    parsed = date.fromisoformat(f"{value}-01" if len(value) == 7 else value)
    if parsed.day != 1:
        raise ValueError("VAT periods must be month-start dates")
    return parsed


def _entity_party_id(session: Session, entity_code: str) -> int:
    row = session.scalar(select(InternalEntity).where(InternalEntity.canonical_code == entity_code))
    if row is None:
        raise ValueError(f"unknown internal entity: {entity_code}")
    return int(row.party_id)


def _validate(session: Session, manifest: dict[str, Any]) -> dict[str, Any]:
    profiles = _profiles(session)
    reviewed_at = datetime.fromisoformat(str(manifest["reviewed_at"]).replace("Z", "+00:00"))
    output_plan: list[dict[str, Any]] = []
    seed_plan: list[dict[str, Any]] = []
    seen_external: set[tuple[str, str]] = set()

    for index, item in enumerate(manifest.get("output_vat_events") or []):
        invoice_fact_id = int(item["invoice_fact_id"])
        entity_code = str(item["entity_code"])
        reporting_party_id = _entity_party_id(session, entity_code)
        period = _period(str(item["output_vat_period"]))
        amount = Decimal(str(item["vat_amount"]))
        event_type = str(item["event_type"])
        evidence_type = str(item["evidence_type"])
        confidence = str(item["confidence"])
        source_system = str(item.get("source_system") or "TASK14_REVIEW")
        external_event_id = str(item.get("external_event_id") or f"TASK14:{invoice_fact_id}:{period}:{event_type}")
        identity = (source_system, external_event_id)
        if identity in seen_external:
            raise ValueError(f"duplicate output event source identity in manifest: {identity}")
        seen_external.add(identity)
        if event_type not in {"OUTPUT", "REVERSAL", "ADJUSTMENT"}:
            raise ValueError(f"invalid output event type at index {index}")
        if evidence_type not in {"DOCUMENT_EVIDENCE", "MANUAL_REVIEW"}:
            raise ValueError("confirmed Output VAT evidence cannot be LEGACY_ASSUMPTION")
        if confidence not in {"HIGH", "MEDIUM"}:
            raise ValueError("confirmed Output VAT evidence must be HIGH or MEDIUM confidence")
        source_document_id = item.get("source_document_id")
        if evidence_type == "DOCUMENT_EVIDENCE" and source_document_id is None:
            raise ValueError("DOCUMENT_EVIDENCE requires source_document_id")

        row = session.execute(
            select(InvoiceFact, Fact)
            .join(Fact, Fact.id == InvoiceFact.fact_id)
            .where(InvoiceFact.fact_id == invoice_fact_id)
        ).one_or_none()
        if row is None:
            raise ValueError(f"invoice Fact not found: {invoice_fact_id}")
        invoice, fact = row
        if fact.fact_type != "INVOICE" or not fact.is_current or fact.validation_status != "VALID":
            raise ValueError(f"invoice Fact {invoice_fact_id} is not current VALID")
        if invoice.invoice_status == "VOIDED":
            raise ValueError(f"VOIDED invoice Fact {invoice_fact_id} cannot produce Output VAT")
        if invoice.seller_party_id is None:
            raise ValueError(f"invoice Fact {invoice_fact_id} has unresolved seller Party")
        resolved = resolve_reporting_party(
            invoice.seller_party_id,
            period,
            profiles,
            tax_type="VAT",
            require_reviewed=True,
        )
        if resolved != reporting_party_id:
            raise ValueError(
                f"invoice Fact {invoice_fact_id} seller reports to Party {resolved}, not {reporting_party_id}"
            )
        invoice_vat = Decimal(invoice.vat_amount or Decimal("0.00"))
        if event_type == "OUTPUT":
            if invoice.invoice_status == "RED" or amount <= 0 or amount != invoice_vat:
                raise ValueError(f"OUTPUT event must exactly match positive non-red invoice VAT for Fact {invoice_fact_id}")
        elif event_type == "REVERSAL":
            if invoice.invoice_status != "RED" or amount >= 0 or amount != invoice_vat:
                raise ValueError(f"REVERSAL event must exactly match RED invoice VAT for Fact {invoice_fact_id}")
        elif amount == 0:
            raise ValueError("ADJUSTMENT Output VAT amount cannot be zero")

        existing = session.scalar(
            select(OutputVatEvent).where(
                OutputVatEvent.source_system == source_system,
                OutputVatEvent.external_event_id == external_event_id,
            )
        )
        output_plan.append({
            "action": "NO_CHANGE" if existing is not None else "INSERT",
            "invoice_fact_id": invoice_fact_id,
            "reporting_party_id": reporting_party_id,
            "entity_code": entity_code,
            "output_vat_period": period,
            "vat_amount": amount,
            "event_type": event_type,
            "evidence_type": evidence_type,
            "confidence": confidence,
            "source_document_id": int(source_document_id) if source_document_id is not None else None,
            "source_system": source_system,
            "external_event_id": external_event_id,
            "reviewed_by": str(manifest["reviewed_by"]),
            "reviewed_at": reviewed_at,
            "note": item.get("note"),
        })

    for index, item in enumerate(manifest.get("opening_balance_seeds") or []):
        entity_code = str(item["entity_code"])
        reporting_party_id = _entity_party_id(session, entity_code)
        period = _period(str(item["tax_period"]))
        amount = Decimal(str(item["opening_input_credit"]))
        if amount < 0:
            raise ValueError("opening Input VAT credit cannot be negative")
        source = str(item.get("source") or "").strip()
        if not source:
            raise ValueError(f"opening seed source required at index {index}")
        existing = session.scalar(
            select(VatOpeningBalanceSeed).where(
                VatOpeningBalanceSeed.reporting_party_id == reporting_party_id,
                VatOpeningBalanceSeed.tax_period == period,
            )
        )
        if existing is not None and (
            Decimal(existing.opening_input_credit) != amount or not existing.reviewed
        ):
            raise ValueError(f"existing opening seed conflicts with reviewed manifest for {entity_code} {period}")
        seed_plan.append({
            "action": "NO_CHANGE" if existing is not None else "INSERT",
            "entity_code": entity_code,
            "reporting_party_id": reporting_party_id,
            "tax_period": period,
            "opening_input_credit": amount,
            "source_document_id": int(item["source_document_id"]) if item.get("source_document_id") is not None else None,
            "source": source,
            "reviewed_by": str(manifest["reviewed_by"]),
            "reviewed_at": reviewed_at,
            "note": item.get("note"),
        })

    return {"output_vat_events": output_plan, "opening_balance_seeds": seed_plan}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    manifest = _load_manifest(args.manifest)
    database_url = _database_url()
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    with Session(engine) as session:
        head = str(session.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").scalar_one())
        if head != EXPECTED_HEAD:
            raise SystemExit(f"formal DB head must be {EXPECTED_HEAD}")
        plan = _validate(session, manifest)
        if args.apply:
            current_db = session.connection().exec_driver_sql("SELECT current_database()").scalar_one()
            if args.confirm_database != current_db:
                raise SystemExit("--confirm-database must exactly match current_database()")
            created_output_ids: list[int] = []
            created_seed_ids: list[int] = []
            for item in plan["output_vat_events"]:
                if item["action"] == "NO_CHANGE":
                    continue
                row = OutputVatEvent(
                    invoice_fact_id=item["invoice_fact_id"],
                    reporting_party_id=item["reporting_party_id"],
                    output_vat_period=item["output_vat_period"],
                    vat_amount=item["vat_amount"],
                    event_type=item["event_type"],
                    event_status="CONFIRMED",
                    evidence_type=item["evidence_type"],
                    confidence=item["confidence"],
                    source_document_id=item["source_document_id"],
                    source_system=item["source_system"],
                    external_event_id=item["external_event_id"],
                    reviewed_by=item["reviewed_by"],
                    reviewed_at=item["reviewed_at"],
                    note=item["note"],
                )
                session.add(row)
                session.flush()
                created_output_ids.append(row.id)
            for item in plan["opening_balance_seeds"]:
                if item["action"] == "NO_CHANGE":
                    continue
                row = VatOpeningBalanceSeed(
                    reporting_party_id=item["reporting_party_id"],
                    tax_period=item["tax_period"],
                    opening_input_credit=item["opening_input_credit"],
                    source_document_id=item["source_document_id"],
                    source=item["source"],
                    reviewed=True,
                    reviewed_by=item["reviewed_by"],
                    reviewed_at=item["reviewed_at"],
                    note=item["note"],
                )
                session.add(row)
                session.flush()
                created_seed_ids.append(row.id)
            session.commit()
            result: dict[str, Any] = {
                "kind": "V3_TASK14_VAT_EVIDENCE_RESULT",
                "database": current_db,
                "created_output_event_ids": created_output_ids,
                "created_opening_seed_ids": created_seed_ids,
                "plan": plan,
            }
        else:
            session.rollback()
            result = {
                "kind": "V3_TASK14_VAT_EVIDENCE_PLAN",
                "database": make_url(database_url).database,
                "reviewed_by": manifest["reviewed_by"],
                "reviewed_at": manifest["reviewed_at"],
                "plan": plan,
            }

    engine.dispose()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
