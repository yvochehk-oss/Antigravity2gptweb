"""Regression guards for collection balance semantics."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_unpaid_is_non_negative_and_advance_is_separate():
    profit = (ROOT / "sql" / "views" / "analytics_project_profit.sql").read_text(encoding="utf-8")
    full = (ROOT / "sql" / "views" / "analytics_project_full.sql").read_text(encoding="utf-8")

    assert "GREATEST(pr.recognized_revenue-pr.collected_amount,0)" in profit
    assert "GREATEST(pr.collected_amount-pr.recognized_revenue,0)" in profit
    assert "advance_collection_amount" in profit
    assert "advance_collection_amount" in full
