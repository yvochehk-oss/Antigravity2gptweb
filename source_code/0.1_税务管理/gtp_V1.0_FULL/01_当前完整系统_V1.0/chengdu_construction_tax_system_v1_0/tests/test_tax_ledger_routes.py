"""Classic tax-ledger read boundary and FVAT-3 retirement regressions."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select


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


def _snapshot(period: str) -> list[tuple[object, ...]]:
    from app.db import SessionLocal
    from app.models import TaxLedger

    with SessionLocal() as db:
        rows = db.execute(
            select(TaxLedger)
            .where(TaxLedger.period == period)
            .order_by(TaxLedger.entity_code, TaxLedger.id)
        ).scalars().all()
        return [
            (
                row.id,
                row.period,
                row.entity_code,
                row.output_vat,
                row.input_vat,
                row.vat_payable,
                row.revenue,
                row.real_cost,
                row.estimated_profit,
                row.estimated_cit,
                row.cit_note,
                row.generated,
            )
            for row in rows
        ]


def test_html_get_requires_login_and_is_read_only(seeded_app):
    from app.main import app

    client = TestClient(app)
    assert client.get("/tax-ledger", follow_redirects=False).status_code == 302

    _login(client)
    before = _snapshot("2026-08")
    response = client.get("/tax-ledger?period=2026-08")
    assert response.status_code == 200
    assert "实际法人月度税务管理台账" in response.text
    assert _snapshot("2026-08") == before


def test_legacy_rebuild_paths_are_permanently_gone(seeded_app):
    from app.main import app

    client = TestClient(app)
    _login(client)
    headers = {"X-CSRF-Token": client.cookies.get("tax_csrf")}
    api_response = client.post(
        "/api/tax-ledger/rebuild",
        json={"period": "2026-08"},
        headers=headers,
    )
    html_response = client.post(
        "/tax-ledger/rebuild",
        data={"period": "2026-08"},
        headers=headers,
        follow_redirects=False,
    )

    for response in (api_response, html_response):
        assert response.status_code == 410
        assert response.json()["detail"] == {
            "code": "LEGACY_TAX_LEDGER_REBUILD_RETIRED",
            "detail": (
                "旧 TaxLedger 重建写入口已永久退役；正式 VAT 只能通过 "
                "Canonical Statutory 资源按法人、期间确定性重建。"
            ),
            "canonical_endpoint_template": (
                "/api/v3/legal-entities/{entity_code}/statutory-vat/rebuild?period={period}"
            ),
        }


def test_retired_api_preserves_auth_and_csrf_guards(seeded_app):
    from app.main import app

    anonymous = TestClient(app)
    assert anonymous.post(
        "/api/tax-ledger/rebuild", json={"period": "2026-08"}
    ).status_code == 401

    client = TestClient(app)
    _login(client)
    assert client.post(
        "/api/tax-ledger/rebuild", json={"period": "2026-08"}
    ).status_code == 403
    response = client.post(
        "/api/tax-ledger/rebuild",
        json={"period": "2026-08"},
        headers={"X-CSRF-Token": client.cookies.get("tax_csrf")},
    )
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "LEGACY_TAX_LEDGER_REBUILD_RETIRED"


def test_legacy_rebuild_is_gone_before_body_validation(seeded_app):
    from app.main import app

    client = TestClient(app)
    _login(client)
    headers = {"X-CSRF-Token": client.cookies.get("tax_csrf")}
    for payload in (None, {}, {"period": "bad"}, {"unexpected": True}):
        response = client.post("/api/tax-ledger/rebuild", json=payload, headers=headers)
        assert response.status_code == 410
        assert response.json()["detail"]["code"] == "LEGACY_TAX_LEDGER_REBUILD_RETIRED"


def test_legacy_rebuild_cannot_mutate_tax_ledger(seeded_app):
    from app.main import app

    period = "2026-08"
    before = _snapshot(period)
    client = TestClient(app)
    _login(client)
    response = client.post(
        "/api/tax-ledger/rebuild",
        json={"period": period},
        headers={"X-CSRF-Token": client.cookies.get("tax_csrf")},
    )
    assert response.status_code == 410
    assert _snapshot(period) == before


def test_api_tax_ledger_get_remains_read_only_and_deprecated(seeded_app):
    from app.main import app

    period = "2098-03"
    before = _snapshot(period)
    client = TestClient(app)
    _login(client)
    response = client.get(f"/api/tax-ledger?period={period}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["deprecated"] is True
    assert _snapshot(period) == before
