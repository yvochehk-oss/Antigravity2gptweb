"""Versioned deterministic business-rule parameters.

This module is the code boundary for numeric/list business policy values used by
matching and risk scanning. The database owns the values; Python owns only the
rule identifiers and validation contract. Missing or ambiguous active rules
fail closed instead of silently restoring hard-coded business assumptions.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

BUSINESS_RULE_VERSION = "business_rules_v1"


def _load_value(db: Session, rule_set: str, code: str) -> Any:
    rows = db.execute(
        text(
            "SELECT value_json FROM business_rule_parameters "
            "WHERE rule_set = :rule_set AND rule_version = :rule_version "
            "AND code = :code AND enabled = true"
        ),
        {
            "rule_set": rule_set,
            "rule_version": BUSINESS_RULE_VERSION,
            "code": code,
        },
    ).scalars().all()
    if len(rows) != 1:
        raise ValueError(
            "business rule must have exactly one active value: "
            f"{rule_set}.{code}@{BUSINESS_RULE_VERSION}; found {len(rows)}"
        )
    raw = rows[0]
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"invalid JSON business rule: {rule_set}.{code}@{BUSINESS_RULE_VERSION}"
        ) from exc


def decimal_rule(db: Session, rule_set: str, code: str) -> Decimal:
    value = _load_value(db, rule_set, code)
    if isinstance(value, bool):
        raise ValueError(f"boolean is not a decimal business rule: {rule_set}.{code}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid decimal business rule: {rule_set}.{code}") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"business rule must be a finite non-negative decimal: {rule_set}.{code}")
    return result


def decimal_list_rule(db: Session, rule_set: str, code: str) -> tuple[Decimal, ...]:
    value = _load_value(db, rule_set, code)
    if not isinstance(value, list) or not value:
        raise ValueError(f"business rule must be a non-empty list: {rule_set}.{code}")
    out: list[Decimal] = []
    for item in value:
        if isinstance(item, bool):
            raise ValueError(f"boolean is not a decimal list item: {rule_set}.{code}")
        try:
            parsed = Decimal(str(item))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"invalid decimal list item: {rule_set}.{code}") from exc
        if not parsed.is_finite() or parsed < 0:
            raise ValueError(f"business rule list item must be finite and non-negative: {rule_set}.{code}")
        out.append(parsed)
    return tuple(out)


__all__ = [
    "BUSINESS_RULE_VERSION",
    "decimal_rule",
    "decimal_list_rule",
]
