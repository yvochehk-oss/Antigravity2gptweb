"""Deterministic accrual-to-invoice auto matching.

This module is intentionally read-only.  It derives reversal allocations from
accepted/current Canonical Facts without mutating the facts themselves.

Matching contract:
- explicit ``reversal_amount`` on an accrual remains authoritative;
- only the unreversed remainder is eligible for auto matching;
- an external-input invoice must match contract + supplier + accounting cost;
- exactly one eligible accrual candidate is required;
- ambiguous candidates fail closed and are surfaced as data gaps.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable

_MONEY = Decimal("0.01")


def _d(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _payload(fact: dict[str, Any]) -> dict[str, Any]:
    payload = fact.get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _fact_id(fact: dict[str, Any]) -> int:
    return int(fact.get("fact_id") or fact.get("id") or 0)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _code(value: Any) -> str:
    return _clean(value).upper()


def _contract_key(payload: dict[str, Any]) -> str:
    return _code(
        payload.get("contract_no")
        or payload.get("contract_number")
        or payload.get("contract_code")
        or payload.get("related_contract_no")
    )


def _supplier_key(payload: dict[str, Any], *, invoice: bool) -> str:
    if invoice:
        code = (
            payload.get("seller_entity_code")
            or payload.get("seller_code")
            or payload.get("supplier_entity_code")
            or payload.get("supplier_code")
            or payload.get("vendor_code")
        )
        name = (
            payload.get("seller_name")
            or payload.get("supplier_name")
            or payload.get("vendor_name")
        )
    else:
        code = (
            payload.get("supplier_entity_code")
            or payload.get("supplier_code")
            or payload.get("vendor_code")
            or payload.get("seller_entity_code")
            or payload.get("seller_code")
        )
        name = (
            payload.get("supplier_name")
            or payload.get("vendor_name")
            or payload.get("seller_name")
        )
    normalized_code = _code(code)
    if normalized_code:
        return f"CODE:{normalized_code}"
    normalized_name = _code(name)
    return f"NAME:{normalized_name}" if normalized_name else ""


def _invoice_cost_amount(payload: dict[str, Any]) -> Decimal:
    """Return the same economic cost basis used by canonical boundary P&L."""
    net = _d(payload.get("net_amount", payload.get("net")))
    vat = _d(payload.get("vat_amount", payload.get("vat")))
    if net == 0 and payload.get("total_amount") is not None:
        net = _d(payload.get("total_amount")) - vat
    nondeductible_vat = Decimal("0") if bool(payload.get("deductible", True)) else vat
    return max(Decimal("0"), net + nondeductible_vat)


def _is_external_input_invoice(payload: dict[str, Any], internal_codes: set[str]) -> bool:
    seller = _code(payload.get("seller_entity_code") or payload.get("seller_code"))
    buyer = _code(payload.get("buyer_entity_code") or payload.get("buyer_code"))
    return bool(seller and buyer) and seller not in internal_codes and buyer in internal_codes


def match_accrual_reversals(
    accrual_facts: Iterable[dict[str, Any]],
    invoice_facts: Iterable[dict[str, Any]],
    internal_codes: set[str],
) -> dict[str, Any]:
    """Derive deterministic invoice-triggered accrual reversals.

    The function never guesses.  If more than one unreversed accrual has the
    same contract/supplier/amount tuple for an invoice, no allocation is made.
    """
    accrual_rows: dict[int, dict[str, Any]] = {}
    index: dict[tuple[str, str, Decimal], list[int]] = {}
    explicit_total = Decimal("0")

    for fact in sorted(accrual_facts, key=_fact_id):
        fact_id = _fact_id(fact)
        payload = _payload(fact)
        amount = max(Decimal("0"), _d(payload.get("amount")))
        explicit = min(amount, max(Decimal("0"), _d(payload.get("reversal_amount"))))
        remaining = max(Decimal("0"), amount - explicit)
        explicit_total += explicit
        row = {
            "fact_id": fact_id,
            "contract_key": _contract_key(payload),
            "supplier_key": _supplier_key(payload, invoice=False),
            "amount": amount,
            "explicit_reversal": explicit,
            "remaining": remaining,
        }
        accrual_rows[fact_id] = row
        if remaining <= 0 or not row["contract_key"] or not row["supplier_key"]:
            continue
        key = (row["contract_key"], row["supplier_key"], _money(amount))
        index.setdefault(key, []).append(fact_id)

    auto_by_accrual: dict[int, Decimal] = {}
    matches: list[dict[str, Any]] = []
    data_gaps: list[dict[str, Any]] = []

    for invoice in sorted(invoice_facts, key=_fact_id):
        payload = _payload(invoice)
        if not _is_external_input_invoice(payload, internal_codes):
            continue
        contract_key = _contract_key(payload)
        supplier_key = _supplier_key(payload, invoice=True)
        amount = _money(_invoice_cost_amount(payload))
        if not contract_key or not supplier_key or amount <= 0:
            continue

        key = (contract_key, supplier_key, amount)
        candidates = [
            fact_id
            for fact_id in index.get(key, [])
            if auto_by_accrual.get(fact_id, Decimal("0")) == 0
        ]
        if len(candidates) > 1:
            data_gaps.append(
                {
                    "code": "ACCRUAL_AUTO_MATCH_AMBIGUOUS",
                    "invoice_fact_id": _fact_id(invoice),
                    "candidate_accrual_fact_ids": candidates,
                    "contract_key": contract_key,
                    "supplier_key": supplier_key,
                    "amount": amount,
                }
            )
            continue
        if len(candidates) != 1:
            continue

        accrual_fact_id = candidates[0]
        matched_amount = accrual_rows[accrual_fact_id]["remaining"]
        auto_by_accrual[accrual_fact_id] = matched_amount
        matches.append(
            {
                "invoice_fact_id": _fact_id(invoice),
                "accrual_fact_id": accrual_fact_id,
                "invoice_accounting_cost": amount,
                "matched_amount": matched_amount,
                "match_basis": "contract_supplier_accounting_cost",
                "deterministic": True,
            }
        )

    auto_total = sum(auto_by_accrual.values(), Decimal("0"))
    return {
        "status": "DEGRADED" if data_gaps else "READY",
        "explicit_reversal_total": _money(explicit_total),
        "auto_reversal_total": _money(auto_total),
        "auto_reversal_by_accrual": auto_by_accrual,
        "matches": matches,
        "data_gaps": data_gaps,
        "read_only": True,
    }


__all__ = ["match_accrual_reversals"]
