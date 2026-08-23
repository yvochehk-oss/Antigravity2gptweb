"""Canonical 26 system-internal entity codes shared by Tax and RAG.

Only numbered A/B/C/D codes identify system-internal units. External
counterparties live in ``external_parties`` and may participate in project tax
planning, but they are not part of internal consolidated profit.
"""
from __future__ import annotations
import re

CANONICAL_ENTITY_CODES = frozenset(
    {f"A{i:02d}" for i in range(1, 12)}
    | {f"B{i:02d}" for i in range(1, 11)}
    | {f"C{i:02d}" for i in range(1, 3)}
    | {f"D{i:02d}" for i in range(1, 4)}
)
CANONICAL_ENTITY_CODE_RE = re.compile(
    r"^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C0[12]|D0[1-3])$"
)
BUSINESS_ROLE_CODES = frozenset({"A", "B", "C", "D"})
VIRTUAL_ENTITY_CODES = frozenset({"A", "B", "C", "D", "甲", "乙", "丙", "丁"})
CANONICAL_ENTITY_RANGE_TEXT = "A01-A11, B01-B10, C01-C02, D01-D03"


def normalize_entity_code(value: str | None) -> str | None:
    if value is None:
        return None
    code = str(value).strip().upper()
    return code or None


def is_canonical_entity_code(value: str | None) -> bool:
    code = normalize_entity_code(value)
    return bool(code and code in CANONICAL_ENTITY_CODES and CANONICAL_ENTITY_CODE_RE.fullmatch(code))


def validate_entity_code(value: str | None) -> str | None:
    code = normalize_entity_code(value)
    if code is None:
        return None
    if not is_canonical_entity_code(code):
        raise ValueError(f"invalid entity_code {value!r}; expected {CANONICAL_ENTITY_RANGE_TEXT}")
    return code
