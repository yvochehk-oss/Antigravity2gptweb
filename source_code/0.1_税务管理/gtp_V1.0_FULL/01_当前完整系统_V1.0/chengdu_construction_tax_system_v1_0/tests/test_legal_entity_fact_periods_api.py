"""Contract and ordering tests for legal-entity Canonical invoice fact periods."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.legal_entity_fact_periods import _summarize_fact_periods


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


def test_fact_period_summary_orders_by_count_then_latest_period():
    facts = [
        {"payload": {"seller_entity_code": "A08", "buyer_entity_code": "X01", "invoice_date": "2026-03-12"}},
        {"payload": {"seller_entity_code": "A08", "buyer_entity_code": "X02", "invoice_date": "2026-03-22"}},
        {"payload": {"seller_entity_code": "X03", "buyer_entity_code": "A08", "invoice_date": "2025-08-01"}},
        {"payload": {"seller_entity_code": "X04", "buyer_entity_code": "A08", "invoice_date": "2025-08-02"}},
        {"payload": {"seller_entity_code": "A08", "buyer_entity_code": "X05", "invoice_date": "2024-07-03"}},
        {"payload": {"seller_entity_code": "A08", "buyer_entity_code": "X06"}},
        {"payload": {"seller_entity_code": "B01", "buyer_entity_code": "X07", "invoice_date": "2026-12-01"}},
    ]

    result = _summarize_fact_periods(facts, entity_code="a08")

    assert result["status"] == "READY"
    assert result["entity_code"] == "A08"
    assert result["source_of_truth"] == "analytics_canonical_facts_current"
    assert result["fact_type"] == "invoice"
    assert result["total_fact_count"] == 5
    assert result["unperiodized_fact_count"] == 1
    assert result["periods"] == [
        {"period": "2026-03", "fact_count": 2, "is_primary": True},
        {"period": "2025-08", "fact_count": 2, "is_primary": False},
        {"period": "2024-07", "fact_count": 1, "is_primary": False},
    ]


def test_fact_period_summary_returns_empty_without_fabricating_periods():
    result = _summarize_fact_periods(
        [{"payload": {"seller_entity_code": "A08", "buyer_entity_code": "X01"}}],
        entity_code="A08",
    )
    assert result["status"] == "EMPTY"
    assert result["periods"] == []
    assert result["total_fact_count"] == 0
    assert result["unperiodized_fact_count"] == 1


def test_fact_period_api_exposes_canonical_invoice_period_contract(seeded_app, monkeypatch):
    from app.main import app
    import app.routers.legal_entity_operating_projection as router_module

    calls: list[str] = []

    def fake_periods(db, entity_code: str):
        calls.append(entity_code)
        return {
            "status": "READY",
            "entity_code": entity_code.strip().upper(),
            "source_of_truth": "analytics_canonical_facts_current",
            "fact_type": "invoice",
            "total_fact_count": 18,
            "unperiodized_fact_count": 1,
            "periods": [
                {"period": "2026-03", "fact_count": 12, "is_primary": True},
                {"period": "2025-08", "fact_count": 6, "is_primary": False},
            ],
        }

    monkeypatch.setattr(router_module, "get_legal_entity_fact_periods", fake_periods)
    client = TestClient(app)
    _login(client)

    response = client.get("/api/v3/legal-entities/A08/fact-periods")
    assert response.status_code == 200, response.text
    assert calls == ["A08"]
    assert response.json() == {
        "status": "READY",
        "entity_code": "A08",
        "source_of_truth": "analytics_canonical_facts_current",
        "fact_type": "invoice",
        "total_fact_count": 18,
        "unperiodized_fact_count": 1,
        "periods": [
            {"period": "2026-03", "fact_count": 12, "is_primary": True},
            {"period": "2025-08", "fact_count": 6, "is_primary": False},
        ],
    }
