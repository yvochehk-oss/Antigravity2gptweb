"""Closed-loop completeness assertion for one Formal VAT ledger materialization.

The evaluator is deliberately pure: it compares the canonical rebuild snapshot
with the components actually materialized for the candidate ledger. Promotion
may proceed only when every source is represented exactly once, with the exact
amount, and no unexpected component exists.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any, Iterable

STATUS_COMPLETE = "COMPLETE"
STATUS_NO_ACTIVITY = "NO_ACTIVITY"
STATUS_INCOMPLETE = "INCOMPLETE"


def _value(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _expected_entries(snapshot: dict[str, Any]) -> tuple[list[tuple[str, int, Decimal]], list[str]]:
    entries: list[tuple[str, int, Decimal]] = []
    errors: list[str] = []

    opening = snapshot.get("opening") or {}
    opening_kind = opening.get("kind")
    try:
        if opening_kind == "PRIOR_STATUTORY_RESOURCE":
            entries.append(("OPENING:PRIOR_LEDGER", int(opening["ledger_id"]), _money(opening["amount"])))
        elif opening_kind == "OPENING_SEED":
            entries.append(("OPENING:SEED", int(opening["seed_id"]), _money(opening["amount"])))
        else:
            errors.append("OPENING_SOURCE_UNRESOLVED")
    except (KeyError, TypeError, ValueError, ArithmeticError):
        errors.append("OPENING_SOURCE_INVALID")

    for row in snapshot.get("output_events") or []:
        try:
            entries.append(("OUTPUT_VAT_EVENT", int(row["id"]), _money(row["amount"])))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            errors.append("OUTPUT_SOURCE_INVALID")
    for row in snapshot.get("input_claims") or []:
        try:
            entries.append(("INPUT_VAT_CLAIM", int(row["id"]), _money(row["amount"])))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            errors.append("INPUT_SOURCE_INVALID")
    for row in snapshot.get("tax_prepayments") or []:
        try:
            entries.append(("TAX_PREPAYMENT_FACT", int(row["fact_id"]), _money(row["amount"])))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            errors.append("PREPAYMENT_SOURCE_INVALID")
    return entries, errors


def _component_entry(row: Any) -> tuple[str, int, Decimal] | None:
    component_type = _value(row, "component_type")
    try:
        amount = _money(_value(row, "amount"))
        if component_type == "OPENING_INPUT_CREDIT":
            prior_id = _value(row, "prior_ledger_id")
            seed_id = _value(row, "opening_balance_seed_id")
            if prior_id is not None and seed_id is None:
                return ("OPENING:PRIOR_LEDGER", int(prior_id), amount)
            if seed_id is not None and prior_id is None:
                return ("OPENING:SEED", int(seed_id), amount)
            return None
        if component_type == "OUTPUT_VAT":
            source_id = _value(row, "output_vat_event_id")
            return None if source_id is None else ("OUTPUT_VAT_EVENT", int(source_id), amount)
        if component_type == "INPUT_VAT":
            source_id = _value(row, "input_vat_claim_id")
            return None if source_id is None else ("INPUT_VAT_CLAIM", int(source_id), amount)
        if component_type == "TAX_PREPAYMENT":
            source_id = _value(row, "tax_prepayment_fact_id")
            return None if source_id is None else ("TAX_PREPAYMENT_FACT", int(source_id), amount)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return None


def _source_key(entry: tuple[str, int, Decimal]) -> str:
    return f"{entry[0]}:{entry[1]}"


def evaluate_formal_vat_closed_loop(
    snapshot: dict[str, Any],
    components: Iterable[Any],
) -> dict[str, Any]:
    """Return deterministic completeness evidence for a candidate VAT ledger."""
    expected, errors = _expected_entries(snapshot)
    persisted: list[tuple[str, int, Decimal]] = []
    invalid_component_count = 0
    for row in components:
        entry = _component_entry(row)
        if entry is None:
            invalid_component_count += 1
        else:
            persisted.append(entry)

    expected_keys = [_source_key(item) for item in expected]
    persisted_keys = [_source_key(item) for item in persisted]
    expected_counts = Counter(expected_keys)
    persisted_counts = Counter(persisted_keys)

    duplicate_source_keys = sorted(key for key, count in expected_counts.items() if count != 1)
    duplicate_component_keys = sorted(key for key, count in persisted_counts.items() if count != 1)
    missing_source_keys = sorted(key for key in expected_counts if persisted_counts.get(key, 0) == 0)
    unexpected_component_keys = sorted(key for key in persisted_counts if expected_counts.get(key, 0) == 0)

    expected_amounts = {_source_key(item): item[2] for item in expected if expected_counts[_source_key(item)] == 1}
    persisted_amounts = {_source_key(item): item[2] for item in persisted if persisted_counts[_source_key(item)] == 1}
    amount_mismatches = [
        {
            "source_key": key,
            "expected": f"{expected_amounts[key]:.2f}",
            "persisted": f"{persisted_amounts[key]:.2f}",
        }
        for key in sorted(expected_amounts.keys() & persisted_amounts.keys())
        if expected_amounts[key] != persisted_amounts[key]
    ]

    incomplete = bool(
        errors
        or invalid_component_count
        or duplicate_source_keys
        or duplicate_component_keys
        or missing_source_keys
        or unexpected_component_keys
        or amount_mismatches
        or len(expected) != len(persisted)
    )
    transaction_source_count = sum(
        1 for item in expected if not item[0].startswith("OPENING:")
    )
    status = STATUS_INCOMPLETE if incomplete else (
        STATUS_NO_ACTIVITY if transaction_source_count == 0 else STATUS_COMPLETE
    )

    return {
        "status": status,
        "source_resolved": not errors,
        "expected_component_count": len(expected),
        "persisted_component_count": len(persisted),
        "transaction_source_count": transaction_source_count,
        "invalid_component_count": invalid_component_count,
        "source_errors": sorted(errors),
        "missing_source_keys": missing_source_keys,
        "unexpected_component_keys": unexpected_component_keys,
        "duplicate_source_keys": duplicate_source_keys,
        "duplicate_component_keys": duplicate_component_keys,
        "amount_mismatches": amount_mismatches,
    }


__all__ = [
    "STATUS_COMPLETE",
    "STATUS_INCOMPLETE",
    "STATUS_NO_ACTIVITY",
    "evaluate_formal_vat_closed_loop",
]
