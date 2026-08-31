"""Deterministic Task24 normalization rules.

No LLM decision is permitted in this module.
Semantic rule changes must be introduced as a new explicit version.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
import unicodedata
from typing import Any, Mapping

from pydantic import BaseModel

from .schemas import CanonicalIngestRequest


CANONICAL_INTAKE_VERSION = "IDP_CANONICAL_INTAKE_V1"
INVOICE_TYPE_MAPPING_VERSION = "IDP_INVOICE_TYPE_V1"
CONTRACT_IDENTITY_VERSION = "CONTRACT_PARTIES_V1"

MONEY_TOLERANCE = Decimal("0.01")


class NormalizationError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class NormalizedInvoiceType:
    original_value: str | None
    medium: str
    category: str
    mapping_version: str
    recognized: bool
    rule_key: str | None = None


def normalize_identity_component(value: Any) -> str:
    text = unicodedata.normalize(
        "NFKC",
        "" if value is None else str(value),
    )
    return re.sub(r"\s+", "", text).upper()


def normalize_label(value: Any) -> str:
    return normalize_identity_component(value)


def normalize_currency(value: Any) -> str:
    currency = normalize_identity_component(value or "CNY")
    if len(currency) != 3 or not currency.isalpha():
        raise NormalizationError(
            "INVALID_CURRENCY",
            f"currency must be a three-letter code, got {value!r}",
        )
    return currency


def money_value(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, ValueError) as exc:
        raise NormalizationError(
            "INVALID_AMOUNT",
            f"invalid monetary value: {value!r}",
        ) from exc


def money_text(value: Any) -> str | None:
    normalized = money_value(value)
    if normalized is None:
        return None
    return format(normalized, ".2f")


def ensure_invoice_amount_equation(
    *,
    gross: Decimal | None,
    net: Decimal | None,
    vat: Decimal | None,
) -> None:
    if gross is None or net is None or vat is None:
        return
    if abs(gross - (net + vat)) > MONEY_TOLERANCE:
        raise NormalizationError(
            "AMOUNT_INCONSISTENT",
            f"gross {gross} != net {net} + VAT {vat}",
        )


def ensure_contract_amount(value: Decimal | None) -> None:
    if value is not None and value < 0:
        raise NormalizationError(
            "CONTRACT_AMOUNT_NEGATIVE",
            f"contract amount must be non-negative, got {value}",
        )


_INVOICE_TYPE_MAP: dict[str, tuple[str, str]] = {
    normalize_label("增值税电子专用发票"): ("DIGITAL", "SPECIAL"),
    normalize_label("电子发票(增值税专用发票)"): ("DIGITAL", "SPECIAL"),
    normalize_label("数电票(增值税专用发票)"): ("DIGITAL", "SPECIAL"),
    normalize_label("全面数字化的电子发票(增值税专用发票)"): (
        "DIGITAL",
        "SPECIAL",
    ),
    normalize_label("增值税电子普通发票"): ("DIGITAL", "ORDINARY"),
    normalize_label("电子发票(普通发票)"): ("DIGITAL", "ORDINARY"),
    normalize_label("数电票(普通发票)"): ("DIGITAL", "ORDINARY"),
    normalize_label("全面数字化的电子发票(普通发票)"): (
        "DIGITAL",
        "ORDINARY",
    ),
    normalize_label("电子发票"): ("DIGITAL", "OTHER"),
    normalize_label("数电票"): ("DIGITAL", "OTHER"),
    normalize_label("增值税专用发票"): ("PAPER", "SPECIAL"),
    normalize_label("增值税普通发票"): ("PAPER", "ORDINARY"),
    normalize_label("机动车销售统一发票"): ("PAPER", "OTHER"),
}


def normalize_invoice_type(value: str | None) -> NormalizedInvoiceType:
    key = normalize_label(value)
    if key in _INVOICE_TYPE_MAP:
        medium, category = _INVOICE_TYPE_MAP[key]
        return NormalizedInvoiceType(
            original_value=value,
            medium=medium,
            category=category,
            mapping_version=INVOICE_TYPE_MAPPING_VERSION,
            recognized=True,
            rule_key=key,
        )

    return NormalizedInvoiceType(
        original_value=value,
        medium="OTHER",
        category="OTHER",
        mapping_version=INVOICE_TYPE_MAPPING_VERSION,
        recognized=False,
        rule_key=None,
    )


def build_contract_business_identity_key(
    contract_number: str | None,
    party_codes: tuple[str, str] | list[str],
) -> str:
    number = normalize_identity_component(contract_number)
    if not number:
        raise NormalizationError(
            "BUSINESS_IDENTITY_INCOMPLETE",
            "contract number is required for Task24 contract identity",
        )

    normalized_parties = sorted(
        normalize_identity_component(value)
        for value in party_codes
        if normalize_identity_component(value)
    )

    if len(normalized_parties) != 2:
        raise NormalizationError(
            "BUSINESS_IDENTITY_INCOMPLETE",
            "exactly two resolved contract parties are required",
        )

    if normalized_parties[0] == normalized_parties[1]:
        raise NormalizationError(
            "PARTY_IDENTIFIER_CONFLICT",
            "contract participants resolve to the same Party code",
        )

    source = "|".join(
        [
            CONTRACT_IDENTITY_VERSION,
            number,
            normalized_parties[0],
            normalized_parties[1],
        ]
    )
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()

    return f"CONTRACT|{CONTRACT_IDENTITY_VERSION}|{digest}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="python"))

    if isinstance(value, Decimal):
        return format(value, "f")

    if isinstance(value, (date, datetime)):
        return value.isoformat()

    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0]),
            )
        }

    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]

    if isinstance(value, list):
        return [_jsonable(item) for item in value]

    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint_payload(value: Any) -> str:
    return hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def source_payload_fingerprint(request: CanonicalIngestRequest) -> str:
    return fingerprint_payload(
        request.model_dump(
            mode="python",
            exclude_none=False,
        )
    )


def canonical_payload_fingerprint(payload: Mapping[str, Any]) -> str:
    return fingerprint_payload(payload)


def average_confidence(*sources: Mapping[str, float]) -> Decimal | None:
    values: list[Decimal] = []

    for source in sources:
        for value in source.values():
            try:
                item = Decimal(str(value))
            except (InvalidOperation, ValueError):
                continue
            if Decimal("0") <= item <= Decimal("1"):
                values.append(item)

    if not values:
        return None

    result = sum(values, Decimal("0")) / Decimal(len(values))
    return result.quantize(Decimal("0.00001"))
