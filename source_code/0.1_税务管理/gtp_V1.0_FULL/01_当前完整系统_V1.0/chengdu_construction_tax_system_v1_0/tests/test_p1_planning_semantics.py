"""P1 regression guards for planning profit semantics."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_planning_contract_renames_legacy_project_profit():
    from app.routers.planning import _truth_safe_planning_response

    payload = {
        "planning_basis": {"note": "legacy"},
        "scenarios": [{"scenario_id": "S1", "projected_management_profit": 123.0}],
        "recommended": {"scenario_id": "S1", "projected_management_profit": 123.0},
        "ai_recommendation": {"summary": "推荐 S1"},
    }
    result = _truth_safe_planning_response(payload)

    assert result["recommended"]["scenario_known_cost_margin"] == 123.0
    assert result["recommended"]["profit_scope"] == "known_cost_margin_not_project_eac"
    assert "不是项目最终利润" in result["recommended"]["profit_definition"]
    assert "projected_management_profit" not in result["recommended"]
    assert "projected_management_profit" not in result["scenarios"][0]
    assert "不得表述为项目最终利润或EAC利润" in result["ai_recommendation"]["fact_boundary"]
    assert "不是项目最终/EAC利润" in result["planning_basis"]["note"]


def test_planning_manager_ui_uses_truth_safe_metric_name():
    template = (ROOT / "app" / "templates" / "manager_planning.html").read_text(encoding="utf-8")
    assert "rec.projected_management_profit" not in template
    assert "rec.scenario_known_cost_margin" in template
    assert "已知成本情景余量" in template
    assert "不代表项目最终利润或 EAC 利润" in template
