"""Regression tests for request-level entity authorization validation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import DocumentMetadataPatch


@pytest.mark.parametrize("placeholder", ["A", "B", "C", "D", "甲", "乙", "丙", "丁"])
def test_document_counterparty_rejects_virtual_role_labels(placeholder):
    with pytest.raises(ValidationError, match="not counterparties"):
        DocumentMetadataPatch(counterparty_code=placeholder)


@pytest.mark.parametrize("counterparty", [None, "A01", "B10", "SUPPLIER-001"])
def test_document_counterparty_accepts_real_or_external_identifiers(counterparty):
    patch = DocumentMetadataPatch(counterparty_code=counterparty)
    assert patch.counterparty_code == counterparty
