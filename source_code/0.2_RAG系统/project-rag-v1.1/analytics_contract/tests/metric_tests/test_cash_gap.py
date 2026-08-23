"""cash_gap_30d must remain unavailable until deterministic planning data exists."""
from pathlib import Path

def test_cash_gap_contract_is_explicitly_unavailable():
    sql=(Path(__file__).resolve().parents[3]/"sql/views/analytics_cashflow.sql").read_text()
    assert "NULL::numeric AS cash_gap_30d" in sql
    assert "planned_inflows" not in sql and "planned_outflows" not in sql and "bank_balances" not in sql
