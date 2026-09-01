from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.domain.invoice.validation import (
    ValidationDecision,
    ValidationFinding,
)
from app.integration.idp_canonical.evidence_schemas import (
    InvoiceEvidenceCompletionRequest,
    InvoiceLineEvidence,
    ReviewedTaxRateRules,
    SourceDocumentEvidence,
)
from app.integration.idp_canonical.evidence_service import (
    EvidenceCompletionError,
    apply_task09_decision,
)
from app.integration.idp_canonical.invoice_line_normalizer import (
    InvoiceLineEvidenceError,
    invoice_line_payload_fingerprint,
    plan_invoice_line_write,
)
from app.integration.idp_canonical.source_document_bridge import (
    SourceDocumentBindingError,
    merge_source_document_status,
)


def _line(
    *,
    line_no: int = 1,
    net: str = "100.00",
    vat: str = "13.00",
    rate: str = "0.13",
) -> InvoiceLineEvidence:
    return InvoiceLineEvidence(
        line_no=line_no,
        item_name="施工服务",
        category="SERVICE",
        quantity=Decimal("1"),
        unit_price=Decimal("100"),
        net_amount=Decimal(net),
        vat_amount=Decimal(vat),
        tax_rate=Decimal(rate),
    )


def _stored_line(
    *,
    line_no: int = 1,
    net: str = "100.00",
    vat: str = "13.00",
    rate: str = "0.13",
):
    return SimpleNamespace(
        line_no=line_no,
        item_name="施工服务",
        category="SERVICE",
        quantity=Decimal("1.0000"),
        unit_price=Decimal("100.000000"),
        net_amount=Decimal(net),
        vat_amount=Decimal(vat),
        tax_rate=Decimal(rate),
        tax_classification_code=None,
    )


def _decision(
    status: str,
) -> ValidationDecision:
    findings = (
        ()
        if status == "VALID"
        else (
            ValidationFinding(
                severity=(
                    "INVALID"
                    if status == "INVALID"
                    else "REVIEW"
                ),
                code="TEST",
                message="test",
            ),
        )
    )

    return ValidationDecision(
        fact_id=1,
        ruleset_version=(
            "V3_INVOICE_VALIDATION_V1"
        ),
        desired_status=status,
        findings=findings,
    )


def test_validated_document_requires_actor():
    with pytest.raises(
        ValidationError
    ):
        SourceDocumentEvidence(
            filename="invoice.pdf",
            validation_status="VALIDATED",
            validation_reason="reviewed",
        )


def test_validated_document_requires_reason():
    with pytest.raises(
        ValidationError
    ):
        SourceDocumentEvidence(
            filename="invoice.pdf",
            validation_status="VALIDATED",
            validated_by="reviewer",
        )


def test_validated_document_with_audit_is_valid():
    value = SourceDocumentEvidence(
        filename="invoice.pdf",
        validation_status="VALIDATED",
        validated_by="reviewer",
        validation_reason="document checked",
    )

    assert value.validation_status == "VALIDATED"


def test_reviewed_tax_rules_require_rates():
    with pytest.raises(
        ValidationError
    ):
        ReviewedTaxRateRules(
            rule_version="RULE_V1",
            reviewed_by="reviewer",
            allowed_tax_rates=[],
        )


def test_reviewed_tax_rules_reject_duplicate_rates():
    with pytest.raises(
        ValidationError
    ):
        ReviewedTaxRateRules(
            rule_version="RULE_V1",
            reviewed_by="reviewer",
            allowed_tax_rates=[
                Decimal("0.13"),
                Decimal("0.130000"),
            ],
        )


def test_line_fingerprint_is_order_independent():
    left = invoice_line_payload_fingerprint(
        [
            _line(line_no=2),
            _line(line_no=1),
        ]
    )
    right = invoice_line_payload_fingerprint(
        [
            _line(line_no=1),
            _line(line_no=2),
        ]
    )

    assert left == right


def test_line_fingerprint_uses_canonical_decimal_scales():
    left = invoice_line_payload_fingerprint(
        [
            InvoiceLineEvidence(
                line_no=1,
                quantity=Decimal("1"),
                unit_price=Decimal("100"),
                net_amount=Decimal("100"),
                vat_amount=Decimal("13"),
                tax_rate=Decimal("0.13"),
            )
        ]
    )

    right = invoice_line_payload_fingerprint(
        [
            InvoiceLineEvidence(
                line_no=1,
                quantity=Decimal("1.0000"),
                unit_price=Decimal(
                    "100.000000"
                ),
                net_amount=Decimal("100.00"),
                vat_amount=Decimal("13.00"),
                tax_rate=Decimal(
                    "0.130000"
                ),
            )
        ]
    )

    assert left == right


def test_duplicate_line_numbers_fail_closed():
    with pytest.raises(
        InvoiceLineEvidenceError
    ) as exc:
        invoice_line_payload_fingerprint(
            [
                _line(line_no=1),
                _line(line_no=1),
            ]
        )

    assert (
        exc.value.code
        == "INVOICE_LINE_DUPLICATE_NUMBER"
    )


