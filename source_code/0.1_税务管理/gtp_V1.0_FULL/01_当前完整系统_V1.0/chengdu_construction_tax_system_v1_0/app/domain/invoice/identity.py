"""Versioned invoice business-identity rules.

Task 07a supports only the two v1.2 identity contracts. New rules must be added
as new explicit versions; changing normalization in-place would change business
identity and is therefore forbidden.
"""
from __future__ import annotations

import re
import unicodedata


class InvoiceIdentityError(ValueError):
    """Raised when an invoice cannot receive a deterministic identity key."""


def normalize_identity_component(value: str | None) -> str:
    """NFKC-normalize, uppercase, and remove Unicode whitespace only."""
    normalized = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return re.sub(r"\s+", "", normalized).upper()


def _required(value: str | None, field: str) -> str:
    normalized = normalize_identity_component(value)
    if not normalized:
        raise InvoiceIdentityError(f"{field} is required for invoice identity")
    return normalized


def build_invoice_identity_key(
    identity_version: str,
    *,
    invoice_number: str | None,
    invoice_code: str | None = None,
    seller_tax_identity: str | None = None,
) -> str:
    """Build the canonical physical-invoice identity key.

    DIGITAL_V1: normalized invoice number.
    LEGACY_V1: seller tax identity + invoice code + invoice number.
    """
    version = normalize_identity_component(identity_version)
    number = _required(invoice_number, "invoice_number")

    if version == "DIGITAL_V1":
        return f"DIGITAL_V1|{number}"
    if version == "LEGACY_V1":
        seller = _required(seller_tax_identity, "seller_tax_identity")
        code = _required(invoice_code, "invoice_code")
        return f"LEGACY_V1|{seller}|{code}|{number}"
    raise InvoiceIdentityError(f"unsupported invoice identity version: {identity_version!r}")


def build_invoice_business_identity_key(invoice_identity_key: str) -> str:
    """Namespace an invoice identity for the Fact supertype."""
    key = _required(invoice_identity_key, "invoice_identity_key")
    return f"INVOICE|{key}"
