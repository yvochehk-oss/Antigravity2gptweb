"""Aggregation and SQL source-of-truth contract tests."""
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[3]

def test_analytics_views_use_real_tax_truth_tables_only():
    sql="\n".join(p.read_text() for p in (ROOT/"sql/views").glob("analytics_*.sql"))
    for required in ("progress","real_costs","budgets","invoices","cashflows"):
        assert required in sql
    for retired in ("revenue_invoices","cost_records","collection_records","work_progress","project_budgets","cost_invoices","bank_balances","planned_inflows","planned_outflows"):
        assert retired not in sql

def test_group_profit_margin_is_recomputed_from_totals():
    projects=[{"profit":1_400_000,"revenue":12_500_000},{"profit":800_000,"revenue":8_000_000}]
    assert sum(x["profit"] for x in projects)/sum(x["revenue"] for x in projects)==pytest.approx(2_200_000/20_500_000)

def test_group_collection_rate_is_weighted():
    rows=[(8_875_000,12_500_000),(5_200_000,8_000_000)]
    rate=sum(c for c,_ in rows)/sum(r for _,r in rows)
    assert rate==pytest.approx(14_075_000/20_500_000)
