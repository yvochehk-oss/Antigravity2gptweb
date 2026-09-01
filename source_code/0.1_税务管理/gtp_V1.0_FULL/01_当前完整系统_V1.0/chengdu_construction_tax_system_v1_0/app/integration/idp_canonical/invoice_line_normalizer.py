"""Deterministic Task25 InvoiceLine normalization and conflict detection."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any, Iterable

from .evidence_schemas import InvoiceLineEvidence


class InvoiceLineEvidenceError(ValueError):
    def __init__(
        self,
        code: str,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class InvoiceLineWritePlan:
    action: str
    fingerprint: str | None
    normalized_lines: tuple[dict[str, Any], ...]


def _decimal(
    value: Any,
    *,
    quantum: str,
) -> Decimal | None:
    if value is None:
        return None

    try:
        decimal_value = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )
    except (InvalidOperation, ValueError) as exc:
        raise InvoiceLineEvidenceError(
            "INVOICE_LINE_SCHEMA_INVALID",
            f"invalid decimal value {value!r}",
        ) from exc

    if not decimal_value.is_finite():
        raise InvoiceLineEvidenceError(
            "INVOICE_LINE_SCHEMA_INVALID",
            "invoice line decimal must be finite",
        )

    return decimal_value.quantize(
        Decimal(quantum),
        rounding=ROUND_HALF_UP,
    )


def _text(
    value: Any,
) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split()).strip()
    return normalized or None


def normalize_invoice_line(
    line: InvoiceLineEvidence,
) -> dict[str, Any]:
    return {
        "line_no": int(line.line_no),
        "item_name": _text(line.item_name),
        "category": _text(line.category),
        "quantity": _decimal(
            line.quantity,
            quantum="0.0001",
        ),
        "unit_price": _decimal(
            line.unit_price,
            quantum="0.000001",
        ),
        "net_amount": _decimal(
            line.net_amount,
            quantum="0.01",
        ),
        "vat_amount": _decimal(
            line.vat_amount,
            quantum="0.01",
        ),
        "tax_rate": _decimal(
            line.tax_rate,
            quantum="0.000001",
        ),
        "tax_classification_code": _text(
            line.tax_classification_code
        ),
    }


def normalize_invoice_lines(
    lines: Iterable[InvoiceLineEvidence],
) -> tuple[dict[str, Any], ...]:
    normalized = [
        normalize_invoice_line(line)
        for line in lines
    ]

    line_numbers = [
        int(line["line_no"])
        for line in normalized
    ]

    if len(set(line_numbers)) != len(line_numbers):
        raise InvoiceLineEvidenceError(
            "INVOICE_LINE_DUPLICATE_NUMBER",
            "invoice line_no must be unique within one Invoice Fact",
        )

    normalized.sort(
        key=lambda item: int(item["line_no"])
    )

    return tuple(normalized)


def _jsonable(
    value: Any,
) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")

    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0]),
            )
        }

    if isinstance(value, (tuple, list)):
        return [
            _jsonable(item)
            for item in value
        ]

    return value


def canonical_line_json(
    lines: tuple[dict[str, Any], ...],
) -> str:
    return json.dumps(
        _jsonable(lines),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def invoice_line_payload_fingerprint(
    lines: Iterable[InvoiceLineEvidence],
) -> str | None:
    normalized = normalize_invoice_lines(lines)

    if not normalized:
        return None

    return hashlib.sha256(
        canonical_line_json(normalized).encode("utf-8")
    ).hexdigest()


def normalize_existing_invoice_lines(
    rows: Iterable[Any],
) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []

    for row in rows:
        normalized.append(
            {
                "line_no": int(row.line_no),
                "item_name": _text(row.item_name),
                "category": _text(row.category),
                "quantity": _decimal(
                    row.quantity,
                    quantum="0.0001",
                ),
                "unit_price": _decimal(
                    row.unit_price,
                    quantum="0.000001",
                ),
                "net_amount": _decimal(
                    row.net_amount,
                    quantum="0.01",
                ),
                "vat_amount": _decimal(
                    row.vat_amount,
                    quantum="0.01",
                ),
                "tax_rate": _decimal(
                    row.tax_rate,
                    quantum="0.000001",
                ),
                "tax_classification_code": _text(
                    row.tax_classification_code
                ),
            }
        )

    normalized.sort(
        key=lambda item: int(item["line_no"])
    )

    numbers = [
        int(item["line_no"])
        for item in normalized
    ]

    if len(numbers) != len(set(numbers)):
        raise InvoiceLineEvidenceError(
            "CANONICAL_LINE_DUPLICATE_NUMBER",
            "stored Canonical InvoiceLine rows contain duplicate line_no",
        )

    return tuple(normalized)


def existing_line_payload_fingerprint(
    rows: Iterable[Any],
) -> str | None:
    normalized = normalize_existing_invoice_lines(rows)

    if not normalized:
        return None

    return hashlib.sha256(
        canonical_line_json(normalized).encode("utf-8")
    ).hexdigest()


def plan_invoice_line_write(
    *,
    existing_rows: Iterable[Any],
    incoming_lines: Iterable[InvoiceLineEvidence],
    established_fingerprint: str | None,
) -> InvoiceLineWritePlan:
    existing = normalize_existing_invoice_lines(
        existing_rows
    )
    incoming = normalize_invoice_lines(
        incoming_lines
    )

    existing_fp = (
        hashlib.sha256(
            canonical_line_json(existing).encode("utf-8")
        ).hexdigest()
        if existing
        else None
    )
    incoming_fp = (
        hashlib.sha256(
            canonical_line_json(incoming).encode("utf-8")
        ).hexdigest()
        if incoming
        else None
    )

    if established_fingerprint is not None:
        if existing_fp is None:
            raise InvoiceLineEvidenceError(
                "CANONICAL_LINE_EVIDENCE_MISSING",
                "binding records established InvoiceLine evidence "
                "but Canonical InvoiceLine rows are missing",
            )

        if existing_fp != established_fingerprint:
            raise InvoiceLineEvidenceError(
                "CANONICAL_LINE_EVIDENCE_DRIFT",
                "stored InvoiceLine rows differ from the established "
                "Task25 evidence fingerprint",
            )

        if (
            incoming_fp is not None
            and incoming_fp != established_fingerprint
        ):
            raise InvoiceLineEvidenceError(
                "INVOICE_LINE_EVIDENCE_CONFLICT",
                "incoming InvoiceLine evidence differs from the "
                "already established Task25 payload",
            )

    if not incoming:
        return InvoiceLineWritePlan(
            action="NOOP" if existing else "NONE",
            fingerprint=existing_fp or established_fingerprint,
            normalized_lines=(),
        )

    if existing:
        if existing_fp != incoming_fp:
            raise InvoiceLineEvidenceError(
                "INVOICE_LINE_EVIDENCE_CONFLICT",
                "Canonical InvoiceLine rows already exist with a "
                "different normalized payload",
            )

        return InvoiceLineWritePlan(
            action="NOOP",
            fingerprint=incoming_fp,
            normalized_lines=incoming,
        )

    return InvoiceLineWritePlan(
        action="CREATED",
        fingerprint=incoming_fp,
        normalized_lines=incoming,
    )
