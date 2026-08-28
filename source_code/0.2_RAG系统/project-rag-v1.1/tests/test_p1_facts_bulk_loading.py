"""Regression guards for Executive/Facts N+1 removal."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_facts_provider_preloads_canonical_view_once_per_session():
    source = (ROOT / "facts_provider" / "bulk_loader.py").read_text(encoding="utf-8")
    init = (ROOT / "facts_provider" / "__init__.py").read_text(encoding="utf-8")

    assert "SELECT * FROM analytics_project_full ORDER BY project_code" in source
    assert 'info.get("_canonical_facts_bulk")' in source
    assert "get_facts_bulk" in source
    assert "install_bulk_facts_loading()" in init
    assert "for code in project_codes" not in source
