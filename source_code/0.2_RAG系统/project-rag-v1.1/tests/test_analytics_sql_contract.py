"""Static contract checks for the deterministic analytics views."""

from pathlib import Path


VIEW_DIR = Path(__file__).parents[1] / "sql" / "views"


def _sql(name: str) -> str:
    return (VIEW_DIR / name).read_text(encoding="utf-8")


def test_project_full_exposes_source_completeness_and_no_default_health_score():
    sql = _sql("analytics_project_full.sql")

    assert "AS facts_available" in sql
    assert "analytics_project_full.facts_available" in sql
    assert "ELSE NULL" in sql
    assert "COALESCE(eac.cpi, 1.0)" not in sql


def test_eac_does_not_turn_missing_budget_or_progress_into_a_real_forecast():
    sql = _sql("analytics_eac.sql")

    assert "SUM(b.amount) AS total_budget_cost" in sql
    assert "total_budget_cost IS NOT NULL" in sql
    assert "ELSE 1.0" not in sql
    assert "ELSE NULL" in sql


def test_tax_burden_is_null_when_no_confirmed_revenue_exists():
    sql = _sql("analytics_tax.sql")

    assert "r.taxable_revenue IS NOT NULL" in sql
    assert "ELSE NULL" in sql
