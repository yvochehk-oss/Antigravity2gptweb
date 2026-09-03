"""HTTP contract tests for V3 legal-entity Master Data."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _login(client: TestClient) -> None:
    assert client.get("/login").status_code == 200
    csrf_token = client.cookies.get("tax_csrf")
    assert csrf_token
    response = client.post(
        "/login",
        data={"username": "admin", "password": "888888", "_csrf": csrf_token},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_v3_legal_entities_exposes_party_ssot_contract(seeded_app, monkeypatch):
    from app.main import app
    import app.routers.legal_entity_operating_projection as router_module

    calls: list[tuple[bool, bool]] = []

    def fake_list(db, *, active: bool, legal_entity: bool):
        calls.append((active, legal_entity))
        return [
            {"party_id": 8, "canonical_code": "A08", "legal_name": "测试建设公司"},
            {"party_id": 31, "canonical_code": "B01", "legal_name": "测试贸易公司"},
        ]

    monkeypatch.setattr(router_module, "list_legal_entities", fake_list)
    client = TestClient(app)
    _login(client)

    response = client.get("/api/v3/legal-entities?active=true&legal_entity=true")
    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == [(True, True)]
    assert body == {
        "status": "READY",
        "source_of_truth": "parties+internal_entities",
        "items": [
            {"party_id": 8, "canonical_code": "A08", "legal_name": "测试建设公司"},
            {"party_id": 31, "canonical_code": "B01", "legal_name": "测试贸易公司"},
        ],
        "total": 2,
    }


def test_v3_legal_entities_forwards_filter_flags(seeded_app, monkeypatch):
    from app.main import app
    import app.routers.legal_entity_operating_projection as router_module

    calls: list[tuple[bool, bool]] = []

    def fake_list(db, *, active: bool, legal_entity: bool):
        calls.append((active, legal_entity))
        return []

    monkeypatch.setattr(router_module, "list_legal_entities", fake_list)
    client = TestClient(app)
    _login(client)

    response = client.get("/api/v3/legal-entities?active=false&legal_entity=false")
    assert response.status_code == 200, response.text
    assert calls == [(False, False)]
    assert response.json()["total"] == 0
