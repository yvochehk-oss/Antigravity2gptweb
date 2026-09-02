from __future__ import annotations

from decimal import Decimal

import pytest

from app.db import SessionLocal
from app.services.canonical_project_summary import canonical_project_summary


@pytest.mark.integration
def test_project_15_canonical_summary_values():
    db = SessionLocal()
    try:
        summary = canonical_project_summary(db, 15)
        assert Decimal(str(summary["contract_total"])) == Decimal("1450000000.00")
        assert Decimal(str(summary["real_cost"])).quantize(Decimal("0.01")) == Decimal("208482217.42")
        assert summary["legacy_tables_used"] is False
        assert summary["source_of_truth"] == "analytics_canonical_facts_current"
    finally:
        db.close()
