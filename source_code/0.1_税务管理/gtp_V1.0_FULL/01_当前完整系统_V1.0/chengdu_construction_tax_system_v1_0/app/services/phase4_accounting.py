"""Phase 4 deterministic accounting and book-tax engine.

The engine consumes only accepted/current Canonical Facts plus Project master
attributes.  It never treats payment cash flow as P&L and never reads legacy
contract/invoice/cashflow tables for accounting truth.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import text

from ..models import Project
from .canonical_ssot import consolidate_invoice_facts, load_current_facts

ENGINE_VERSION = "canonical-accounting-phase4-v1"
DEFAULT_CIT_RATE = Decimal("0.25")
_MONEY = Decimal("0.01")


def _d(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


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
) -> dict[str, Any]:
    """Pure deterministic cost-to-cost recognition and book-tax calculation."""
    accrued_unbilled = Decimal("0")
    tax_addback_accrual = Decimal("0")
    capitalized = Decimal("0")
    for fact in accrual_facts:
        item = _payload(fact)
        amount = max(
            Decimal("0"),
            _d(item.get("amount")) - _d(item.get("reversal_amount")),
        )
        if bool(item.get("capitalized", False)):
            capitalized += amount
            continue
        accrued_unbilled += amount
        if item.get("tax_deductible") is not True:
            tax_addback_accrual += amount

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
        completion = min(
            Decimal("1"),
            max(Decimal("0"), incurred_cost / estimated_total_cost),
        )
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
) -> dict[str, Any]:
    project = db.get(Project, int(project_id))
    if project is None:
        raise LookupError(f"project not found: {project_id}")

    all_facts = load_current_facts(db, int(project_id))
    by_type: dict[str, list[dict[str, Any]]] = {}
    for fact in all_facts:
        by_type.setdefault(str(fact.get("fact_type") or ""), []).append(fact)

    internal_codes = {
        str(code).strip().upper()
        for code in db.execute(text("SELECT code FROM entities WHERE active = TRUE")).scalars().all()
        if code
    }
    invoice_facts = by_type.get("invoice", [])
    boundary = consolidate_invoice_facts(invoice_facts, internal_codes)
    transaction_price = _d(
        getattr(project, "contract_total", None)
        or getattr(project, "contract_amount", None)
    )
    model = calculate_phase4_model(
        transaction_price=transaction_price,
        external_revenue_documentary=_d(boundary.get("external_revenue")),
        external_invoiced_cost=_d(boundary.get("external_cost")),
        invoice_tax_addback=_invoice_tax_addback(invoice_facts),
        accrual_facts=by_type.get("accrual", []),
        progress_facts=by_type.get("progress", []),
        tax_adjustment_facts=by_type.get("tax_adjustment", []),
        cit_rate=cit_rate,
    )
    snapshot_hash, versions = fact_snapshot_hash(all_facts)
    preview_version = f"PREVIEW-{ENGINE_VERSION}-{snapshot_hash[:12]}"
    return {
        "project_id": int(project_id),
        "project_code": str(getattr(project, "code", "") or ""),
        "source_of_truth": "canonical_facts",
        "legacy_v3_facts_used": False,
        "payment_in_pnl": False,
        "engine_version": ENGINE_VERSION,
        **model,
        "boundary": boundary,
        "lineage": {
            "fact_snapshot_hash": snapshot_hash,
            "fact_versions": versions,
            "engine_version": ENGINE_VERSION,
            "report_version": preview_version,
        },
    }


def snapshot_project_accounting(
    db,
    project_id: int,
    *,
    cit_rate: Decimal = DEFAULT_CIT_RATE,
) -> dict[str, Any]:
    result = build_project_accounting(db, project_id, cit_rate=cit_rate)
    lineage = result["lineage"]
    existing = db.execute(
        text(
            "SELECT report_version, result_json FROM accounting_report_snapshots "
            "WHERE project_id=:project_id AND engine_version=:engine_version "
            "AND fact_snapshot_hash=:fact_hash ORDER BY id DESC LIMIT 1"
        ),
        {
            "project_id": project_id,
            "engine_version": ENGINE_VERSION,
            "fact_hash": lineage["fact_snapshot_hash"],
        },
    ).mappings().first()
    if existing:
        stored = existing["result_json"]
        return stored if isinstance(stored, dict) else json.loads(stored)

    next_version = int(
        db.execute(
            text(
                "SELECT COALESCE(MAX(report_sequence), 0) + 1 "
                "FROM accounting_report_snapshots WHERE project_id=:project_id"
            ),
            {"project_id": project_id},
        ).scalar_one()
    )
    report_version = f"P{project_id}-R{next_version}"
    result["lineage"]["report_version"] = report_version
    db.execute(
        text(
            "INSERT INTO accounting_report_snapshots ("
            "project_id, report_sequence, report_version, engine_version, "
            "fact_snapshot_hash, fact_versions_json, result_json"
            ") VALUES ("
            ":project_id, :sequence, :report_version, :engine_version, :fact_hash, "
            "CAST(:fact_versions AS jsonb), CAST(:result AS jsonb))"
        ),
        {
            "project_id": project_id,
            "sequence": next_version,
            "report_version": report_version,
            "engine_version": ENGINE_VERSION,
            "fact_hash": lineage["fact_snapshot_hash"],
            "fact_versions": json.dumps(
                lineage["fact_versions"],
                ensure_ascii=False,
                default=str,
            ),
            "result": json.dumps(result, ensure_ascii=False, default=str),
        },
    )
    return result


def list_accounting_snapshots(db, project_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT report_version, engine_version, fact_snapshot_hash, created_at "
            "FROM accounting_report_snapshots WHERE project_id=:project_id "
            "ORDER BY report_sequence DESC"
        ),
        {"project_id": project_id},
    ).mappings().all()
    return [dict(row) for row in rows]


__all__ = [
    "DEFAULT_CIT_RATE",
    "ENGINE_VERSION",
    "build_project_accounting",
    "calculate_phase4_model",
    "fact_snapshot_hash",
    "list_accounting_snapshots",
    "snapshot_project_accounting",
]
