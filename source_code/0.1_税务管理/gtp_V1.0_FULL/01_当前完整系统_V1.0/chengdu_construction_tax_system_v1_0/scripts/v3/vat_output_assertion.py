#!/usr/bin/env python3
"""PLAN/APPLY reviewed monthly Output VAT completeness assertions.

This tool never infers Output VAT from invoice_date or legacy period. A reviewed
assertion may explicitly state 0.00, but only when a human reviewer supplies the
supporting source/reason. APPLY is additive and refuses conflicting existing rows.
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.v3_party_models import InternalEntity
from app.v3_vat_ledger_models import OutputVatEvent
from app.v3_vat_review_models import VatOutputPeriodAssertion

EXPECTED_HEAD = "85_v3_vat_output_period_assertions"
MANIFEST_KIND = "V3_TASK14_OUTPUT_VAT_ASSERTION_MANIFEST"
PLAN_KIND = "V3_TASK14_OUTPUT_VAT_ASSERTION_PLAN"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task14b Output VAT assertion loader is PostgreSQL-only")
    return value


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _period(value: str) -> date:
    parsed = date.fromisoformat(f"{value}-01" if len(value) == 7 else value)
    if parsed.day != 1:
        raise ValueError("tax_period must be month-start")
    return parsed


def _manifest(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("kind") != MANIFEST_KIND or payload.get("version") != 1:
        raise ValueError("unsupported Output VAT assertion manifest")
    if payload.get("reviewed") is not True:
        raise ValueError("manifest must be reviewed=true")
    if not str(payload.get("reviewed_by") or "").strip():
        raise ValueError("reviewed_by is required")
    reviewed_at = str(payload.get("reviewed_at") or "").strip()
    if not reviewed_at:
        raise ValueError("reviewed_at is required")
    datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    return payload


def _plan(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    db_head = str(session.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").scalar_one())
    if db_head != EXPECTED_HEAD:
        raise ValueError(f"formal DB head must be {EXPECTED_HEAD}")

    reviewed_at = datetime.fromisoformat(str(payload["reviewed_at"]).replace("Z", "+00:00"))
    items: list[dict[str, Any]] = []
    seen: set[tuple[int, date]] = set()
    for index, item in enumerate(payload.get("assertions") or []):
        entity_code = str(item["entity_code"])
        entity = session.scalar(select(InternalEntity).where(InternalEntity.canonical_code == entity_code))
        if entity is None:
            raise ValueError(f"unknown internal entity at index {index}: {entity_code}")
        tax_period = _period(str(item["tax_period"]))
        scope = (int(entity.party_id), tax_period)
        if scope in seen:
            raise ValueError(f"duplicate entity/month assertion in manifest: {entity_code} {tax_period}")
        seen.add(scope)

        asserted_total = Decimal(str(item["asserted_output_vat_total"])).quantize(Decimal("0.01"))
        source = str(item.get("source") or "").strip()
        if not source:
            raise ValueError(f"source is required at index {index}")

        confirmed_total = Decimal(
            session.scalar(
                select(func.coalesce(func.sum(OutputVatEvent.vat_amount), 0)).where(
                    OutputVatEvent.reporting_party_id == entity.party_id,
                    OutputVatEvent.output_vat_period == tax_period,
                    OutputVatEvent.event_status == "CONFIRMED",
                )
            )
            or 0
        ).quantize(Decimal("0.01"))
        if confirmed_total != asserted_total:
            raise ValueError(
                f"confirmed Output VAT events total {confirmed_total} does not match reviewed assertion "
                f"{asserted_total} for {entity_code} {tax_period}"
            )

        existing = session.scalar(
            select(VatOutputPeriodAssertion).where(
                VatOutputPeriodAssertion.reporting_party_id == entity.party_id,
                VatOutputPeriodAssertion.tax_period == tax_period,
            )
        )
        if existing is not None and (
            not existing.reviewed
            or Decimal(existing.asserted_output_vat_total) != asserted_total
            or existing.source != source
        ):
            raise ValueError(f"existing Output VAT assertion conflicts for {entity_code} {tax_period}")

        items.append(
            {
                "action": "NO_CHANGE" if existing is not None else "INSERT",
                "entity_code": entity_code,
                "reporting_party_id": int(entity.party_id),
                "tax_period": str(tax_period),
                "asserted_output_vat_total": str(asserted_total),
                "confirmed_output_vat_event_total": str(confirmed_total),
                "source_document_id": int(item["source_document_id"]) if item.get("source_document_id") is not None else None,
                "source": source,
                "reviewed_by": str(payload["reviewed_by"]),
                "reviewed_at": reviewed_at.isoformat(),
                "note": item.get("note"),
            }
        )

    core = {
        "kind": PLAN_KIND,
        "version": 1,
        "database": session.connection().exec_driver_sql("SELECT current_database()").scalar_one(),
        "alembic_head": db_head,
        "items": items,
    }
    return {**core, "plan_digest": _hash(core)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    payload = _manifest(args.manifest)
    database_url = _database_url()
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    with Session(engine) as session:
        plan = _plan(session, payload)
        result: dict[str, Any] = plan
        if args.apply:
            current_db = session.connection().exec_driver_sql("SELECT current_database()").scalar_one()
            if args.confirm_database != current_db:
                raise SystemExit("--confirm-database must exactly match current_database()")
            created_ids: list[int] = []
            for item in plan["items"]:
                if item["action"] == "NO_CHANGE":
                    continue
                row = VatOutputPeriodAssertion(
                    reporting_party_id=item["reporting_party_id"],
                    tax_period=date.fromisoformat(item["tax_period"]),
                    asserted_output_vat_total=Decimal(item["asserted_output_vat_total"]),
                    source_document_id=item["source_document_id"],
                    source=item["source"],
                    reviewed=True,
                    reviewed_by=item["reviewed_by"],
                    reviewed_at=datetime.fromisoformat(item["reviewed_at"]),
                    note=item["note"],
                )
                session.add(row)
                session.flush()
                created_ids.append(row.id)
            session.commit()
            result = {
                "kind": "V3_TASK14_OUTPUT_VAT_ASSERTION_RESULT",
                "version": 1,
                "database": current_db,
                "alembic_head": EXPECTED_HEAD,
                "plan_digest": plan["plan_digest"],
                "created_assertion_ids": created_ids,
                "created_count": len(created_ids),
                "items": plan["items"],
            }

    engine.dispose()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
