"""Deterministic Invoice Fact validation rules.

Task 07a introduced the value-level validator retained below for backwards
compatibility. Task 09 adds an evidence-aware three-state decision layer:
missing evidence -> ``NEEDS_REVIEW``; deterministic contradiction ->
``INVALID``; only complete, internally consistent evidence -> ``VALID``.

Mutable statutory assumptions (for example fixed invoice number/code lengths or
tax rates) are deliberately not hard-coded here. Versioned tax-rate rules remain
an explicit caller input to the Task 07a helper.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

MONEY_TOLERANCE = Decimal("0.01")
RULESET_VERSION = "V3_INVOICE_VALIDATION_V1"
LEGACY_MIGRATION_IDENTITY_VERSION = "LEGACY_MIGRATION_V1"


def _d(value: Decimal | int | str | None) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class InvoiceLineInput:
    line_no: int
    net_amount: Decimal | None
    vat_amount: Decimal | None
    tax_rate: Decimal | None


@dataclass(frozen=True)
class InvoiceValidationInput:
    seller_party_id: int | None
    buyer_party_id: int | None
    invoice_number: str
    invoice_date: date | None
    gross_amount: Decimal | None
    net_amount: Decimal | None
    vat_amount: Decimal | None
    lines: tuple[InvoiceLineInput, ...]


@dataclass(frozen=True)
class InvoiceValidationResult:
    valid: bool
    violations: tuple[str, ...]


def validate_invoice_values(
    invoice: InvoiceValidationInput,
    *,
    allowed_tax_rates: Iterable[Decimal],
) -> InvoiceValidationResult:
    """Validate the Task 07a hard-integrity contract before Fact becomes VALID."""
    violations: list[str] = []
    rates = {_d(value) for value in allowed_tax_rates}
    rates.discard(None)

    if invoice.seller_party_id is None:
        violations.append("SELLER_PARTY_REQUIRED")
    if invoice.buyer_party_id is None:
        violations.append("BUYER_PARTY_REQUIRED")
    if (
        invoice.seller_party_id is not None
        and invoice.buyer_party_id is not None
        and invoice.seller_party_id == invoice.buyer_party_id
    ):
        violations.append("SELLER_EQUALS_BUYER")
    if not str(invoice.invoice_number or "").strip():
        violations.append("INVOICE_NUMBER_REQUIRED")
    if invoice.invoice_date is None:
        violations.append("INVOICE_DATE_REQUIRED")

    net = _d(invoice.net_amount)
    vat = _d(invoice.vat_amount)
    gross = _d(invoice.gross_amount)
    if net is None or vat is None or gross is None:
        violations.append("HEADER_AMOUNTS_REQUIRED")
    elif abs((net + vat) - gross) > MONEY_TOLERANCE:
        violations.append("HEADER_AMOUNT_UNBALANCED")

    if not invoice.lines:
        violations.append("INVOICE_LINES_REQUIRED")
    if not rates:
        violations.append("VERSIONED_TAX_RATE_RULES_REQUIRED")

    line_net = Decimal("0")
    line_vat = Decimal("0")
    seen_line_nos: set[int] = set()
    for line in invoice.lines:
        if line.line_no < 1:
            violations.append(f"LINE_{line.line_no}_NUMBER_INVALID")
        if line.line_no in seen_line_nos:
            violations.append(f"LINE_{line.line_no}_DUPLICATE")
        seen_line_nos.add(line.line_no)

        current_net = _d(line.net_amount)
        current_vat = _d(line.vat_amount)
        current_rate = _d(line.tax_rate)
        if current_net is None or current_vat is None:
            violations.append(f"LINE_{line.line_no}_AMOUNTS_REQUIRED")
        else:
            line_net += current_net
            line_vat += current_vat
        if current_rate is None:
            violations.append(f"LINE_{line.line_no}_TAX_RATE_REQUIRED")
        elif rates and current_rate not in rates:
            violations.append(f"LINE_{line.line_no}_TAX_RATE_NOT_ALLOWED")

    if net is not None and abs(line_net - net) > MONEY_TOLERANCE:
        violations.append("LINE_NET_SUM_MISMATCH")
    if vat is not None and abs(line_vat - vat) > MONEY_TOLERANCE:
        violations.append("LINE_VAT_SUM_MISMATCH")
    if gross is not None and abs((line_net + line_vat) - gross) > MONEY_TOLERANCE:
        violations.append("LINE_GROSS_SUM_MISMATCH")

    unique_violations = tuple(dict.fromkeys(violations))
    return InvoiceValidationResult(valid=not unique_violations, violations=unique_violations)


@dataclass(frozen=True)
class InvoiceEvidenceSnapshot:
    fact_id: int
    current_validation_status: str
    invoice_identity_key: str
    invoice_identity_version: str
    invoice_number: str
    invoice_code: str | None
    invoice_date: date | None
    invoice_status: str | None
    seller_party_id: int | None
    buyer_party_id: int | None
    gross_amount: Decimal | None
    net_amount: Decimal | None
    vat_amount: Decimal | None
    currency: str
    line_count: int
    incomplete_line_count: int
    line_net_sum: Decimal | None
    line_vat_sum: Decimal | None
    provenance_count: int
    document_provenance_count: int
    seller_tax_identifier_count: int
    reversal_relation_count: int
    void_relation_count: int


@dataclass(frozen=True)
class ValidationFinding:
    severity: str  # INVALID | REVIEW
    code: str
    message: str


@dataclass(frozen=True)
class ValidationDecision:
    fact_id: int
    ruleset_version: str
    desired_status: str
    findings: tuple[ValidationFinding, ...]

    @property
    def invalid_findings(self) -> tuple[ValidationFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "INVALID")

    @property
    def review_findings(self) -> tuple[ValidationFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "REVIEW")

    def as_dict(self) -> dict[str, object]:
        return {
            "fact_id": self.fact_id,
            "ruleset_version": self.ruleset_version,
            "desired_status": self.desired_status,
            "findings": [asdict(item) for item in self.findings],
        }


def _money(value: Decimal | None) -> Decimal | None:
    value = _d(value)
    return value.quantize(Decimal("0.01")) if value is not None else None


def _finding(severity: str, code: str, message: str) -> ValidationFinding:
    return ValidationFinding(severity=severity, code=code, message=message)


def evaluate_invoice_evidence(snapshot: InvoiceEvidenceSnapshot) -> ValidationDecision:
    """Return the deterministic Task 09 status supported by current evidence."""
    findings: list[ValidationFinding] = []

    if snapshot.seller_party_id is None:
        findings.append(_finding("REVIEW", "SELLER_PARTY_MISSING", "seller Party is not resolved"))
    if snapshot.buyer_party_id is None:
        findings.append(_finding("REVIEW", "BUYER_PARTY_MISSING", "buyer Party is not resolved"))
    if (
        snapshot.seller_party_id is not None
        and snapshot.buyer_party_id is not None
        and snapshot.seller_party_id == snapshot.buyer_party_id
    ):
        findings.append(
            _finding("INVALID", "SELLER_BUYER_SAME_PARTY", "seller and buyer resolve to the same Party")
        )

    if not snapshot.invoice_identity_key.strip():
        findings.append(_finding("REVIEW", "IDENTITY_KEY_MISSING", "invoice identity key is missing"))
    if not snapshot.invoice_identity_version.strip():
        findings.append(
            _finding("REVIEW", "IDENTITY_VERSION_MISSING", "invoice identity version is missing")
        )
    elif snapshot.invoice_identity_version == LEGACY_MIGRATION_IDENTITY_VERSION:
        findings.append(
            _finding(
                "REVIEW",
                "LEGACY_MIGRATION_IDENTITY",
                "Task 08 migration identity is not sufficient document identity evidence",
            )
        )

    if not snapshot.invoice_number.strip():
        findings.append(_finding("REVIEW", "INVOICE_NUMBER_MISSING", "invoice number is missing"))
    if snapshot.invoice_date is None:
        findings.append(_finding("REVIEW", "INVOICE_DATE_MISSING", "invoice date is missing"))
    if snapshot.invoice_status is None:
        findings.append(
            _finding("REVIEW", "INVOICE_DOCUMENT_STATUS_MISSING", "invoice document status is missing")
        )

    if snapshot.seller_tax_identifier_count < 1:
        findings.append(
            _finding(
                "REVIEW",
                "SELLER_TAX_IDENTITY_MISSING",
                "seller has no active TAX_REGISTRATION_ID Party identifier",
            )
        )

    gross = _money(snapshot.gross_amount)
    net = _money(snapshot.net_amount)
    vat = _money(snapshot.vat_amount)
    if gross is None or net is None or vat is None:
        findings.append(
            _finding("REVIEW", "HEADER_AMOUNT_INCOMPLETE", "gross/net/VAT header amounts are incomplete")
        )
    elif abs(gross - (net + vat)) > MONEY_TOLERANCE:
        findings.append(
            _finding(
                "INVALID",
                "HEADER_AMOUNT_EQUATION_MISMATCH",
                f"gross {gross} != net {net} + VAT {vat}",
            )
        )

    if snapshot.document_provenance_count < 1:
        findings.append(
            _finding(
                "REVIEW",
                "SOURCE_DOCUMENT_PROVENANCE_MISSING",
                "no Fact provenance row points to a source document",
            )
        )

    if snapshot.line_count < 1:
        findings.append(
            _finding("REVIEW", "INVOICE_LINES_MISSING", "no invoice line evidence is stored")
        )
    elif snapshot.incomplete_line_count:
        findings.append(
            _finding(
                "REVIEW",
                "INVOICE_LINES_INCOMPLETE",
                f"{snapshot.incomplete_line_count} invoice line(s) lack net or VAT amount",
            )
        )
    elif net is not None and vat is not None:
        line_net = _money(snapshot.line_net_sum)
        line_vat = _money(snapshot.line_vat_sum)
        if line_net is None or abs(line_net - net) > MONEY_TOLERANCE:
            findings.append(
                _finding(
                    "INVALID",
                    "LINE_NET_SUM_MISMATCH",
                    f"line net sum {line_net} != header net {net}",
                )
            )
        if line_vat is None or abs(line_vat - vat) > MONEY_TOLERANCE:
            findings.append(
                _finding(
                    "INVALID",
                    "LINE_VAT_SUM_MISMATCH",
                    f"line VAT sum {line_vat} != header VAT {vat}",
                )
            )

    if snapshot.invoice_status == "RED":
        for label, value in (("net", net), ("VAT", vat), ("gross", gross)):
            if value is not None and value > 0:
                findings.append(
                    _finding(
                        "INVALID",
                        "RED_AMOUNT_POSITIVE",
                        f"red invoice {label} amount must be non-positive, got {value}",
                    )
                )
        if snapshot.reversal_relation_count < 1:
            findings.append(
                _finding(
                    "INVALID",
                    "RED_REVERSAL_RELATION_MISSING",
                    "red invoice has no REVERSAL_OF relationship",
                )
            )

    if snapshot.invoice_status == "VOIDED" and snapshot.void_relation_count < 1:
        findings.append(
            _finding(
                "INVALID",
                "VOID_RELATION_MISSING",
                "voided invoice has no VOID_RELATION relationship",
            )
        )

    invalid = any(item.severity == "INVALID" for item in findings)
    review = any(item.severity == "REVIEW" for item in findings)
    desired = "INVALID" if invalid else ("NEEDS_REVIEW" if review else "VALID")
    return ValidationDecision(
        fact_id=snapshot.fact_id,
        ruleset_version=RULESET_VERSION,
        desired_status=desired,
        findings=tuple(findings),
    )


def evaluate_many_evidence(
    rows: Iterable[InvoiceEvidenceSnapshot],
) -> tuple[ValidationDecision, ...]:
    return tuple(evaluate_invoice_evidence(row) for row in rows)
