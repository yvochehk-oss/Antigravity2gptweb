"""HTTP contract for the FVAT-2 statutory rebuild command."""
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


def test_formal_vat_rebuild_api_uses_single_entity_period_command(seeded_app, monkeypatch):
    from app.main import app
    import app.routers.legal_entity_operating_projection as router_module

    calls: list[dict] = []

    def fake_rebuild(db, **kwargs):
        calls.append(kwargs)
        return {
            "status": "BUILT",
            "resource_type": "FORMAL_VAT_STATUTORY_V1",
            "entity_code": kwargs["entity_code"].upper(),
            "reporting_party_id": 88,
            "period": kwargs["period"],
            "tax_period": f"{kwargs['period']}-01",
            "run_kind": "STANDARD",
            "calculation_run_id": 901,
            "ledger_id": 902,
            "input_snapshot_sha256": "a" * 64,
            "result_sha256": "b" * 64,
            "resource": {
                "status": "READY",
                "resource_type": "FORMAL_VAT_STATUTORY_V1",
                "entity_code": kwargs["entity_code"].upper(),
                "period": kwargs["period"],
            },
        }

    monkeypatch.setattr(router_module, "rebuild_formal_vat_statutory_resource", fake_rebuild)
    client = TestClient(app)
    _login(client)
    csrf_token = client.cookies.get("tax_csrf")
    assert csrf_token

    response = client.post(
        "/api/v3/legal-entities/A08/statutory-vat/rebuild",
        params={"period": "2026-04"},
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": csrf_token,
        },
    )

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["entity_code"] == "A08"
    assert calls[0]["period"] == "2026-04"
    assert calls[0]["allow_restatement"] is False
    assert calls[0]["created_by"] == "admin"
    payload = response.json()
    assert payload["status"] == "BUILT"
    assert payload["resource_type"] == "FORMAL_VAT_STATUTORY_V1"
    assert payload["calculation_run_id"] == 901
    assert payload["ledger_id"] == 902
