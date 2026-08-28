"""Static regression guards for P0 truth semantics.

These tests intentionally require no database. Runtime/API tests cover behavior elsewhere;
these guards prevent the specific fabricated-value and collection-conversion patterns from
being reintroduced during refactors.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_executive_penetration_does_not_reintroduce_ratio_fabrication():
    source = (ROOT / "app" / "routers" / "executive_mobile.py").read_text(encoding="utf-8")

    assert 'amt * Decimal("1.2")' not in source
    assert 'amt * Decimal("1.09")' not in source
    assert '"contract": float(camt) if camt > 0 else None' in source
    assert '"nominal": None' in source


def test_executive_after_tax_profit_uses_the_ui_formula_not_real_profit_alias():
    source = (ROOT / "app" / "routers" / "executive_mobile.py").read_text(encoding="utf-8")

    assert "rec_revenue - system_external_real_cost - tax_paid_val" in source
    assert '_metric_value(metrics, "real_profit") if facts_available else None' not in source
    assert "management_profit_after_tax_unavailable" in source


def test_project_audit_tax_categories_are_a_stable_list():
    source = (ROOT / "app" / "legacy_routes.py").read_text(encoding="utf-8")

    assert 'coverage["tax_detail"] = sorted(tax_cats)' in source
    assert "dict(tax_cats)" not in source
