"""Regression guards for planning penetration values that must stay factual."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_planning_penetration_does_not_fabricate_contract_or_nominal_amounts():
    source = (ROOT / "app" / "planning" / "service.py").read_text(encoding="utf-8")

    assert 'amt * D("1.2")' not in source
    assert 'amt * D("1.09")' not in source
    assert '"contract": float(camt) if camt > 0 else None' in source
    assert '"nominal": None' in source
    assert "不按已开票金额比例反推合同额" in source
    assert "不按税率反推" in source
