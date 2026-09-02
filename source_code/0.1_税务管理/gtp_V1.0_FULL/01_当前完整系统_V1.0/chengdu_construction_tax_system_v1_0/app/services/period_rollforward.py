"""Natural accounting period differential over Phase 4 cumulative positions."""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from .phase4_accounting import build_project_accounting

_MONEY = Decimal("0.01")
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$", re.IGNORECASE)


def _d(value: Any) -> Decimal:
    try:
        result = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def accounting_period_bounds(period: str) -> tuple[str, date, date]:
    token = str(period or "").strip().upper()
    month_match = _MONTH.fullmatch(token)
    if month_match:
        year = int(month_match.group(1))
        month = int(month_match.group(2))
        end_day = calendar.monthrange(year, month)[1]
        return "MONTH", date(year, month, 1), date(year, month, end_day)

    quarter_match = _QUARTER.fullmatch(token)
    if quarter_match:
        year = int(quarter_match.group(1))
        quarter = int(quarter_match.group(2))
        start_month = 1 + (quarter - 1) * 3
        end_month = start_month + 2
        end_day = calendar.monthrange(year, end_month)[1]
        return "QUARTER", date(year, start_month, 1), date(year, end_month, end_day)

    raise ValueError("period must use YYYY-MM or YYYY-Q1..Q4")


def _position(accounting: dict[str, Any]) -> dict[str, Decimal]:
    recognition = accounting.get("recognition") or {}
    book_tax = accounting.get("book_tax") or {}
    accruals = accounting.get("accruals") or {}
    return {
        "recognized_revenue": _money(_d(recognition.get("recognized_revenue"))),
        "recognized_cost": _money(_d(recognition.get("recognized_cost"))),
        "incurred_cost": _money(_d(recognition.get("incurred_cost"))),
        "accounting_profit": _money(_d(book_tax.get("accounting_profit"))),
        "taxable_income_before_loss_offset": _money(
            _d(book_tax.get("taxable_income_before_loss_offset"))
        ),
        "taxable_income_current": _money(_d(book_tax.get("taxable_income_current"))),
        "current_cit": _money(_d(book_tax.get("current_cit"))),
        "accrued_unbilled": _money(_d(accruals.get("unbilled_cost"))),
        "capitalized_not_expensed": _money(_d(accruals.get("capitalized_not_expensed"))),
    }


def _merge_gaps(opening: dict[str, Any], closing: dict[str, Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for label, payload in (("OPENING", opening), ("CLOSING", closing)):
        for gap in payload.get("data_gaps") or []:
            key = repr(gap)
            if key in seen:
                continue
            seen.add(key)
            if isinstance(gap, dict):
                result.append({"snapshot": label, **gap})
            else:
                result.append({"snapshot": label, "detail": str(gap)})
    return result


def build_period_rollforward(db, project_id: int, period: str) -> dict[str, Any]:
    """Return opening, single-period movement and closing cumulative positions.

    No P&L formula is duplicated here.  Opening and closing are Phase 4
    cumulative snapshots and movement is their exact mathematical difference.
    """
    grain, period_start, period_end = accounting_period_bounds(period)
    opening_cutoff = period_start - timedelta(days=1)
    opening_accounting = build_project_accounting(db, int(project_id), as_of=opening_cutoff)
    closing_accounting = build_project_accounting(db, int(project_id), as_of=period_end)

    opening = _position(opening_accounting)
    closing = _position(closing_accounting)
    movement = {key: _money(closing[key] - opening[key]) for key in opening}

    identity_checks = {
        key: _money(opening[key] + movement[key]) == closing[key]
        for key in opening
    }
    identity_checks["period_profit_equals_revenue_minus_cost"] = (
        movement["accounting_profit"]
        == _money(movement["recognized_revenue"] - movement["recognized_cost"])
    )

    data_gaps = _merge_gaps(opening_accounting, closing_accounting)
    status = (
        "DEGRADED"
        if data_gaps
        or opening_accounting.get("status") == "DEGRADED"
        or closing_accounting.get("status") == "DEGRADED"
        else "READY"
    )

    return {
        "status": status,
        "project_id": int(project_id),
        "period": str(period).strip().upper(),
        "grain": grain,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "opening_cutoff": opening_cutoff.isoformat(),
        "closing_cutoff": period_end.isoformat(),
        "opening": opening,
        "movement": movement,
        "closing": closing,
        "identity_checks": identity_checks,
        "identity_ok": all(identity_checks.values()),
        "source_of_truth": "analytics_canonical_facts_current",
        "basis": "phase4_cumulative_snapshot_differential",
        "engine_version": closing_accounting.get("engine_version"),
        "data_gaps": data_gaps,
        "read_only": True,
    }


__all__ = ["accounting_period_bounds", "build_period_rollforward"]
