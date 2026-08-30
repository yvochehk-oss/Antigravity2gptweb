#!/usr/bin/env python3
"""Task14b wrapper for Entity VAT Ledger under revision 85.

Reuses Task14 builder semantics while adding the reviewed monthly Output VAT
completeness assertion to the source snapshot and stale-plan hash.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select

import entity_vat_ledger as base  # noqa: E402
from app.v3_vat_review_models import VatOutputPeriodAssertion  # noqa: E402

base.EXPECTED_HEAD = "85_v3_vat_output_period_assertions"
_original_source_snapshot = base._source_snapshot


def _source_snapshot(session, reporting_party_id, tax_period):
    snapshot = _original_source_snapshot(session, reporting_party_id, tax_period)
    assertion = session.scalar(
        select(VatOutputPeriodAssertion).where(
            VatOutputPeriodAssertion.reporting_party_id == reporting_party_id,
            VatOutputPeriodAssertion.tax_period == tax_period,
            VatOutputPeriodAssertion.reviewed.is_(True),
        )
    )
    if assertion is None:
        raise ValueError("reviewed Output VAT completeness assertion is required")

    confirmed_total = sum(
        (Decimal(row["amount"]) for row in snapshot["output_events"]),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    asserted_total = Decimal(assertion.asserted_output_vat_total).quantize(Decimal("0.01"))
    if confirmed_total != asserted_total:
        raise ValueError(
            f"confirmed Output VAT total {confirmed_total} does not match reviewed assertion {asserted_total}"
        )

    return {
        **snapshot,
        "output_assertion": {
            "id": assertion.id,
            "asserted_output_vat_total": str(asserted_total),
            "source": assertion.source,
            "source_document_id": assertion.source_document_id,
            "reviewed_by": assertion.reviewed_by,
            "reviewed_at": str(assertion.reviewed_at),
        },
    }


base._source_snapshot = _source_snapshot


if __name__ == "__main__":
    raise SystemExit(base.main())
