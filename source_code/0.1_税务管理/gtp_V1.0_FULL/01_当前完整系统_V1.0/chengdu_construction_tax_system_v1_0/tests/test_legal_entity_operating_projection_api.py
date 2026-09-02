"""HTTP contract tests for the legal-entity operating projection."""
from __future__ import annotations

from fastapi.testclient import TestClient


_REQUIRED_PROJECTION_FIELDS = {
    "status",
    "scope",
    "is_filing_basis",
    "entity_code",
    "period",
    "revenue",
    "book_cost_projection",
    "accounting_profit_projection",
    "output_vat",
    "input_vat",
    "deductible_input_vat",
    "nondeductible_input_vat",
    "pending_input_vat",
    "internal_trade_net",
    "internal_trade_vat",
    "project_contributions",
    "non_project_contribution",
    "data_gaps",
    "source_of_truth",
    "official_vat_ledger",
}


def _login(client: TestClient) -> str:
    login_page = client.get("/login")
    assert login_page.status_code == 200
    csrf_token = client.cookies.get("tax_csrf")
    assert csrf_token

    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "888888",
            "_csrf": csrf_token,
        },
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert client.cookies.get("tax_session")

    post_login_csrf_token = client.cookies.get("tax_csrf")
    assert post_login_csrf_token
    return post_login_csrf_token


def _active_entity_code() -> str:
    from app.db import SessionLocal
    from app.models import Entity

    with SessionLocal() as db:
        entity = db.query(Entity).filter(Entity.active.is_(True)).order_by(Entity.code).first()
        assert entity is not None, "seeded test DB must contain an active legal entity"
        return str(entity.code)


def _projection_payload(entity_code: str, period: str | None) -> dict[str, object]:
    wanted = entity_code.strip().upper()
    projection_period = period or ""
    return {
        "status": "READY",
        "scope": "LEGAL_ENTITY_PROJECTION",
        "is_filing_basis": False,
        "entity_code": wanted,
        "period": projection_period,
        "revenue": 1000.0,
        "book_cost_projection": 600.0,
        "accounting_profit_projection": 400.0,
        "output_vat": 90.0,
        "input_vat": 54.0,
        "deductible_input_vat": 50.0,
        "nondeductible_input_vat": 2.0,
        "pending_input_vat": 2.0,
        "input_vat_accounted": 54.0,
        "input_vat_unaccounted": 0.0,
        "input_vat_identity_ok": True,
        "internal_trade_net": 120.0,
        "internal_trade_vat": 10.8,
        "project_contributions": [
            {
                "project_id": 1,
                "project_code": "P-001",
                "project_name": "测试项目",
                "revenue": 1000.0,
                "book_cost_projection": 600.0,
                "accounting_profit_projection": 400.0,
                "output_vat": 90.0,
                "input_vat": 54.0,
                "deductible_input_vat": 50.0,
                "nondeductible_input_vat": 2.0,
                "pending_input_vat": 2.0,
                "internal_trade_net": 120.0,
                "internal_trade_vat": 10.8,
                "fact_count": 2,
                "fact_ids": [101, 102],
            }
        ],
        "non_project_contribution": {
            "project_id": None,
            "project_code": "",
            "project_name": "非项目归属",
            "revenue": 0.0,
            "book_cost_projection": 0.0,
            "accounting_profit_projection": 0.0,
            "output_vat": 0.0,
            "input_vat": 0.0,
            "deductible_input_vat": 0.0,
            "nondeductible_input_vat": 0.0,
            "pending_input_vat": 0.0,
            "internal_trade_net": 0.0,
            "internal_trade_vat": 0.0,
            "fact_count": 0,
            "fact_ids": [],
        },
        "fact_count": 2,
        "fact_ids": [101, 102],
        "data_gaps": [],
        "source_of_truth": "analytics_canonical_facts_current",
        "official_vat_ledger": "entity_vat_ledgers",
        "limitations": ["test projection fixture"],
        "calculation_version": "legal-entity-canonical-scope-v1",
    }


def _mock_projection(monkeypatch):
    import app.routers.legal_entity_operating_projection as projection_router

    calls: list[tuple[str, str | None]] = []

    def fake_aggregate(db, entity_code: str, *, period: str | None = None):
        calls.append((entity_code, period))
        return _projection_payload(entity_code, period)

    monkeypatch.setattr(
        projection_router,
        "aggregate_legal_entity_scope",
        fake_aggregate,
    )
    return calls


def test_csrf_handshake_grants_reader_access_to_operating_projection(
    seeded_app,
    monkeypatch,
):
    from app.main import app

    calls = _mock_projection(monkeypatch)
    client = TestClient(app)
    fresh_csrf = _login(client)
    assert fresh_csrf == client.cookies.get("tax_csrf")

    entity_code = _active_entity_code()
    response = client.get(
        f"/api/legal-entities/{entity_code}/operating-projection?period=2026-08"
    )
    assert response.status_code == 200, response.text
    assert calls == [(entity_code, "2026-08")]


def test_operating_projection_contract_and_scope(seeded_app, monkeypatch):
    from app.main import app

    calls = _mock_projection(monkeypatch)
    client = TestClient(app)
    _login(client)
    entity_code = _active_entity_code()

    response = client.get(
        f"/api/legal-entities/{entity_code}/operating-projection?period=2026-08"
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert calls == [(entity_code, "2026-08")]
    assert _REQUIRED_PROJECTION_FIELDS <= body.keys()
    assert body["entity_code"] == entity_code.strip().upper()
    assert body["period"] == "2026-08"
    assert body["scope"] == "LEGAL_ENTITY_PROJECTION"
    assert body["is_filing_basis"] is False
    assert body["source_of_truth"] == "analytics_canonical_facts_current"
    assert body["official_vat_ledger"] == "entity_vat_ledgers"
    assert body["revenue"] == 1000.0
    assert body["book_cost_projection"] == 600.0
    assert body["accounting_profit_projection"] == 400.0
    assert isinstance(body["project_contributions"], list)
    assert isinstance(body["non_project_contribution"], dict)
    assert isinstance(body["data_gaps"], list)


def test_operating_projection_supports_optional_period(seeded_app, monkeypatch):
    from app.main import app

    calls = _mock_projection(monkeypatch)
    client = TestClient(app)
    _login(client)
    entity_code = _active_entity_code()

    response = client.get(
        f"/api/legal-entities/{entity_code}/operating-projection"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == [(entity_code, None)]
    assert body["period"] == ""
    assert body["scope"] == "LEGAL_ENTITY_PROJECTION"
    assert body["is_filing_basis"] is False


def test_operating_projection_rejects_invalid_period(seeded_app, monkeypatch):
    from app.main import app

    calls = _mock_projection(monkeypatch)
    client = TestClient(app)
    _login(client)
    entity_code = _active_entity_code()

    response = client.get(
        f"/api/legal-entities/{entity_code}/operating-projection?period=2026-13"
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "period 必须为 YYYY-MM 格式"
    assert calls == []
