"""Phase 4 deterministic accounting and book-tax engine.

The engine consumes only accepted/current Canonical Facts plus static Project
master attributes. Dynamic accounting truth, including transaction price, is
resolved from Canonical Facts. Payment cash flow never enters P&L.

Phase 4 v3 adds two read-only deterministic extensions:
- invoice-triggered accrual auto reversal (explicit reversal remains authoritative);
- optional whole-fact ``as_of`` cutoff used by the external period rollforward layer.
"""
from __future__ import annotations

import calendar
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import text

from ..models import Project
from .accrual_matching import match_accrual_reversals
from .canonical_project_summary import resolve_project_transaction_price
from .canonical_ssot import consolidate_invoice_facts, load_current_facts

ENGINE_VERSION = "canonical-accounting-phase4-v3"
DEFAULT_CIT_RATE = Decimal("0.25")
_MONEY = Decimal("0.01")


def _d(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None or value == "":
        return default
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default
    return result if result.is_finite() else default


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _payload(fact: dict[str, Any]) -> dict[str, Any]:
    value = fact.get("payload") or {}
    return value if isinstance(value, dict) else {}


def _fact_id(fact: dict[str, Any]) -> int:
    return int(fact.get("fact_id") or fact.get("id") or 0)


def _lineage_rows(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for fact in facts:
        rows.append(
            {
                "fact_id": _fact_id(fact),
                "fact_type": str(fact.get("fact_type") or ""),
                "business_key": str(fact.get("business_key") or ""),
                "fact_version": int(fact.get("fact_version") or 0),
                "source_hash": str(fact.get("source_hash") or ""),
                "source_document_id": int(fact.get("source_document_id") or 0),
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            item["fact_type"],
            item["business_key"],
            item["fact_version"],
            item["fact_id"],
        ),
    )


def fact_snapshot_hash(facts: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    versions = _lineage_rows(facts)
    encoded = json.dumps(versions, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), versions


def calculation_parameters_hash(
    cit_rate: Decimal,
    *,
    transaction_price: Decimal = Decimal("0"),
    transaction_price_fact_id: int | None = None,
    transaction_price_fact_version: int | None = None,
    as_of: date | None = None,
) -> tuple[str, dict[str, str]]:
    parameters = {
        "cit_rate": str(cit_rate),
        "transaction_price": str(transaction_price),
        "transaction_price_fact_id": str(transaction_price_fact_id or ""),
        "transaction_price_fact_version": str(transaction_price_fact_version or ""),
    }
    # Preserve historical snapshot hashes for the default project-to-date path.
    if as_of is not None:
        parameters["as_of"] = as_of.isoformat()
    encoded = json.dumps(parameters, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), parameters


def _parse_business_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    token = str(value).strip()
    if not token:
        return None
    try:
        return date.fromisoformat(token[:10])
    except ValueError:
        pass
    if len(token) == 7 and token[4:6].upper() == "-Q":
        try:
            year = int(token[:4])
            quarter = int(token[6])
            if quarter not in {1, 2, 3, 4}:
                return None
            month = quarter * 3
            return date(year, month, calendar.monthrange(year, month)[1])
        except (ValueError, IndexError):
            return None
    if len(token) == 7 and token[4] == "-" and token[5:7].isdigit():
        try:
            year = int(token[:4])
            month = int(token[5:7])
            return date(year, month, calendar.monthrange(year, month)[1])
        except (ValueError, IndexError):
            return None
    return None


def _fact_effective_date(fact: dict[str, Any]) -> date | None:
    item = _payload(fact)
    fact_type = str(fact.get("fact_type") or "").strip().lower()
    field_order: dict[str, tuple[str, ...]] = {
        "invoice": (
            "invoice_date",
            "accounting_date",
            "posting_date",
            "effective_date",
            "date",
            "period",
        ),
        "accrual": (
            "accrual_date",
            "accounting_date",
            "posting_date",
            "effective_date",
            "as_of_date",
            "date",
            "period",
        ),
        "progress": (
            "as_of_date",
            "effective_date",
            "date",
            "period",
        ),
        "tax_adjustment": (
            "accounting_date",
            "effective_date",
            "posting_date",
            "date",
            "period",
        ),
        "contract": (
            "effective_date",
            "contract_date",
            "signing_date",
            "date",
            "period",
        ),
    }
    fields = field_order.get(
        fact_type,
        ("accounting_date", "effective_date", "posting_date", "date", "period"),
    )
    for field in fields:
        parsed = _parse_business_date(item.get(field))
        if parsed is not None:
            return parsed

    # Accepted-at is a deterministic last-resort availability date.  It prevents
    # an as-of view from reading facts that were not yet accepted when no
    # economic date exists in the payload.
    return _parse_business_date(fact.get("accepted_at"))


def _accrual_reversal_effective_date(fact: dict[str, Any]) -> date | None:
    item = _payload(fact)
    if max(Decimal("0"), _d(item.get("reversal_amount"))) <= 0:
        return None
    for field in (
        "reversal_date",
        "reversal_accounting_date",
        "reversal_posting_date",
        "reversal_effective_date",
    ):
        parsed = _parse_business_date(item.get(field))
        if parsed is not None:
            return parsed
    # Existing Phase 4 facts may only carry reversal_amount. For those facts the
    # current-version accepted_at is the conservative date on which that reversal
    # became available; this prevents a later reversal from leaking backward.
    return _parse_business_date(fact.get("accepted_at"))


def _coerce_as_of(value: date | str | None) -> date | None:
    if value is None:
        return None
    parsed = _parse_business_date(value)
    if parsed is None:
        raise ValueError("as_of must be an ISO date")
    return parsed


def _facts_as_of(
    facts: list[dict[str, Any]],
    cutoff: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    included: list[dict[str, Any]] = []
    data_gaps: list[dict[str, Any]] = []
    for fact in facts:
        effective = _fact_effective_date(fact)
        if effective is None:
            data_gaps.append(
                {
                    "code": "ACCOUNTING_FACT_DATE_MISSING",
                    "fact_id": _fact_id(fact),
                    "fact_type": str(fact.get("fact_type") or ""),
                }
            )
            continue
        if effective <= cutoff:
            projected = fact
            if str(fact.get("fact_type") or "").strip().lower() == "accrual":
                item = _payload(fact)
                if max(Decimal("0"), _d(item.get("reversal_amount"))) > 0:
                    reversal_effective = _accrual_reversal_effective_date(fact)
                    if reversal_effective is None:
                        data_gaps.append(
                            {
                                "code": "ACCRUAL_REVERSAL_DATE_MISSING",
                                "fact_id": _fact_id(fact),
                            }
                        )
                        projected = {**fact, "payload": {**item, "reversal_amount": 0}}
                    elif reversal_effective > cutoff:
                        projected = {**fact, "payload": {**item, "reversal_amount": 0}}
            included.append(projected)
    return included, data_gaps


def calculate_phase4_model(
    *,
    transaction_price: Decimal,
    external_revenue_documentary: Decimal,
    external_invoiced_cost: Decimal,
    invoice_tax_addback: Decimal,
    accrual_facts: list[dict[str, Any]],
    progress_facts: list[dict[str, Any]],
    tax_adjustment_facts: list[dict[str, Any]],
    cit_rate: Decimal = DEFAULT_CIT_RATE,
    auto_reversal_by_accrual: dict[int, Decimal] | None = None,
    accrual_match_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    auto_reversal_by_accrual = auto_reversal_by_accrual or {}
    accrued_unbilled = Decimal("0")
    tax_addback_accrual = Decimal("0")
    capitalized = Decimal("0")
    explicit_reversal_total = Decimal("0")
    auto_reversal_total = Decimal("0")

    for fact in accrual_facts:
        item = _payload(fact)
        amount = max(Decimal("0"), _d(item.get("amount")))
        explicit_reversal = min(
            amount,
            max(Decimal("0"), _d(item.get("reversal_amount"))),
        )
        eligible_after_explicit = max(Decimal("0"), amount - explicit_reversal)
        auto_reversal = min(
            eligible_after_explicit,
            max(Decimal("0"), _d(auto_reversal_by_accrual.get(_fact_id(fact)))),
        )
        remaining = max(
            Decimal("0"),
            amount - explicit_reversal - auto_reversal,
        )
        explicit_reversal_total += explicit_reversal
        auto_reversal_total += auto_reversal
        if item.get("capitalized") is True:
            capitalized += remaining
            continue
        accrued_unbilled += remaining
        if item.get("tax_deductible") is not True:
            tax_addback_accrual += remaining

    incurred_cost = external_invoiced_cost + accrued_unbilled

    progress_sorted = sorted(
        progress_facts,
        key=lambda fact: (
            str(_payload(fact).get("as_of_date") or ""),
            int(fact.get("fact_version") or 0),
            _fact_id(fact),
        ),
    )
    latest_progress = _payload(progress_sorted[-1]) if progress_sorted else {}
    explicit_completion = latest_progress.get("completion_percent")
    estimated_total_cost = _d(latest_progress.get("estimated_total_cost"))
    explicit_revenue = latest_progress.get("recognized_revenue")

    recognition_basis = "missing_progress_evidence"
    if explicit_completion is not None:
        completion = min(Decimal("1"), max(Decimal("0"), _d(explicit_completion)))
        recognition_basis = "certified_completion_percent"
    elif estimated_total_cost > 0:
        completion = min(Decimal("1"), max(Decimal("0"), incurred_cost / estimated_total_cost))
        recognition_basis = "cost_to_cost"
    else:
        completion = Decimal("0")

    if explicit_revenue is not None:
        recognized_revenue = max(Decimal("0"), _d(explicit_revenue))
        recognition_basis = "certified_recognized_revenue"
    else:
        recognized_revenue = transaction_price * completion

    recognized_cost = incurred_cost
    accounting_profit = recognized_revenue - recognized_cost

    explicit_add = Decimal("0")
    explicit_deduct = Decimal("0")
    for fact in tax_adjustment_facts:
        item = _payload(fact)
        amount = max(Decimal("0"), _d(item.get("amount")))
        direction = str(item.get("direction") or "").strip().upper()
        if direction == "ADD":
            explicit_add += amount
        elif direction == "DEDUCT":
            explicit_deduct += amount

    tax_additions = invoice_tax_addback + tax_addback_accrual + explicit_add
    taxable_income_before_loss_offset = accounting_profit + tax_additions - explicit_deduct
    taxable_income_current = max(Decimal("0"), taxable_income_before_loss_offset)
    current_cit = taxable_income_current * cit_rate
    matching = accrual_match_audit or {
        "status": "READY",
        "matches": [],
        "data_gaps": [],
        "read_only": True,
    }

    return {
        "recognition": {
            "basis": recognition_basis,
            "completion_percent": completion,
            "transaction_price": _money(transaction_price),
            "estimated_total_cost": _money(estimated_total_cost),
            "incurred_cost": _money(incurred_cost),
            "recognized_revenue": _money(recognized_revenue),
            "recognized_cost": _money(recognized_cost),
            "documentary_external_revenue": _money(external_revenue_documentary),
            "progress_evidence_present": bool(progress_facts),
        },
        "accruals": {
            "unbilled_cost": _money(accrued_unbilled),
            "capitalized_not_expensed": _money(capitalized),
            "tax_addback_until_deductible": _money(tax_addback_accrual),
            "explicit_reversal_total": _money(explicit_reversal_total),
            "auto_reversal_total": _money(auto_reversal_total),
            "auto_match_count": len(matching.get("matches") or []),
            "auto_matches": matching.get("matches") or [],
            "matching_status": matching.get("status", "READY"),
            "matching_data_gaps": matching.get("data_gaps") or [],
        },
        "book_tax": {
            "accounting_profit": _money(accounting_profit),
            "invoice_nondeductible_addback": _money(invoice_tax_addback),
            "accrual_nondeductible_addback": _money(tax_addback_accrual),
            "explicit_tax_additions": _money(explicit_add),
            "explicit_tax_deductions": _money(explicit_deduct),
            "taxable_income_before_loss_offset": _money(taxable_income_before_loss_offset),
            "taxable_income_current": _money(taxable_income_current),
            "cit_rate": cit_rate,
            "current_cit": _money(current_cit),
        },
    }


def _invoice_tax_addback(invoice_facts: list[dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for fact in invoice_facts:
        item = _payload(fact)
        if item.get("cit_deductible") is not False:
            continue
        direction = str(item.get("direction") or "").strip().lower()
        if direction not in {"in", "input", "purchase"}:
            continue
        net = _d(item.get("net_amount", item.get("net")))
        vat = _d(item.get("vat_amount", item.get("vat")))
        deductible_vat = item.get("deductible") is True
        total += net + (Decimal("0") if deductible_vat else vat)
    return total


def build_project_accounting(
    db,
    project_id: int,
    *,
    cit_rate: Decimal = DEFAULT_CIT_RATE,
    as_of: date | str | None = None,
) -> dict[str, Any]:
    project = db.get(Project, int(project_id))
    if project is None:
        raise LookupError(f"project not found: {project_id}")

    cutoff = _coerce_as_of(as_of)
    current_facts = load_current_facts(db, int(project_id))
    cutoff_gaps: list[dict[str, Any]] = []
    if cutoff is None:
        all_facts = current_facts
    else:
        all_facts, cutoff_gaps = _facts_as_of(current_facts, cutoff)

    by_type: dict[str, list[dict[str, Any]]] = {}
    for fact in all_facts:
        by_type.setdefault(str(fact.get("fact_type") or ""), []).append(fact)

    internal_codes = {
        str(code).strip().upper()
        for code in db.execute(text("SELECT code FROM entities WHERE active = TRUE")).scalars().all()
        if code
    }
    invoice_facts = by_type.get("invoice", [])
    accrual_facts = by_type.get("accrual", [])
    boundary = consolidate_invoice_facts(invoice_facts, internal_codes)
    transaction = resolve_project_transaction_price(
        db,
        int(project_id),
        contract_facts=by_type.get("contract", []),
    )
    transaction_price = _d(transaction["amount"])
    accrual_matching = match_accrual_reversals(
        accrual_facts,
        invoice_facts,
        internal_codes,
    )
    model = calculate_phase4_model(
        transaction_price=transaction_price,
        external_revenue_documentary=_d(boundary.get("external_revenue")),
        external_invoiced_cost=_d(boundary.get("external_cost")),
        invoice_tax_addback=_invoice_tax_addback(invoice_facts),
        accrual_facts=accrual_facts,
        progress_facts=by_type.get("progress", []),
        tax_adjustment_facts=by_type.get("tax_adjustment", []),
        cit_rate=cit_rate,
        auto_reversal_by_accrual=accrual_matching["auto_reversal_by_accrual"],
        accrual_match_audit=accrual_matching,
    )
    snapshot_hash, versions = fact_snapshot_hash(all_facts)
    params_hash, parameters = calculation_parameters_hash(
        cit_rate,
        transaction_price=transaction_price,
        transaction_price_fact_id=transaction["source_fact_id"],
        transaction_price_fact_version=transaction["fact_version"],
        as_of=cutoff,
    )
    preview_version = f"PREVIEW-{ENGINE_VERSION}-{snapshot_hash[:8]}-{params_hash[:8]}"
    data_gaps = [*cutoff_gaps, *(accrual_matching.get("data_gaps") or [])]
    return {
        "status": "DEGRADED" if data_gaps else "READY",
        "project_id": int(project_id),
        "project_code": str(getattr(project, "code", "") or ""),
        "source_of_truth": "canonical_facts",
        "legacy_v3_facts_used": False,
        "payment_in_pnl": False,
        "engine_version": ENGINE_VERSION,
        "as_of": cutoff.isoformat() if cutoff is not None else None,
        "cutoff_basis": (
            "canonical_fact_business_date_with_accepted_at_fallback"
            if cutoff is not None
            else "current_project_to_date"
        ),
        "data_gaps": data_gaps,
        **model,
        "boundary": boundary,
        "transaction_price_source": {
            "status": transaction["status"],
            "fact_id": transaction["source_fact_id"],
            "fact_version": transaction["fact_version"],
            "contract_no": transaction["contract_no"],
        },
        "lineage": {
            "fact_snapshot_hash": snapshot_hash,
            "fact_versions": versions,
            "engine_version": ENGINE_VERSION,
            "report_version": preview_version,
            "calculation_parameters_hash": params_hash,
            "calculation_parameters": parameters,
        },
    }


def snapshot_project_accounting(db, project_id: int, *, cit_rate: Decimal = DEFAULT_CIT_RATE) -> dict[str, Any]:
    result = build_project_accounting(db, int(project_id), cit_rate=cit_rate)
    lineage = result["lineage"]
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 4_000_000_000 + int(project_id)})
    existing = db.execute(
        text(
            "SELECT id, report_sequence, report_version, result_json "
            "FROM accounting_report_snapshots "
            "WHERE project_id=:project_id AND engine_version=:engine_version "
            "AND fact_snapshot_hash=:fact_snapshot_hash "
            "AND calculation_parameters_hash=:calculation_parameters_hash"
        ),
        {
            "project_id": int(project_id),
            "engine_version": ENGINE_VERSION,
            "fact_snapshot_hash": lineage["fact_snapshot_hash"],
            "calculation_parameters_hash": lineage["calculation_parameters_hash"],
        },
    ).mappings().first()
    if existing is not None:
        stored = existing["result_json"]
        if isinstance(stored, str):
            stored = json.loads(stored)
        return stored

    sequence = int(
        db.execute(
            text("SELECT COALESCE(MAX(report_sequence), 0) + 1 FROM accounting_report_snapshots WHERE project_id=:project_id"),
            {"project_id": int(project_id)},
        ).scalar_one()
    )
    report_version = f"P{int(project_id)}-R{sequence}"
    result["lineage"]["report_version"] = report_version
    payload = json.loads(json.dumps(result, default=str, ensure_ascii=False))
    db.execute(
        text(
            "INSERT INTO accounting_report_snapshots ("
            "project_id, report_sequence, report_version, engine_version, fact_snapshot_hash, "
            "calculation_parameters_hash, fact_versions_json, calculation_parameters_json, result_json"
            ") VALUES ("
            ":project_id, :report_sequence, :report_version, :engine_version, :fact_snapshot_hash, "
            ":calculation_parameters_hash, CAST(:fact_versions_json AS jsonb), "
            "CAST(:calculation_parameters_json AS jsonb), CAST(:result_json AS jsonb))"
        ),
        {
            "project_id": int(project_id),
            "report_sequence": sequence,
            "report_version": report_version,
            "engine_version": ENGINE_VERSION,
            "fact_snapshot_hash": lineage["fact_snapshot_hash"],
            "calculation_parameters_hash": lineage["calculation_parameters_hash"],
            "fact_versions_json": json.dumps(lineage["fact_versions"], ensure_ascii=False, default=str),
            "calculation_parameters_json": json.dumps(lineage["calculation_parameters"], ensure_ascii=False, default=str),
            "result_json": json.dumps(payload, ensure_ascii=False, default=str),
        },
    )
    return payload


def list_accounting_snapshots(db, project_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT report_version, engine_version, fact_snapshot_hash, "
            "calculation_parameters_hash, calculation_parameters_json, created_at "
            "FROM accounting_report_snapshots WHERE project_id=:project_id "
            "ORDER BY report_sequence DESC"
        ),
        {"project_id": project_id},
    ).mappings().all()
    return [dict(row) for row in rows]


__all__ = [
    "ENGINE_VERSION",
    "DEFAULT_CIT_RATE",
    "build_project_accounting",
    "calculate_phase4_model",
    "calculation_parameters_hash",
    "fact_snapshot_hash",
    "list_accounting_snapshots",
    "snapshot_project_accounting",
]
