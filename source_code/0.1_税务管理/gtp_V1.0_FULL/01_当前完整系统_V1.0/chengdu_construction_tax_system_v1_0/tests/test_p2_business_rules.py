"""P2 regression guards for versioned business-rule parameters."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path


def test_versioned_business_rule_baselines_are_loadable(seeded_app):
    from app.calc.business_rules import (
        BUSINESS_RULE_VERSION,
        decimal_list_rule,
        decimal_rule,
    )
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        assert BUSINESS_RULE_VERSION == "business_rules_v1"
        assert decimal_rule(db, "matching", "invoice_over_contract") == Decimal("1.05")
        assert decimal_rule(db, "matching", "paid_over_invoice") == Decimal("1.05")
        assert decimal_rule(db, "matching", "fulfilled_over_contract") == Decimal("1.10")
        assert set(decimal_list_rule(db, "risk", "equipment_allowed_invoice_rates")) == {
            Decimal("0"), Decimal("0.03"), Decimal("0.09"), Decimal("0.13")
        }
    finally:
        db.close()


def test_business_values_are_not_embedded_in_matching_or_risk_source():
    root = Path(__file__).resolve().parents[1] / "app" / "calc"
    matching = (root / "matching.py").read_text(encoding="utf-8")
    risk = (root / "risk.py").read_text(encoding="utf-8")

    assert "DEFAULT_RISK_THRESHOLDS" not in matching
    assert "1.05" not in matching
    assert "1.10" not in matching
    assert "(0.09, 0.13, 0.03, 0)" not in risk
    assert "equipment_allowed_invoice_rates" in risk