def test_new_lines_plan_create():
    plan = plan_invoice_line_write(
        existing_rows=[],
        incoming_lines=[_line()],
        established_fingerprint=None,
    )

    assert plan.action == "CREATED"
    assert plan.fingerprint is not None


def test_same_existing_lines_plan_noop():
    fingerprint = (
        invoice_line_payload_fingerprint(
            [_line()]
        )
    )

    plan = plan_invoice_line_write(
        existing_rows=[
            _stored_line()
        ],
        incoming_lines=[
            _line()
        ],
        established_fingerprint=(
            fingerprint
        ),
    )

    assert plan.action == "NOOP"
    assert plan.fingerprint == fingerprint


def test_changed_line_payload_fails_closed():
    fingerprint = (
        invoice_line_payload_fingerprint(
            [_line()]
        )
    )

    with pytest.raises(
        InvoiceLineEvidenceError
    ) as exc:
        plan_invoice_line_write(
            existing_rows=[
                _stored_line()
            ],
            incoming_lines=[
                _line(
                    net="99.00",
                    vat="14.00",
                )
            ],
            established_fingerprint=(
                fingerprint
            ),
        )

    assert (
        exc.value.code
        == "INVOICE_LINE_EVIDENCE_CONFLICT"
    )


def test_established_fingerprint_requires_canonical_rows():
    fingerprint = (
        invoice_line_payload_fingerprint(
            [_line()]
        )
    )

    with pytest.raises(
        InvoiceLineEvidenceError
    ) as exc:
        plan_invoice_line_write(
            existing_rows=[],
            incoming_lines=[],
            established_fingerprint=(
                fingerprint
            ),
        )

    assert (
        exc.value.code
        == "CANONICAL_LINE_EVIDENCE_MISSING"
    )


def test_source_document_status_is_monotonic():
    assert (
        merge_source_document_status(
            "EXTRACTED",
            "VALIDATED",
        )
        == "VALIDATED"
    )

    assert (
        merge_source_document_status(
            "VALIDATED",
            "EXTRACTED",
        )
        == "VALIDATED"
    )


def test_failed_source_document_does_not_auto_recover():
    with pytest.raises(
        SourceDocumentBindingError
    ) as exc:
        merge_source_document_status(
            "FAILED",
            "VALIDATED",
        )

    assert (
        exc.value.code
        == "SOURCE_DOCUMENT_FAILED"
    )


def test_task09_promotes_needs_review_to_valid():
    fact = SimpleNamespace(
        id=1,
        is_current=True,
        validation_status="NEEDS_REVIEW",
    )

    changed = apply_task09_decision(
        fact,
        _decision("VALID"),
    )

    assert changed is True
    assert fact.validation_status == "VALID"


def test_task09_marks_deterministic_conflict_invalid():
    fact = SimpleNamespace(
        id=1,
        is_current=True,
        validation_status="NEEDS_REVIEW",
    )

    changed = apply_task09_decision(
        fact,
        _decision("INVALID"),
    )

    assert changed is True
    assert fact.validation_status == "INVALID"


def test_incomplete_evidence_remains_needs_review():
    fact = SimpleNamespace(
        id=1,
        is_current=True,
        validation_status="NEEDS_REVIEW",
    )

    changed = apply_task09_decision(
        fact,
        _decision("NEEDS_REVIEW"),
    )

    assert changed is False
    assert (
        fact.validation_status
        == "NEEDS_REVIEW"
    )


def test_invalid_fact_is_not_auto_rehabilitated():
    fact = SimpleNamespace(
        id=1,
        is_current=True,
        validation_status="INVALID",
    )

    changed = apply_task09_decision(
        fact,
        _decision("VALID"),
    )

    assert changed is False
    assert fact.validation_status == "INVALID"


def test_valid_fact_is_not_downgraded_by_missing_retry_evidence():
    fact = SimpleNamespace(
        id=1,
        is_current=True,
        validation_status="VALID",
    )

    changed = apply_task09_decision(
        fact,
        _decision("NEEDS_REVIEW"),
    )

    assert changed is False
    assert fact.validation_status == "VALID"


def test_valid_fact_new_contradiction_requires_reviewed_workflow():
    fact = SimpleNamespace(
        id=1,
        is_current=True,
        validation_status="VALID",
    )

    with pytest.raises(
        EvidenceCompletionError
    ) as exc:
        apply_task09_decision(
            fact,
            _decision("INVALID"),
        )

    assert (
        exc.value.code
        == "VALID_FACT_EVIDENCE_REGRESSION"
    )


def test_evidence_request_rejects_invalid_page_range():
    with pytest.raises(
        ValidationError
    ):
        InvoiceEvidenceCompletionRequest(
            source_system="IDP",
            source_document_id="doc",
            source_extraction_id="ext",
            document_sha256="a" * 64,
            document=SourceDocumentEvidence(
                filename="invoice.pdf",
            ),
            page_start=2,
            page_end=1,
        )
