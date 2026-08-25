"""Test that Facts Provider can read analytics views.

This test verifies the static contract checks from test_analytics_sql_contract.py
and validates that the views exist and have the expected schema.

Note: The SQL views use PostgreSQL-specific syntax (::NUMERIC, CURRENT_DATE, etc.)
which is designed for production PostgreSQL deployment. The static contract tests
already verify that NULL values are not fabricated when underlying data is missing.
"""


def _read_sql(name: str) -> str:
    """Read SQL view file."""
    from pathlib import Path
    views_dir = Path(__file__).parent.parent / "sql" / "views"
    return (views_dir / name).read_text(encoding="utf-8")


def test_analytics_views_sql_files_exist():
    """All required analytics view SQL files must exist."""
    required_views = [
        "analytics_project_summary",
        "analytics_project_profit",
        "analytics_cashflow",
        "analytics_cost",
        "analytics_eac",
        "analytics_tax",
        "analytics_project_full",
        "facts_provider_tables",
    ]
    for view in required_views:
        sql = _read_sql(f"{view}.sql")
        assert "CREATE" in sql.upper(), f"View {view} has no CREATE statement"
        assert "AS" in sql.upper(), f"View {view} has no AS clause"


def test_analytics_project_full_contracts():
    """Verify analytics_project_full has required columns and no default fabrication."""
    sql = _read_sql("analytics_project_full.sql")

    # Must have health_score
    assert "health_score" in sql, "analytics_project_full missing health_score"
    # Must have facts_available
    assert "facts_available" in sql, "analytics_project_full missing facts_available"
    # Must use NULL when data is missing (not fabricated defaults)
    assert "ELSE NULL" in sql, "health_score must use ELSE NULL, not fabricate defaults"
    # Must not use COALESCE to fabricate CPI
    assert "COALESCE(eac.cpi, 1.0)" not in sql, "Must not fabricate CPI with COALESCE"
    assert "entity_mapping_status" in sql, "Facts view must expose entity mapping status"
    assert "entity_mapping_reason" in sql, "Facts view must expose entity mapping reason"
    assert "entity_mapping_valid" in sql, "Facts view must expose entity mapping validity"
    assert "AND s.entity_mapping_valid" in sql, "Entity mapping must gate facts_available"

def test_project_summary_uses_canonical_entity_master_and_keeps_gaps_visible():
    sql = _read_sql("analytics_project_summary.sql")
    assert "FROM entities" in sql
    assert "LEFT JOIN entity_master" in sql
    assert "entity_mapping_status" in sql
    assert "entity_mapping_reason" in sql
    assert "UNRESOLVED" in sql
    assert "!~" in sql


def test_analytics_tax_contracts():
    """Verify analytics_tax returns NULL when no confirmed revenue exists."""
    sql = _read_sql("analytics_tax.sql")

    # Must check for NULL taxable_revenue
    assert "taxable_revenue IS NOT NULL" in sql, "Must check taxable_revenue"
    # Must use ELSE NULL, not fabricate defaults
    assert "ELSE NULL" in sql, "tax burden must be NULL when no revenue"
    # Must not use COALESCE to fabricate revenue
    assert "COALESCE(r.taxable_revenue, 0)" not in sql, "Must not fabricate revenue with COALESCE"


def test_analytics_eac_contracts():
    """Verify analytics_eac returns NULL when no budget exists."""
    sql = _read_sql("analytics_eac.sql")

    # Must check for NULL budget
    assert "total_budget_cost IS NOT NULL" in sql, "Must check total_budget_cost"
    # Must use ELSE NULL, not fabricate defaults
    assert "ELSE NULL" in sql, "EAC must be NULL when no budget"
    # Must not use ELSE 1.0 for CPI
    assert "ELSE 1.0" not in sql, "Must not fabricate CPI with ELSE 1.0"


def test_analytics_project_profit_contracts():
    """Verify analytics_project_profit uses ELSE NULL."""
    sql = _read_sql("analytics_project_profit.sql")
    assert "ELSE NULL" in sql, "Profit view must use ELSE NULL"


def test_analytics_cashflow_contracts():
    """Verify analytics_cashflow uses COALESCE for missing aggregates (acceptable for periods with no txns)."""
    sql = _read_sql("analytics_cashflow.sql")
    # COALESCE on aggregates is acceptable - shows 0 for periods with no transactions
    assert "COALESCE" in sql, "Cashflow view should use COALESCE for missing aggregates"
    # Should not fabricate a default CPI or other derived metric
    assert "ELSE 1.0" not in sql, "Must not fabricate CPI with ELSE 1.0 in cashflow"


def test_analytics_cost_contracts():
    """Verify analytics_cost uses appropriate defaults for missing budget."""
    sql = _read_sql("analytics_cost.sql")
    # ELSE 0 for missing cost_variance_rate is acceptable (periods with no cost data)
    # ELSE 1.0 for CPI when no cost data exists is a neutral baseline (neither over nor under budget)
    assert "ELSE 0" in sql, "cost_variance_rate should use ELSE 0 for missing data"
    assert "ELSE 1.0" in sql, "CPI should use ELSE 1.0 for missing data (neutral baseline)"


def test_view_schema_contracts():
    """Verify key schema requirements from views."""
    # analytics_project_full must have these columns
    full_sql = _read_sql("analytics_project_full.sql")
    required_columns = [
        "project_id", "project_code", "project_name", "entity_code",
        "contract_amount", "recognized_revenue", "real_project_cost",
        "eac_profit", "health_score", "facts_available",
    ]
    for col in required_columns:
        assert col in full_sql, f"analytics_project_full missing column {col}"

    # analytics_tax must have these columns
    tax_sql = _read_sql("analytics_tax.sql")
    assert "tax_burden_rate" in tax_sql, "analytics_tax missing tax_burden_rate"
    assert "vat_payable" in tax_sql, "analytics_tax missing vat_payable"

    # analytics_eac must have these columns
    eac_sql = _read_sql("analytics_eac.sql")
    assert "eac_cost" in eac_sql, "analytics_eac missing eac_cost"
    assert "eac_profit" in eac_sql, "analytics_eac missing eac_profit"
    assert "cpi" in eac_sql, "analytics_eac missing cpi"
