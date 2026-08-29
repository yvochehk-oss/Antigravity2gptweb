"""Unit contracts for the authenticated React collection payloads."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.routers.collections import (
    _audit_item,
    _page,
    _project_data_quality,
    _risk_item,
    _tax_item,
)


def _login(client: TestClient, username: str = "admin") -> None:
    response = client.post(
        "/login",
        data={"username": username, "password": "TestPass12345!"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_page_is_bounded_and_reports_more_rows() -> None:
    rows, total, has_more = _page([1, 2, 3, 4, 5], page=2, page_size=2)
    assert rows == [3, 4]
    assert total == 5
    assert has_more is True


def test_risk_payload_is_database_derived_and_has_frontend_aliases() -> None:
    event = SimpleNamespace(
        id=11,
        project_id=7,
        severity="danger",
        code="FOUR_STREAM_MISMATCH",
        message="缺少履约证据",
        resolved=False,
    )
    project = SimpleNamespace(id=7, code="A08", name="真实项目")
    item = _risk_item(event, project)

    assert item["id"] == "11"
    assert item["severity"] == "高危"
    assert item["projectName"] == "真实项目"
    assert item["description"] == "缺少履约证据"
    assert "演示" not in str(item)


def test_tax_payload_preserves_deterministic_values_and_unknown_risk() -> None:
    ledger = SimpleNamespace(
        id=12,
        period="2026-08",
        entity_code="A08",
        output_vat="12.50",
        input_vat="2.50",
        vat_payable="10.00",
        revenue="100.00",
        real_cost="40.00",
        estimated_profit="60.00",
        estimated_cit="15.00",
        cit_note="management estimate",
        generated=True,
    )
    entity = SimpleNamespace(
        code="A08",
        name="四川锐宝建设有限公司",
        business_role="A",
        kind="construction",
        legal_entity=True,
    )
    item = _tax_item(ledger, entity)

    assert item["entity_code"] == "A08"
    assert item["revenue"] == 100.0
    assert item["taxAmount"] == 10.0
    assert item["riskLevel"] == "未知"
    assert item["isInternal"] is True
    assert item["source"] == "canonical"


def test_tax_item_marks_external_entity_when_code_outside_canonical_set() -> None:
    ledger = SimpleNamespace(
        id=2,
        period="2026-Q2",
        entity_code="EXT-9011",
        output_vat="10.00",
        input_vat="0",
        vat_payable="10.00",
        revenue="100.00",
        real_cost="40.00",
        estimated_profit="60.00",
        estimated_cit="15.00",
        cit_note="external vendor",
        generated=True,
    )
    item = _tax_item(ledger, None)

    assert item["entity_code"] == "EXT-9011"
    assert item["isInternal"] is False
    assert item["source"] == "external"


def test_audit_payload_does_not_fabricate_integrity_or_timestamps() -> None:
    row = SimpleNamespace(
        id=3,
        action="CREATE",
        object_type="Invoice",
        object_id="19",
        actor="operator",
        message="created",
        ip="127.0.0.1",
        request_id="req-3",
    )
    item = _audit_item(row)

    assert item["target_subject"] == "Invoice:19"
    assert item["operator"] == "operator"
    assert item["integrity_hash"] == ""
    assert item["integrity_status"] == "NOT_RECORDED"
    assert item["timestamp"] == ""


def test_collection_routes_require_login(seeded_app) -> None:
    from app.main import app

    client = TestClient(app)
    for path in ("/api/projects", "/api/risks", "/api/tax-ledger", "/api/audit"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()["detail"]


def test_collection_routes_return_real_database_envelopes(seeded_app) -> None:
    from app.main import app

    client = TestClient(app)
    _login(client)
    for path in (
        "/api/projects",
        "/api/risks",
        "/api/tax-ledger?period=2026-08",
        "/api/audit",
    ):
        response = client.get(path)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] in {"READY", "DEGRADED"}
        assert isinstance(payload["items"], list)
        assert payload["items"] == payload["data"]
        assert payload["total"] >= len(payload["items"])
        if payload["status"] == "DEGRADED":
            assert any(m in payload["message"] for m in ("数据缺口", "data gap", "降级", "待复核"))

    projects = client.get("/api/projects").json()
    assert projects["projects"]
    assert all(project["id"] > 0 for project in projects["projects"])


def test_six_projects_all_unmapped_are_degraded() -> None:
    from types import SimpleNamespace

    projects = [
        SimpleNamespace(id=index, entity_code=None)
        for index in range(1, 7)
    ]
    states = {
        project.id: _project_data_quality(
            project,
            entity_code=project.entity_code,
            entity_code_available=True,
            entities={},
        )
        for project in projects
    }

    assert len(states) == 6
    assert all(state["data_status"] == "DEGRADED" for state in states.values())
    assert all(
        state["data_gaps"] == ["PROJECT_ENTITY_CODE_MISSING"]
        for state in states.values()
    )


def test_six_projects_partial_mapping_keeps_mapped_projects_ready() -> None:
    from types import SimpleNamespace

    entities = {
        "A08": SimpleNamespace(code="A08", active=True),
        "B01": SimpleNamespace(code="B01", active=True),
    }
    codes = ["A08", "B01", None, None, None, None]
    states = {
        index: _project_data_quality(
            SimpleNamespace(id=index),
            entity_code=code,
            entity_code_available=True,
            entities=entities,
        )
        for index, code in enumerate(codes, start=1)
    }

    assert [states[index]["data_status"] for index in (1, 2)] == [
        "READY", "READY",
    ]
    assert all(states[index]["trusted"] for index in (1, 2))
    assert all(
        states[index]["data_status"] == "DEGRADED"
        for index in (3, 4, 5, 6)
    )


def test_six_projects_illegal_entity_codes_never_map_by_project_code() -> None:
    from types import SimpleNamespace

    codes = ["A", "B", "C", "D", "YB-DEMO-001", "NOT-CANONICAL"]
    states = {
        index: _project_data_quality(
            SimpleNamespace(id=index),
            entity_code=code,
            entity_code_available=True,
            entities={},
        )
        for index, code in enumerate(codes, start=1)
    }

    assert all(state["data_status"] == "DEGRADED" for state in states.values())
    assert all(
        state["data_gaps"][0].startswith("PROJECT_ENTITY_CODE_INVALID:")
        for state in states.values()
    )


def test_missing_or_inactive_entity_is_not_silently_accepted() -> None:
    from types import SimpleNamespace

    entities = {
        "A08": SimpleNamespace(code="A08", active=True),
        "A09": SimpleNamespace(code="A09", active=False),
    }
    missing = _project_data_quality(
        SimpleNamespace(id=1),
        entity_code="B01",
        entity_code_available=True,
        entities=entities,
    )
    inactive = _project_data_quality(
        SimpleNamespace(id=2),
        entity_code="A09",
        entity_code_available=True,
        entities=entities,
    )

    assert missing["data_status"] == "DEGRADED"
    assert missing["data_gaps"] == ["PROJECT_ENTITY_NOT_FOUND:B01"]
    assert inactive["data_status"] == "DEGRADED"
    assert inactive["data_gaps"] == ["PROJECT_ENTITY_INACTIVE:A09"]


def test_collection_filters_and_period_validation(seeded_app) -> None:
    from app.main import app

    client = TestClient(app)
    _login(client, "operator")
    response = client.get("/api/tax-ledger?period=not-a-period")
    assert response.status_code == 422
    response = client.get("/api/risks?page=0")
    assert response.status_code == 422
    response = client.get("/api/projects?search=不存在的项目")
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_tax_ledger_get_is_read_only_and_filters_existing_rows(seeded_app) -> None:
    """GET must never rebuild/delete a period and must filter stored rows only."""
    from decimal import Decimal

    from app.db import SessionLocal
    from app.main import app
    from app.models import TaxLedger

    period = "2099-07"
    db = SessionLocal()
    try:
        db.add_all(
            [
                TaxLedger(
                    period=period,
                    entity_code="A08",
                    output_vat=Decimal("10.00"),
                    input_vat=Decimal("2.00"),
                    vat_payable=Decimal("8.00"),
                    revenue=Decimal("100.00"),
                    real_cost=Decimal("40.00"),
                    estimated_profit=Decimal("60.00"),
                    estimated_cit=Decimal("9.00"),
                    cit_note="read-only fixture",
                    generated=True,
                ),
                TaxLedger(
                    period=period,
                    entity_code="B01",
                    output_vat=Decimal("20.00"),
                    input_vat=Decimal("4.00"),
                    vat_payable=Decimal("16.00"),
                    revenue=Decimal("200.00"),
                    real_cost=Decimal("80.00"),
                    estimated_profit=Decimal("120.00"),
                    estimated_cit=Decimal("18.00"),
                    cit_note="read-only fixture",
                    generated=True,
                ),
                TaxLedger(
                    period=period,
                    entity_code="C02",
                    output_vat=Decimal("30.00"),
                    input_vat=Decimal("6.00"),
                    vat_payable=Decimal("24.00"),
                    revenue=Decimal("300.00"),
                    real_cost=Decimal("120.00"),
                    estimated_profit=Decimal("180.00"),
                    estimated_cit=Decimal("27.00"),
                    cit_note="read-only fixture",
                    generated=True,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    def snapshot() -> list[tuple]:
        session = SessionLocal()
        try:
            return [
                (
                    row.id,
                    row.period,
                    row.entity_code,
                    str(row.output_vat),
                    str(row.input_vat),
                    str(row.vat_payable),
                    str(row.revenue),
                    str(row.real_cost),
                    str(row.estimated_profit),
                    str(row.estimated_cit),
                    row.cit_note,
                    row.generated,
                )
                for row in session.query(TaxLedger)
                .filter(TaxLedger.period == period)
                .order_by(TaxLedger.id)
                .all()
            ]
        finally:
            session.close()

    client = TestClient(app)
    try:
        _login(client)
        before = snapshot()
        all_rows = client.get(f"/api/tax-ledger?period={period}")
        assert all_rows.status_code == 200, all_rows.text
        assert [item["entity_code"] for item in all_rows.json()["items"]] == [
            "A08", "B01", "C02",
        ]

        project_rows = client.get(
            f"/api/tax-ledger?period={period}&project_id=1"
        )
        assert project_rows.status_code == 200, project_rows.text
        assert [item["entity_code"] for item in project_rows.json()["items"]] == [
            "A08", "B01",
        ]

        entity_rows = client.get(
            f"/api/tax-ledger?period={period}&entity_code=B01"
        )
        assert entity_rows.status_code == 200, entity_rows.text
        assert [item["entity_code"] for item in entity_rows.json()["items"]] == [
            "B01",
        ]
        assert snapshot() == before
    finally:
        cleanup = SessionLocal()
        try:
            cleanup.query(TaxLedger).filter(TaxLedger.period == period).delete(
                synchronize_session=False
            )
            cleanup.commit()
        finally:
            cleanup.close()
