from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.services.phase4_canonical_facts import (
    infer_phase4_fact_type,
    validate_phase4_payload,
)
from app.services.phase4_extraction import _labelled_money, _labelled_percent

ROOT = Path(__file__).resolve().parents[1]


def test_phase4_document_types_are_mapped_to_one_canonical_ssot() -> None:
    assert infer_phase4_fact_type("progress_claim") == "progress"
    assert infer_phase4_fact_type("completion_certificate") == "progress"
    assert infer_phase4_fact_type("unbilled_cost") == "accrual"
    assert infer_phase4_fact_type("cit_adjustment") == "tax_adjustment"
    assert infer_phase4_fact_type("invoice") is None


def test_progress_validation_is_fail_closed() -> None:
    assert validate_phase4_payload("progress", {"completion_percent": "0.75"}) == []
    assert validate_phase4_payload("progress", {"estimated_total_cost": "5000000"}) == []
    errors = validate_phase4_payload("progress", {"completion_percent": "1.20"})
    assert "completion_percent must be between 0 and 1" in errors
    assert validate_phase4_payload("progress", {})


def test_accrual_and_tax_adjustment_validation_is_fail_closed() -> None:
    assert validate_phase4_payload("accrual", {"amount": "100"}) == []
    assert validate_phase4_payload("accrual", {"amount": "0"})
    assert validate_phase4_payload(
        "tax_adjustment",
        {"amount": "100", "direction": "ADD"},
    ) == []
    assert validate_phase4_payload(
        "tax_adjustment",
        {"amount": "100", "direction": "DEDUCT"},
    ) == []
    assert validate_phase4_payload(
        "tax_adjustment",
        {"amount": "100", "direction": "UNKNOWN"},
    )


def test_labelled_progress_and_money_extraction_is_conservative() -> None:
    completion, raw = _labelled_percent("累计完工进度：62.5%", ("累计完工进度",))
    assert completion == Decimal("0.625")
    assert "62.5%" in raw
    amount, raw = _labelled_money("预计总成本：人民币 1.25亿元", ("预计总成本",))
    assert amount == Decimal("125000000.00")
    assert "1.25亿元" in raw
    missing, raw = _labelled_money("这里没有明确金额标签 999", ("暂估成本",))
    assert missing is None
    assert raw == ""


def test_rag_extract_tax_route_is_runtime_retired_with_410() -> None:
    source = (ROOT / "app" / "routers" / "phase3_retirement.py").read_text(encoding="utf-8")
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "/api/v1/extract-tax" in source
    assert "status_code=410" in source
    assert "install_phase3_retirement(app)" in main


def test_phase4_writer_only_persists_into_shared_canonical_tables() -> None:
    source = (ROOT / "app" / "services" / "phase4_canonical_facts.py").read_text(encoding="utf-8")
    assert "INSERT INTO canonical_facts" in source
    assert "canonical_fact_outbox" in source
    assert "fact_version" in source
    assert "status = \"accepted\" if not errors else \"needs_review\"" in source
    for legacy in ("INSERT INTO facts", "INSERT INTO invoice_facts", "INSERT INTO contract_facts", "INSERT INTO payment_facts"):
        assert legacy not in source


def test_phase4_rag_router_is_composed_and_rag_is_only_writer() -> None:
    router = (ROOT / "app" / "routers" / "phase4_canonical.py").read_text(encoding="utf-8")
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "/api/v1/canonical-facts" in router
    assert "/phase4/promote" in router
    assert '"writer": "RAG"' in router
    assert "app.include_router(phase4_canonical_router)" in main
