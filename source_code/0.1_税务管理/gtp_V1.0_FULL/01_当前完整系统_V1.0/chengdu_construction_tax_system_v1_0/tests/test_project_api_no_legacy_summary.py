from pathlib import Path


def test_project_summary_route_does_not_read_legacy_finance_tables():
    source = Path("app/routers/api.py").read_text(encoding="utf-8")
    start = source.index('@router.get("/api/projects/{pid}")')
    end = source.index('@router.get("/api/projects/{pid}/matching")')
    block = source[start:end]
    assert "canonical_project_summary" in block
    assert "calc.project_summary(" not in block
    assert "from ..calc.project import project_summary" not in source
    assert "contract_total or 0" not in block
    assert "RealCost" not in block
    assert "Invoice" not in block
    assert "Progress" not in block
