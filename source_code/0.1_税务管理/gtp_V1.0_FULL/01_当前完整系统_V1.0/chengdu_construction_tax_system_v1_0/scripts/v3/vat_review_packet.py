#!/usr/bin/env python3
"""Generate a read-only Task14 VAT evidence packet for human review.

The packet never confirms/rejects claims, never creates Output VAT events and
never invents an opening balance. Legacy invoice period/direction are presented
only as historical context and must not be treated as VAT attribution evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.models import Invoice  # noqa: E402
from app.v3_fact_models import Fact, FactProvenance, InvoiceFact, LegacyInvoiceMap  # noqa: E402
from app.v3_party_models import InternalEntity  # noqa: E402
from app.v3_period_models import TaxPeriodState  # noqa: E402
from app.v3_tax_models import InputVatClaim  # noqa: E402
from app.v3_vat_ledger_models import (  # noqa: E402
    EntityVatLedger,
    OutputVatEvent,
    VatOpeningBalanceSeed,
)

EXPECTED_HEAD = "84_v3_entity_vat_ledgers"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task14 VAT review packet is PostgreSQL-only")
    return value


def _period(value: str) -> date:
    parsed = date.fromisoformat(f"{value}-01" if len(value) == 7 else value)
    if parsed.day != 1:
        raise ValueError("period must be a month-start date")
    return parsed


def _previous_month(value: date) -> date:
    if value.month == 1:
        return date(value.year - 1, 12, 1)
    return date(value.year, value.month - 1, 1)


def _money(value: Any) -> str | None:
    if value is None:
        return None
    return str(Decimal(value).quantize(Decimal("0.01")))


def _fact_context(session: Session, invoice_fact_id: int) -> dict[str, Any]:
    row = session.execute(
        select(InvoiceFact, Fact)
        .join(Fact, Fact.id == InvoiceFact.fact_id)
        .where(InvoiceFact.fact_id == invoice_fact_id)
    ).one_or_none()
    if row is None:
        return {"invoice_fact_id": invoice_fact_id, "missing": True}
    invoice, fact = row
    mappings = session.scalars(
        select(LegacyInvoiceMap)
        .where(LegacyInvoiceMap.invoice_fact_id == invoice_fact_id)
        .order_by(LegacyInvoiceMap.legacy_invoice_id)
    ).all()
    legacy_rows: list[dict[str, Any]] = []
    for mapping in mappings:
        legacy = session.get(Invoice, mapping.legacy_invoice_id)
        legacy_rows.append({
            "legacy_invoice_id": mapping.legacy_invoice_id,
            "migration_status": mapping.migration_status,
            "migration_reason": mapping.reason,
            "legacy_direction": mapping.legacy_direction,
            "legacy_context_only_not_tax_attribution": None if legacy is None else {
                "project_id": legacy.project_id,
                "invoice_no": legacy.invoice_no,
                "period": legacy.period,
                "entity_code": legacy.entity_code,
                "direction": legacy.direction,
                "counterparty_code": legacy.counterparty_code,
                "category": legacy.category,
                "net": _money(legacy.net),
                "vat": _money(legacy.vat),
                "rate": str(legacy.rate) if legacy.rate is not None else None,
                "deductible": legacy.deductible,
                "note": legacy.note,
            },
        })
    provenance = [
        {
            "id": row.id,
            "document_id": row.document_id,
            "chunk_id": row.chunk_id,
            "page_start": row.page_start,
            "page_end": row.page_end,
            "confidence": str(row.confidence) if row.confidence is not None else None,
            "extraction_model": row.extraction_model,
            "extraction_model_version": row.extraction_model_version,
            "original_extracted_value": row.original_extracted_value,
            "verified_value": row.verified_value,
            "verifier_user_id": row.verifier_user_id,
            "verification_reason": row.verification_reason,
        }
        for row in session.scalars(
            select(FactProvenance)
            .where(FactProvenance.fact_id == invoice_fact_id)
            .order_by(FactProvenance.id)
        ).all()
    ]
    return {
        "invoice_fact_id": invoice_fact_id,
        "fact": {
            "fact_type": fact.fact_type,
            "validation_status": fact.validation_status,
            "is_current": fact.is_current,
            "business_identity_key": fact.business_identity_key,
            "version_no": fact.version_no,
        },
        "invoice": {
            "seller_party_id": invoice.seller_party_id,
            "buyer_party_id": invoice.buyer_party_id,
            "invoice_identity_version": invoice.invoice_identity_version,
            "invoice_number": invoice.invoice_number,
            "invoice_code": invoice.invoice_code,
            "invoice_date": str(invoice.invoice_date) if invoice.invoice_date else None,
            "invoice_status": invoice.invoice_status,
            "net_amount": _money(invoice.net_amount),
            "vat_amount": _money(invoice.vat_amount),
            "gross_amount": _money(invoice.gross_amount),
            "currency": invoice.currency,
        },
        "legacy_mappings": legacy_rows,
        "fact_provenance": provenance,
    }


def build_packet(session: Session, *, entity_code: str, period: date) -> dict[str, Any]:
    entity = session.scalar(
        select(InternalEntity).where(InternalEntity.canonical_code == entity_code)
    )
    if entity is None:
        raise ValueError(f"unknown internal entity: {entity_code}")
    party_id = int(entity.party_id)

    input_claims = session.scalars(
        select(InputVatClaim)
        .where(
            InputVatClaim.reporting_party_id == party_id,
            InputVatClaim.claim_period == period,
        )
        .order_by(InputVatClaim.id)
    ).all()
    input_items: list[dict[str, Any]] = []
    for claim in input_claims:
        input_items.append({
            "claim": {
                "id": claim.id,
                "invoice_fact_id": claim.invoice_fact_id,
                "claim_period": str(claim.claim_period),
                "claim_amount": _money(claim.claim_amount),
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
            },
            "invoice_fact_context": _fact_context(session, claim.invoice_fact_id),
            "review_question": (
                "Confirm, reject or supersede this Input VAT claim using real reviewed evidence. "
                "LEGACY_ASSUMPTION cannot be promoted directly to CONFIRMED."
                if claim.claim_status == "NEEDS_REVIEW"
                else None
            ),
        })

    output_events = session.scalars(
        select(OutputVatEvent)
        .where(
            OutputVatEvent.reporting_party_id == party_id,
            OutputVatEvent.output_vat_period == period,
        )
        .order_by(OutputVatEvent.id)
    ).all()
    output_items = [
        {
            "id": row.id,
            "invoice_fact_id": row.invoice_fact_id,
            "output_vat_period": str(row.output_vat_period),
            "vat_amount": _money(row.vat_amount),
            "event_type": row.event_type,
            "event_status": row.event_status,
            "evidence_type": row.evidence_type,
            "confidence": row.confidence,
            "source_document_id": row.source_document_id,
            "reviewed_by": row.reviewed_by,
            "reviewed_at": str(row.reviewed_at) if row.reviewed_at else None,
            "invoice_fact_context": _fact_context(session, row.invoice_fact_id),
        }
        for row in output_events
    ]

    legacy_output_context: list[dict[str, Any]] = []
    legacy_rows = session.scalars(
        select(Invoice)
        .where(
            Invoice.entity_code == entity_code,
            Invoice.period == period.strftime("%Y-%m"),
            Invoice.direction == "out",
        )
        .order_by(Invoice.id)
    ).all()
    for legacy in legacy_rows:
        mapping = session.get(LegacyInvoiceMap, legacy.id)
        legacy_output_context.append({
            "legacy_invoice_id": legacy.id,
            "invoice_no": legacy.invoice_no,
            "legacy_period": legacy.period,
            "legacy_direction": legacy.direction,
            "counterparty_code": legacy.counterparty_code,
            "net": _money(legacy.net),
            "vat": _money(legacy.vat),
            "rate": str(legacy.rate) if legacy.rate is not None else None,
            "note": legacy.note,
            "mapping_status": mapping.migration_status if mapping else None,
            "invoice_fact_id": mapping.invoice_fact_id if mapping else None,
            "warning": "context only; legacy period is not Output VAT attribution evidence",
        })

    seed = session.scalar(
        select(VatOpeningBalanceSeed).where(
            VatOpeningBalanceSeed.reporting_party_id == party_id,
            VatOpeningBalanceSeed.tax_period == period,
        )
    )
    prior_period = _previous_month(period)
    prior_state = session.scalar(
        select(TaxPeriodState).where(
            TaxPeriodState.reporting_party_id == party_id,
            TaxPeriodState.tax_type == "VAT",
            TaxPeriodState.tax_period == prior_period,
        )
    )
    prior_ledger = None
    if prior_state is not None and prior_state.current_run_id is not None:
        prior_ledger = session.scalar(
            select(EntityVatLedger).where(
                EntityVatLedger.calculation_run_id == prior_state.current_run_id
            )
        )

    unresolved_input_ids = [
        int(row.id) for row in input_claims if row.claim_status == "NEEDS_REVIEW"
    ]
    unresolved_output_ids = [
        int(row.id) for row in output_events if row.event_status == "NEEDS_REVIEW"
    ]
    opening_source_available = bool(
        prior_ledger is not None or (seed is not None and seed.reviewed)
    )

    return {
        "kind": "V3_TASK14_VAT_REVIEW_PACKET",
        "version": 1,
        "scope": {
            "entity_code": entity_code,
            "reporting_party_id": party_id,
            "period": period.strftime("%Y-%m"),
        },
        "input_vat_claims": input_items,
        "output_vat_events": output_items,
        "legacy_output_context_only": legacy_output_context,
        "opening_continuity": {
            "prior_period": prior_period.strftime("%Y-%m"),
            "prior_period_state_id": prior_state.id if prior_state else None,
            "prior_current_run_id": prior_state.current_run_id if prior_state else None,
            "prior_ledger_id": prior_ledger.id if prior_ledger else None,
            "prior_closing_input_credit": _money(prior_ledger.closing_input_credit) if prior_ledger else None,
            "opening_seed": None if seed is None else {
                "id": seed.id,
                "opening_input_credit": _money(seed.opening_input_credit),
                "source_document_id": seed.source_document_id,
                "source": seed.source,
                "reviewed": seed.reviewed,
                "reviewed_by": seed.reviewed_by,
                "reviewed_at": str(seed.reviewed_at) if seed.reviewed_at else None,
            },
        },
        "review_summary": {
            "unresolved_input_claim_ids": unresolved_input_ids,
            "unresolved_output_event_ids": unresolved_output_ids,
            "confirmed_output_event_count": sum(
                1 for row in output_events if row.event_status == "CONFIRMED"
            ),
            "opening_source_available": opening_source_available,
            "ledger_build_blocked": bool(
                unresolved_input_ids or unresolved_output_ids or not opening_source_available
            ),
            "questions": [
                "Resolve every NEEDS_REVIEW Input/Output VAT event from real evidence.",
                "Determine whether zero Output VAT for the month is supported; do not infer a period from invoice_date or legacy period.",
                "Provide a reviewed opening Input VAT credit seed if no prior official VAT ledger exists.",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entity", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    database_url = _database_url()
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        try:
            head = str(conn.exec_driver_sql(
                "SELECT version_num FROM alembic_version_tax"
            ).scalar_one())
            if head != EXPECTED_HEAD:
                raise SystemExit(f"formal DB head must be {EXPECTED_HEAD}")
            with Session(bind=conn, expire_on_commit=False) as session:
                result = build_packet(
                    session,
                    entity_code=args.entity,
                    period=_period(args.period),
                )
                result["database"] = str(conn.exec_driver_sql(
                    "SELECT current_database()"
                ).scalar_one())
                result["alembic_head"] = head
        finally:
            conn.exec_driver_sql("ROLLBACK")
    engine.dispose()

    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
