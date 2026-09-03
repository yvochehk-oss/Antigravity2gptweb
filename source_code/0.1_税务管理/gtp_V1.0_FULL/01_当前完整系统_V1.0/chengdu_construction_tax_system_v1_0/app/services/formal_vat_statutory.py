"""Formal VAT statutory read model backed only by the official V3 VAT ledger.

This module is intentionally a read boundary.  It never derives VAT from invoice
facts and never repairs/rebuilds missing materializations.  The sole official
resource for one legal entity + one month is:

    TaxPeriodState.current_run_id -> SUCCEEDED CalculationRun -> EntityVatLedger

Any broken or missing link fails closed so callers cannot accidentally treat a
projection or a live fact aggregation as statutory VAT.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
from typing import Any

from sqlalchemy import text

from .legal_entity_fact_periods import LegalEntityNotFoundError
from .legal_entity_scope import _code

_PERIOD_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_SOURCE_OF_TRUTH = (
    "tax_period_states.current_run_id->calculation_runs->entity_vat_ledgers"
)
_RESOURCE_TYPE = "FORMAL_VAT_STATUTORY_V1"


class FormalVatStatutoryResourceNotFoundError(LookupError):
    """Raised when the requested legal-entity/month has no official VAT resource."""


class FormalVatStatutoryResourceIntegrityError(RuntimeError):
    """Raised when the official VAT pointer chain exists but is inconsistent."""


def _tax_period(period: str) -> date:
    normalized = str(period or "").strip()
    if not _PERIOD_RE.fullmatch(normalized):
        raise ValueError("period must be YYYY-MM")
    return date.fromisoformat(f"{normalized}-01")


def _iso_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _iso_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _money(value: Any) -> str:
    if value is None:
        raise FormalVatStatutoryResourceIntegrityError(
            "official VAT ledger contains a missing monetary value"
        )
    return f"{Decimal(str(value)):.2f}"


def get_formal_vat_statutory_resource(
    db,
    entity_code: str,
    period: str,
) -> dict[str, Any]:
    """Read one official VAT materialization without any fact-side fallback."""
    wanted = _code(entity_code)
    tax_period = _tax_period(period)

    scope = db.execute(
        text(
            "SELECT p.id AS reporting_party_id, ie.canonical_code AS entity_code "
            "FROM parties p "
            "JOIN internal_entities ie ON ie.party_id = p.id "
            "WHERE p.party_type = 'internal' "
            "AND p.active = TRUE "
            "AND ie.active = TRUE "
            "AND ie.legal_entity = TRUE "
            "AND UPPER(ie.canonical_code) = :entity_code "
            "LIMIT 1"
        ),
        {"entity_code": wanted},
    ).mappings().one_or_none()
    if scope is None:
        raise LegalEntityNotFoundError(wanted)

    party_id = int(scope["reporting_party_id"])
    rows = db.execute(
        text(
            "SELECT "
            "tps.state AS period_state, tps.state_version, "
            "tps.current_run_id, tps.closed_run_id, "
            "cr.id AS calculation_run_id, "
            "cr.reporting_party_id AS run_party_id, cr.tax_type AS run_tax_type, "
            "cr.tax_period AS run_tax_period, cr.run_kind, cr.run_status, "
            "cr.ruleset_version, cr.input_snapshot_sha256, cr.result_sha256, "
            "cr.completed_at, "
            "evl.id AS ledger_id, evl.reporting_party_id AS ledger_party_id, "
            "evl.tax_period AS ledger_tax_period, evl.opening_input_credit, "
            "evl.output_vat, evl.input_vat, evl.tax_prepayment, "
            "evl.vat_payable_before_prepayment, evl.closing_input_credit, "
            "evl.vat_payable_after_prepayment, evl.unapplied_tax_prepayment, "
            "evl.created_at AS ledger_created_at "
            "FROM tax_period_states tps "
            "LEFT JOIN calculation_runs cr ON cr.id = tps.current_run_id "
            "LEFT JOIN entity_vat_ledgers evl ON evl.calculation_run_id = cr.id "
            "WHERE tps.reporting_party_id = :reporting_party_id "
            "AND tps.tax_type = 'VAT' "
            "AND tps.tax_period = :tax_period"
        ),
        {"reporting_party_id": party_id, "tax_period": tax_period},
    ).mappings().all()

    if not rows or rows[0]["current_run_id"] is None:
        raise FormalVatStatutoryResourceNotFoundError(
            f"no official VAT resource for {wanted} {period}"
        )
    if len(rows) != 1:
        raise FormalVatStatutoryResourceIntegrityError(
            f"official VAT resource is ambiguous for {wanted} {period}"
        )

    row = rows[0]
    current_run_id = int(row["current_run_id"])
    if row["period_state"] == "CLOSED" and row["closed_run_id"] != current_run_id:
        raise FormalVatStatutoryResourceIntegrityError(
            "closed VAT period does not pin the current official run"
        )

    expected_period = tax_period.isoformat()
    if row["calculation_run_id"] != current_run_id:
        raise FormalVatStatutoryResourceIntegrityError(
            "official VAT current_run_id does not resolve to a calculation run"
        )
    if (
        row["run_party_id"] != party_id
        or row["run_tax_type"] != "VAT"
        or _iso_date(row["run_tax_period"]) != expected_period
    ):
        raise FormalVatStatutoryResourceIntegrityError(
            "official VAT calculation run scope does not match the requested resource"
        )
    if row["run_status"] != "SUCCEEDED":
        raise FormalVatStatutoryResourceIntegrityError(
            "official VAT calculation run is not SUCCEEDED"
        )
    result_sha256 = str(row["result_sha256"] or "")
    if len(result_sha256) != 64:
        raise FormalVatStatutoryResourceIntegrityError(
            "official VAT calculation run has no valid result hash"
        )

    if row["ledger_id"] is None:
        raise FormalVatStatutoryResourceIntegrityError(
            "official VAT calculation run has no EntityVatLedger materialization"
        )
    if (
        row["ledger_party_id"] != party_id
        or _iso_date(row["ledger_tax_period"]) != expected_period
    ):
        raise FormalVatStatutoryResourceIntegrityError(
            "official VAT ledger scope does not match the requested resource"
        )

    return {
        "status": "READY",
        "resource_type": _RESOURCE_TYPE,
        "source_of_truth": _SOURCE_OF_TRUTH,
        "entity_code": wanted,
        "reporting_party_id": party_id,
        "period": period,
        "tax_period": expected_period,
        "period_state": row["period_state"],
        "state_version": int(row["state_version"]),
        "calculation_run": {
            "id": current_run_id,
            "run_kind": row["run_kind"],
            "run_status": row["run_status"],
            "ruleset_version": row["ruleset_version"],
            "input_snapshot_sha256": row["input_snapshot_sha256"],
            "result_sha256": result_sha256,
            "completed_at": _iso_datetime(row["completed_at"]),
        },
        "vat_ledger": {
            "id": int(row["ledger_id"]),
            "opening_input_credit": _money(row["opening_input_credit"]),
            "output_vat": _money(row["output_vat"]),
            "input_vat": _money(row["input_vat"]),
            "tax_prepayment": _money(row["tax_prepayment"]),
            "vat_payable_before_prepayment": _money(
                row["vat_payable_before_prepayment"]
            ),
            "closing_input_credit": _money(row["closing_input_credit"]),
            "vat_payable_after_prepayment": _money(
                row["vat_payable_after_prepayment"]
            ),
            "unapplied_tax_prepayment": _money(row["unapplied_tax_prepayment"]),
            "created_at": _iso_datetime(row["ledger_created_at"]),
        },
    }


__all__ = [
    "FormalVatStatutoryResourceIntegrityError",
    "FormalVatStatutoryResourceNotFoundError",
    "get_formal_vat_statutory_resource",
]
