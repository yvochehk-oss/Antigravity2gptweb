from __future__ import annotations

import pytest

from app.extractors import HybridExtractor
from app.granite_client import GraniteAuditClient


def test_ling_failure_keeps_deterministic_fields_and_marks_error():
    def failing_llm(_document_type, _context, _base):
        raise RuntimeError("local model unavailable")

    extractor = HybridExtractor(failing_llm)
    result = extractor.extract(
        "contract",
        "合同编号：HT-2026-001\n甲方：成都甲公司\n乙方：成都乙公司\n付款方式：按进度结算",
    )

    assert result["contract_no"] == "HT-2026-001"
    assert result["sources"]["contract_no"] == "rule:contract_no"
    assert result["_meta"]["semantic_model_used"] is False
    assert "RuntimeError" in result["_meta"]["semantic_error"]


def test_granite_amount_trigger_is_disabled_without_business_threshold(monkeypatch):
    monkeypatch.delenv("GRANITE_MIN_AMOUNT", raising=False)
    auditor = GraniteAuditClient()

    should_run, reasons = auditor.should_audit(
        "contract",
        {"amount_tax_included": "999999999"},
        {"errors": [], "warnings": []},
        "普通工程合同，无风险关键词。",
    )

    assert should_run is False
    assert reasons == []


def test_granite_amount_trigger_uses_explicit_business_threshold(monkeypatch):
    monkeypatch.setenv("GRANITE_MIN_AMOUNT", "500000")
    auditor = GraniteAuditClient()

    should_run, reasons = auditor.should_audit(
        "contract",
        {"amount_tax_included": "500000"},
        {"errors": [], "warnings": []},
        "普通工程合同，无风险关键词。",
    )

    assert should_run is True
    assert reasons == ["high_amount"]


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "not-a-number"])
def test_granite_rejects_invalid_business_threshold(monkeypatch, value):
    monkeypatch.setenv("GRANITE_MIN_AMOUNT", value)

    with pytest.raises(ValueError, match="finite non-negative"):
        GraniteAuditClient()
