"""Regression guard for deterministic risk scanner ownership."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_risk_scan_replaces_only_its_own_rule_rows():
    from app.calc.risk import RISK_SOURCE, RISK_RULE_VERSION
    source = (ROOT / "app" / "calc" / "risk.py").read_text(encoding="utf-8")

    assert RISK_SOURCE == "deterministic_risk_scan"
    assert RISK_RULE_VERSION == "business_rules_v1"
    assert "source = :source AND rule_version = :rule_version" in source
    assert "delete(RiskEvent).where(RiskEvent.project_id == pid)" not in source
    assert "INSERT INTO risk_events" in source
