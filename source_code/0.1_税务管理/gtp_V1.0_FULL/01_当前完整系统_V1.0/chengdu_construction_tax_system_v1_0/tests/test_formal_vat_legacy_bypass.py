"""FVAT-3 regression tests for removal of legacy statutory bypasses."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.formal_vat_statutory import FormalVatStatutoryResourceNotFoundError


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


def _canonical_payload(entity_code: str, period: str) -> dict[str, object]:
    return {
        "status": "READY",
        "resource_type": "FORMAL_VAT_STATUTORY_V1",
        "source_of_truth": (
            "tax_period_states.current_run_id->calculation_runs->entity_vat_ledgers"
        ),
        "entity_code": entity_code.upper(),
        "reporting_party_id": 88,
        "period": period,
        "tax_period": f"{period}-01",
        "period_state": "OPEN",
        "state_version": 3,
        "calculation_run": {
            "id": 901,
            "run_kind": "STANDARD",
            "run_status": "SUCCEEDED",
            "ruleset_version": "V3_VAT_LEDGER_V1",
            "input_snapshot_sha256": "a" * 64,
            "result_sha256": "b" * 64,
            "completed_at": "2026-04-30T10:00:00+00:00",
        },
        "vat_ledger": {
            "id": 902,
            "opening_input_credit": "10.00",
            "output_vat": "130.00",
            "input_vat": "40.00",
            "tax_prepayment": "20.00",
            "vat_payable_before_prepayment": "80.00",
            "closing_input_credit": "0.00",
            "vat_payable_after_prepayment": "60.00",
            "unapplied_tax_prepayment": "0.00",
            "created_at": "2026-04-30T10:00:01+00:00",
        },
    }


def test_legacy_entity_tax_ledger_route_is_physically_replaced(seeded_app):
    from app.main import app

    matching = [
        route
        for route in app.router.routes
        if getattr(route, "path", "") == "/api/entity-tax-ledger"
        and "GET" in (getattr(route, "methods", None) or set())
    ]
    assert len(matching) == 1
    assert matching[0].name == "fvat3_legacy_entity_tax_ledger_proxy"


def test_legacy_entity_tax_ledger_delegates_only_to_formal_statutory(
    seeded_app, monkeypatch
):
    from app.main import app
    import app.routers.collections as legacy_collections
    import app.services.phase3_retirement as cutover

    calls: list[tuple[str, str]] = []

    def legacy_builder_must_not_run(*args, **kwargs):
        raise AssertionError("legacy EntityTaxLedger/TaxLedger projection must be unreachable")

    def fake_formal_read(db, entity_code: str, period: str):
        calls.append((entity_code, period))
        return _canonical_payload(entity_code, period)

    monkeypatch.setattr(
        legacy_collections,
        "_build_v3_entity_tax_ledger_envelope",
        legacy_builder_must_not_run,
    )
    monkeypatch.setattr(cutover, "get_formal_vat_statutory_resource", fake_formal_read)

    client = TestClient(app)
    _login(client)
    response = client.get(
        "/api/entity-tax-ledger",
        params={"entity_code": "A08", "period": "2026-04"},
    )

    assert response.status_code == 200, response.text
    assert calls == [("A08", "2026-04")]
    assert response.json() == _canonical_payload("A08", "2026-04")


def test_legacy_entity_tax_ledger_accepts_entity_alias_but_not_collection_scope(
    seeded_app, monkeypatch
):
    from app.main import app
    import app.services.phase3_retirement as cutover

    calls: list[tuple[str, str]] = []

    def fake_formal_read(db, entity_code: str, period: str):
        calls.append((entity_code, period))
        return _canonical_payload(entity_code, period)

    monkeypatch.setattr(cutover, "get_formal_vat_statutory_resource", fake_formal_read)
    client = TestClient(app)
    _login(client)

    alias_response = client.get(
        "/api/entity-tax-ledger",
        params={"entity": "a08", "period": "2026-04"},
    )
    assert alias_response.status_code == 200
    assert calls == [("a08", "2026-04")]

    missing_period = client.get(
        "/api/entity-tax-ledger", params={"entity_code": "A08"}
    )
    assert missing_period.status_code == 422
    assert missing_period.json()["detail"]["code"] == "FORMAL_VAT_PERIOD_REQUIRED"

    missing_entity = client.get(
        "/api/entity-tax-ledger", params={"period": "2026-04"}
    )
    assert missing_entity.status_code == 422
    assert missing_entity.json()["detail"]["code"] == "FORMAL_VAT_ENTITY_REQUIRED"

    project_scope = client.get(
        "/api/entity-tax-ledger",
        params={"entity_code": "A08", "period": "2026-04", "project_id": 1},
    )
    assert project_scope.status_code == 422
    assert project_scope.json()["detail"]["code"] == "FORMAL_VAT_PROJECT_SCOPE_NOT_ALLOWED"


def test_legacy_entity_tax_ledger_rejects_conflicting_entity_aliases(seeded_app):
    from app.main import app

    client = TestClient(app)
    _login(client)
    response = client.get(
        "/api/entity-tax-ledger",
        params={"entity": "A08", "entity_code": "A09", "period": "2026-04"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "FORMAL_VAT_ENTITY_SCOPE_CONFLICT"


def test_legacy_entity_tax_ledger_preserves_canonical_missing_resource_contract(
    seeded_app, monkeypatch
):
    from app.main import app
    import app.services.phase3_retirement as cutover

    def fake_missing(db, entity_code: str, period: str):
        raise FormalVatStatutoryResourceNotFoundError(f"{entity_code} {period}")

    monkeypatch.setattr(cutover, "get_formal_vat_statutory_resource", fake_missing)
    client = TestClient(app)
    _login(client)
    response = client.get(
        "/api/entity-tax-ledger",
        params={"entity_code": "A08", "period": "2026-04"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "FORMAL_VAT_STATUTORY_RESOURCE_NOT_FOUND",
        "detail": "该法人及期间尚无正式 VAT 法定资源；读取端点不会现场重算。",
    }
