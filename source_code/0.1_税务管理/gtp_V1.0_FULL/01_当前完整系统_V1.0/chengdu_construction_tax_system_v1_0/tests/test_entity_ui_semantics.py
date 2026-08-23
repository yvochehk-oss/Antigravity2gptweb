"""UI and prompt regression checks for canonical real-entity semantics."""
from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_CODES = {
    *(f"A{i:02d}" for i in range(1, 12)),
    *(f"B{i:02d}" for i in range(1, 11)),
    "C01", "C02", "D01", "D02", "D03",
}
VIRTUAL_ENTITY_CODES = {"A", "B", "C", "D", "甲", "乙", "丙", "丁"}


def _client(app) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/login", data={"username": "admin", "password": "TestPass12345!"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return client


def test_manage_and_dashboard_use_canonical_entity_codes(seeded_app):
    from app.calc import rebuild_tax_ledger
    from app.db import SessionLocal
    from app.main import app

    period = datetime.now(timezone.utc).strftime("%Y-%m")
    db = SessionLocal()
    try:
        rows = rebuild_tax_ledger(db, period)
        assert {row.entity_code for row in rows} == CANONICAL_CODES - {"A04"}
    finally:
        db.close()

    client = _client(app)
    manage = client.get("/manage")
    assert manage.status_code == 200
    assert "四川屹明汇建设工程有限公司重庆分公司" in manage.text
    assert 'value="A04"' in manage.text
    assert not any(
        f'value="{code}"' in manage.text for code in VIRTUAL_ENTITY_CODES
    )

    dashboard = client.get("/manager/dashboard")
    assert dashboard.status_code == 200
    assert "实际法人税务汇总" in dashboard.text
    assert "法人代码 A01" in dashboard.text
    assert "A01 · 中镌（湖北）建筑有限公司" in dashboard.text
    assert "分支机构代码 A04 · 四川屹明汇建设工程有限公司重庆分公司" in dashboard.text
    assert "税务归集至 A03" in dashboard.text
    assert "25 家独立法人 · 1 家分公司" in dashboard.text
    assert not any(
        re.search(rf"(?:法人代码|分支机构代码) {re.escape(code)}(?:\s|·|$)", dashboard.text)
        for code in VIRTUAL_ENTITY_CODES
    )
    assert 'for ec in ["A","B","C","D"]' not in (
        ROOT / "app/templates/manager_dashboard.html"
    ).read_text(encoding="utf-8")
    assert "ABCD 法人" not in dashboard.text


def test_prompt_and_csv_samples_reject_virtual_entity_semantics():
    prompt = (ROOT / "app/ai/prompt.py").read_text(encoding="utf-8")
    assert "business_role" in prompt
    assert "ExternalParty" in prompt
    assert "unknown external party" in prompt
    assert "不是法人代码" in prompt
    assert not any(marker in prompt for marker in ("甲（", "乙（", "丙（", "丁（"))

    for filename in ("invoices_template.csv", "cashflows_template.csv"):
        with (ROOT / "data/templates" / filename).open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows
        assert all(row["entity_code"] in CANONICAL_CODES for row in rows)
        assert not any(row["entity_code"] in VIRTUAL_ENTITY_CODES for row in rows)
