from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict


TOLERANCE = Decimal("0.02")


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def validate_invoice(data: Dict[str, Any]) -> Dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    net = _decimal(data.get("amount_excluding_tax"))
    tax = _decimal(data.get("tax_amount"))
    total = _decimal(data.get("amount_including_tax"))

    if net is not None and tax is not None and total is not None:
        if abs(net + tax - total) > TOLERANCE:
            errors.append("invoice_amount_mismatch")

    for side in ("buyer", "seller"):
        party = data.get(side) or {}
        tax_id = party.get("credit_code") or party.get("tax_id")
        if tax_id and len(str(tax_id).strip()) != 18:
            warnings.append(f"{side}_tax_id_length")

    if not data.get("invoice_no"):
        errors.append("invoice_no_missing")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def validate_contract(data: Dict[str, Any]) -> Dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    gross = _decimal(data.get("amount_tax_included"))
    net = _decimal(data.get("amount_tax_excluded"))
    tax = _decimal(data.get("tax_amount"))
    if gross is not None and net is not None and tax is not None:
        if abs(net + tax - gross) > TOLERANCE:
            errors.append("contract_amount_mismatch")

    terms = data.get("payment_terms") or []
    percentages = [_decimal(item.get("percentage")) for item in terms if isinstance(item, dict)]
    percentages = [x for x in percentages if x is not None]
    if percentages and sum(percentages, Decimal("0")) > Decimal("1.0001"):
        errors.append("payment_percentage_exceeds_100_percent")

    if not data.get("contract_no"):
        warnings.append("contract_no_missing")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def decide_review(data: Dict[str, Any], validation: Dict[str, Any], threshold: float = 0.90) -> str:
    if not validation.get("ok", False):
        return "validation_failed"
    confidence = data.get("confidence") or {}
    if confidence and any(float(v) < threshold for v in confidence.values()):
        return "needs_review"
    if validation.get("warnings"):
        return "needs_review"
    return "approved"
