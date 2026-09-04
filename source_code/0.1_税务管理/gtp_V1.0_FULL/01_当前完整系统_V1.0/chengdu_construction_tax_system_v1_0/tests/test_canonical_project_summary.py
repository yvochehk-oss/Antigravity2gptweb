from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.services.canonical_project_summary import resolve_project_transaction_price
from app.db import SessionLocal
from app.models import Project


def _fake_db(codes):
    class FakeDB:
        def execute(self, *_args, **_kwargs):
            class Result:
                def scalars(self):
                    class Scalars:
                        def all(self):
                            return codes
                    return Scalars()
            return Result()
    return FakeDB()


def test_resolver_prefers_known_main_contract(monkeypatch):
    facts = [
        {
            "fact_id": 1,
            "fact_version": 1,
            "business_key": "contract:CDTF-MAIN-2026-01",
            "payload": {
                "contract_no": "CDTF-MAIN-2026-01",
                "total_amount": "1450000000.00",
                "party_a_entity_code": "E0",
                "party_b_entity_code": "A08",
            },
        },
        {
            "fact_id": 2,
            "fact_version": 1,
            "business_key": "contract:internal",
            "payload": {
                "contract_no": "INTERNAL-01",
                "total_amount": "3083055938.94",
                "party_a_entity_code": "A08",
                "party_b_entity_code": "B03",
            },
        },
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    result = resolve_project_transaction_price(_fake_db(["A08", "B03"]), 15)
    assert result["amount"] == Decimal("1450000000.00")
    assert result["contract_no"] == "CDTF-MAIN-2026-01"


def test_resolver_accepts_equivalent_known_main_contract_aliases(monkeypatch):
    facts = [
        {
            "fact_id": 10,
            "fact_version": 1,
            "business_key": "contract:CDTF-MAIN-2023-01",
            "payload": {
                "contract_no": "CDTF-MAIN-2023-01",
                "total_amount": "1450000000.00",
                "party_a_entity_code": "E0",
                "party_b_entity_code": "A08",
            },
        },
        {
            "fact_id": 11,
            "fact_version": 2,
            "business_key": "contract:ZB-CD-TF",
            "payload": {
                "contract_no": "ZB-CD-TF",
                "total_amount": "1450000000.00",
                "party_a_entity_code": "E0",
                "party_b_entity_code": "A08",
            },
        },
        {
            "fact_id": 12,
            "fact_version": 3,
            "business_key": "contract:CDTF-MAIN-2026-01",
            "payload": {
                "contract_no": "CDTF-MAIN-2026-01",
                "total_amount": "1450000000.00",
                "party_a_entity_code": "E0",
                "party_b_entity_code": "A08",
            },
        },
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    result = resolve_project_transaction_price(_fake_db(["A08"]), 15)
    assert result["amount"] == Decimal("1450000000.00")
    assert result["fact_version"] in (2, 3)
    assert result["contract_no"] in ("ZB-CD-TF", "CDTF-MAIN-2026-01")


def test_resolver_degrades_to_largest_boundary_contract(monkeypatch):
    facts = [
        {"fact_id": 1, "fact_version": 1, "business_key": "c1", "payload": {"contract_no": "X-1", "total_amount": "100", "party_a_entity_code": "E0", "party_b_entity_code": "A08"}},
        {"fact_id": 2, "fact_version": 1, "business_key": "c2", "payload": {"contract_no": "X-2", "total_amount": "200", "party_a_entity_code": "EA", "party_b_entity_code": "A08"}},
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    result = resolve_project_transaction_price(_fake_db(["A08"]), 15)
    assert result["amount"] == Decimal("200")
    assert result["status"] == "DEGRADED"
    assert result["resolution_reason"] == "MULTIPLE_BOUNDARY_MAX_AMOUNT"


def test_resolver_degrades_deterministically_when_boundary_amounts_tie(monkeypatch):
    facts = [
        {"fact_id": 1, "fact_version": 1, "business_key": "c1", "payload": {"contract_no": "X-1", "total_amount": "100", "party_a_entity_code": "E0", "party_b_entity_code": "A08"}},
        {"fact_id": 2, "fact_version": 2, "business_key": "c2", "payload": {"contract_no": "X-2", "total_amount": "100", "party_a_entity_code": "EA", "party_b_entity_code": "A08"}},
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    result = resolve_project_transaction_price(_fake_db(["A08"]), 15)
    assert result["amount"] == Decimal("100")
    assert result["contract_no"] == "X-2"
    assert result["status"] == "DEGRADED"


def test_resolver_prefers_zb_prefix_without_static_project_codes(monkeypatch):
    facts = [
        {"fact_id": 1, "fact_version": 1, "business_key": "c1", "payload": {"contract_no": "OTHER-EXT", "total_amount": "990", "party_a_entity_code": "E0", "party_b_entity_code": "A08"}},
        {"fact_id": 2, "fact_version": 1, "business_key": "c2", "payload": {"contract_no": "ZB-FUTURE-NEW-PROJECT", "total_amount": "880", "party_a_entity_code": "EA", "party_b_entity_code": "A08"}},
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    result = resolve_project_transaction_price(_fake_db(["A08"]), 999)
    assert result["contract_no"] == "ZB-FUTURE-NEW-PROJECT"
    assert result["amount"] == Decimal("880")
    assert result["status"] == "READY"


def test_all_projects_resilient_summary(seeded_app, monkeypatch):
    """Every Project Master row must remain HTTP-visible even if summary logic degrades."""
    import app.dependencies as dependencies
    from app.main import app

    monkeypatch.setattr(
        dependencies,
        "require_login",
        lambda _request: SimpleNamespace(role="admin", username="resilience-test"),
    )

    with SessionLocal() as db:
        project_ids = list(db.execute(select(Project.id).order_by(Project.id)).scalars().all())
    assert project_ids, "seeded Project Master must contain projects"

    client = TestClient(app)
    collection = client.get("/api/projects")
    assert collection.status_code == 200
    collection_payload = collection.json()
    returned_ids = {
        int(item["project"]["id"])
        for item in collection_payload.get("projects", [])
        if isinstance(item, dict) and isinstance(item.get("project"), dict)
    }
    assert returned_ids == set(project_ids)

    for project_id in project_ids:
        response = client.get(f"/api/projects/{project_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["project"]["id"] == project_id
        assert payload.get("status") in {"READY", "DEGRADED"}
        assert payload["project"].get("name")


def test_api_project_source_has_no_legacy_project_summary_call():
    source = Path("app/routers/api.py").read_text(encoding="utf-8")
    api_block = source[source.index('@router.get("/api/projects/{pid}")'):source.index('@router.get("/api/projects/{pid}/matching")')]
    assert "canonical_project_summary(db, pid)" in api_block
    assert "calc.project_summary(" not in api_block
    assert "project.contract_total" not in api_block
    assert "_master_project_summary" in api_block
    assert "RealCost" not in api_block
    assert "Invoice" not in api_block
    assert "Progress" not in api_block


def test_phase4_transaction_price_is_canonical():
    source = Path("app/services/phase4_accounting.py").read_text(encoding="utf-8")
    assert "resolve_project_transaction_price" in source
    assert 'getattr(project, "contract_total"' not in source
    assert 'getattr(project, "contract_amount"' not in source
