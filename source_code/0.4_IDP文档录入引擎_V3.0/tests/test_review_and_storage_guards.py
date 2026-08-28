from __future__ import annotations

from pathlib import Path

import pytest

from app.persistence_service import validate_stored_path
from app.repository import ReviewDataValidationError, validate_review_data


def test_review_data_rejects_unknown_fields():
    with pytest.raises(ReviewDataValidationError, match="schema_invalid"):
        validate_review_data(
            "invoice",
            {"invoice_no": "INV-001", "unexpected": "must not enter business tables"},
        )


def test_review_data_rejects_unsupported_type_and_invalid_number():
    with pytest.raises(ReviewDataValidationError, match="unsupported_document_type"):
        validate_review_data("receipt", {})

    with pytest.raises(ReviewDataValidationError, match="business_invalid"):
        validate_review_data(
            "invoice",
            {"invoice_no": "INV-001", "amount_including_tax": "-1"},
        )


def test_review_data_normalizes_valid_invoice():
    normalized = validate_review_data(
        "invoice",
        {"invoice_no": "INV-001", "tax_rate": "0.09", "_meta": {"internal": True}},
    )
    assert normalized["invoice_no"] == "INV-001"
    assert normalized["tax_rate"] == "0.09"
    assert "_meta" not in normalized


def test_stored_path_must_remain_under_storage_root(tmp_path: Path):
    root = tmp_path / "storage"
    root.mkdir()
    inside = root / "document.pdf"
    inside.write_bytes(b"test")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"test")

    assert validate_stored_path({"file_path": str(inside)}, root) == inside
    with pytest.raises(FileNotFoundError, match="outside_storage_root"):
        validate_stored_path({"file_path": str(outside)}, root)
