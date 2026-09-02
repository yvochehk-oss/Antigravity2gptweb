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


def test_csrf_handshake_grants_reader_access_to_operating_projection(seeded_app):
    from app.main import app

    client = TestClient(app)
    fresh_csrf = _login(client)
    assert fresh_csrf == client.cookies.get("tax_csrf")

    entity_code = _active_entity_code()
    response = client.get(
        f"/api/legal-entities/{entity_code}/operating-projection?period=2026-08"
    )
    assert response.status_code == 200, response.text


def test_operating_projection_contract_and_scope(seeded_app):
    from app.main import app

    client = TestClient(app)
    _login(client)
    entity_code = _active_entity_code()

    response = client.get(
        f"/api/legal-entities/{entity_code}/operating-projection?period=2026-08"
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert _REQUIRED_PROJECTION_FIELDS <= body.keys()
    assert body["entity_code"] == entity_code.strip().upper()
    assert body["period"] == "2026-08"
    assert body["scope"] == "LEGAL_ENTITY_PROJECTION"
    assert body["is_filing_basis"] is False
    assert body["source_of_truth"] == "analytics_canonical_facts_current"
    assert body["official_vat_ledger"] == "entity_vat_ledgers"
    assert isinstance(body["project_contributions"], list)
    assert isinstance(body["non_project_contribution"], dict)
    assert isinstance(body["data_gaps"], list)


def test_operating_projection_supports_optional_period(seeded_app):
    from app.main import app

    client = TestClient(app)
    _login(client)
    entity_code = _active_entity_code()

    response = client.get(
        f"/api/legal-entities/{entity_code}/operating-projection"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["period"] == ""
    assert body["scope"] == "LEGAL_ENTITY_PROJECTION"
    assert body["is_filing_basis"] is False


def test_operating_projection_rejects_invalid_period(seeded_app):
    from app.main import app

    client = TestClient(app)
    _login(client)
    entity_code = _active_entity_code()

    response = client.get(
        f"/api/legal-entities/{entity_code}/operating-projection?period=2026-13"
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "period 必须为 YYYY-MM 格式"
