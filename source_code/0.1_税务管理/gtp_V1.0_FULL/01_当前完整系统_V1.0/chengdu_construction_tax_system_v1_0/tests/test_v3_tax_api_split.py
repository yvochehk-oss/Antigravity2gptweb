"""V3 Phase A3 / v1.2 S0-02 — Tax API split regression tests.

v1.2 Step 0.1 contract:
  - /api/entity-tax-ledger: legal-entity month ledger; accepts period + entity,
    and MUST reject project_id.
  - /api/project-tax-analysis: accepts project_id + period + entity and derives
    project-only VAT/cost numbers from project evidence, never TaxLedger.
  - /api/tax-ledger?project_id=: first-stage compatibility response is only a
    deprecation warning; it must not return the old mixed-scope result.

These tests require a seeded PostgreSQL via TEST_DATABASE_URL.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import case, func


def _login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "admin", "password": "888888"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert client.cookies.get("tax_session")


def test_tax_ledger_marked_deprecated(seeded_app):
    from app.main import app

    client = TestClient(app)
    _login(client)
    r = client.get("/api/tax-ledger")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("deprecated") is True
    assert "deprecation_message" in body
    assert body["recommended_endpoints"]["entity_ledger"] == "/api/entity-tax-ledger"
    assert body["recommended_endpoints"]["project_analysis"] == "/api/project-tax-analysis"


def test_entity_tax_ledger_rejects_project_id(seeded_app):
    from app.main import app

    client = TestClient(app)
    _login(client)
    r = client.get("/api/entity-tax-ledger?project_id=1")
    assert r.status_code == 422, r.text


def test_entity_tax_ledger_supports_entity_filter(seeded_app):
    from app.db import SessionLocal
    from app.main import app
    from app.models import TaxLedger

    db = SessionLocal()
    try:
        row = db.query(TaxLedger).first()
        if row is None:
            pytest.skip("seeded DB has no tax ledger rows")
        entity = row.entity_code
        period = row.period
    finally:
        db.close()

    client = TestClient(app)
    _login(client)
    r = client.get(f"/api/entity-tax-ledger?period={period}&entity={entity}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert all(item["entity_code"] == entity for item in body["items"])
    assert all("project_id" not in item for item in body["items"])


def test_entity_tax_ledger_returns_legal_entities(seeded_app):
    from app.main import app

    client = TestClient(app)
    _login(client)
    r = client.get("/api/entity-tax-ledger?period=2026-08")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") in {"READY", "DEGRADED"}
    assert "items" in body
    for item in body["items"]:
        assert "entity_code" in item
        assert "project_id" not in item


def test_project_tax_analysis_strictly_scopes_to_one_project(seeded_app):
    from app.db import SessionLocal
    from app.main import app
    from app.models import Project

    db = SessionLocal()
    try:
        proj = db.query(Project).first()
        if proj is None:
            pytest.skip("no projects in seeded DB")
        project_id = int(proj.id)
        proj_code = proj.project_code or proj.code
    finally:
        db.close()

    client = TestClient(app)
    _login(client)
    r = client.get(f"/api/project-tax-analysis?project_id={project_id}&period=2026-08")
    assert r.status_code == 200, r.text
    items = r.json().get("items", [])
    assert len(items) <= 1
    if items:
        assert items[0]["project_id"] == project_id
        assert items[0]["project_code"] == proj_code
        assert "out_invoice_net" in items[0]
        assert "out_invoice_vat" in items[0]
        assert "in_invoice_net" in items[0]
        assert "in_invoice_vat" in items[0]
        assert "deductible_input_vat" in items[0]
        assert "real_cost" in items[0]
        assert "invoice_count" in items[0]


def test_project_tax_analysis_supports_entity_filter(seeded_app):
    from app.db import SessionLocal
    from app.main import app
    from app.models import Invoice

    db = SessionLocal()
    try:
        invoice = db.query(Invoice).first()
        if invoice is None:
            pytest.skip("no invoices in seeded DB")
        project_id = int(invoice.project_id)
        entity = invoice.entity_code
        period = invoice.period
    finally:
        db.close()

    client = TestClient(app)
    _login(client)
    r = client.get(
        f"/api/project-tax-analysis?project_id={project_id}&period={period}&entity={entity}"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity"] == entity
    for item in body["items"]:
        assert item["project_id"] == project_id
        assert item["entity"] == entity
        assert item["entity_code"] == entity


def test_project_tax_analysis_rejects_missing_project_id(seeded_app):
    from app.main import app

    client = TestClient(app)
    _login(client)
    r = client.get("/api/project-tax-analysis")
    assert r.status_code == 422


def test_project_tax_analysis_returns_404_for_unknown_project(seeded_app):
    from app.main import app

    client = TestClient(app)
    _login(client)
    r = client.get("/api/project-tax-analysis?project_id=9999999")
    assert r.status_code == 404, r.text


def test_project_tax_does_not_leak_other_projects(seeded_app):
    """Gate S0-02: same entity on multiple projects must remain project-scoped."""
    from app.db import SessionLocal
    from app.main import app
    from app.models import Invoice

    db = SessionLocal()
    try:
        candidates = (
            db.query(Invoice.entity_code, Invoice.period)
            .group_by(Invoice.entity_code, Invoice.period)
            .having(func.count(func.distinct(Invoice.project_id)) >= 2)
            .all()
        )
        if not candidates:
            pytest.skip("seeded data has no entity/period spanning multiple projects")
        entity, period = candidates[0]
        project_id = (
            db.query(Invoice.project_id)
            .filter(Invoice.entity_code == entity, Invoice.period == period)
            .first()[0]
        )

        expected = db.query(
            func.coalesce(func.sum(case((Invoice.direction == "out", Invoice.net), else_=0)), 0),
            func.coalesce(func.sum(case((Invoice.direction == "out", Invoice.vat), else_=0)), 0),
            func.coalesce(func.sum(case((Invoice.direction == "in", Invoice.net), else_=0)), 0),
            func.coalesce(func.sum(case((Invoice.direction == "in", Invoice.vat), else_=0)), 0),
            func.count(Invoice.id),
        ).filter(
            Invoice.project_id == project_id,
            Invoice.period == period,
            Invoice.entity_code == entity,
        ).one()

        all_projects_out_net = db.query(
            func.coalesce(func.sum(case((Invoice.direction == "out", Invoice.net), else_=0)), 0)
        ).filter(
            Invoice.period == period,
            Invoice.entity_code == entity,
        ).scalar()
    finally:
        db.close()

    client = TestClient(app)
    _login(client)
    r = client.get(
        f"/api/project-tax-analysis?project_id={project_id}&period={period}&entity={entity}"
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items, "selected project/entity/period should contain seeded invoice evidence"
    item = items[0]
    assert item["out_invoice_net"] == float(expected[0] or 0)
    assert item["out_invoice_vat"] == float(expected[1] or 0)
    assert item["in_invoice_net"] == float(expected[2] or 0)
    assert item["in_invoice_vat"] == float(expected[3] or 0)
    assert item["invoice_count"] == int(expected[4] or 0)
    if float(all_projects_out_net or 0) != float(expected[0] or 0):
        assert item["out_invoice_net"] != float(all_projects_out_net or 0), (
            "project tax analysis leaked another project's invoice totals"
        )


def test_legacy_project_filter_returns_warning_not_mixed_ledger(seeded_app):
    """v1.2 first-stage cutover: project_id on old endpoint returns no old result."""
    from app.db import SessionLocal
    from app.main import app
    from app.models import Project

    db = SessionLocal()
    try:
        proj = db.query(Project).first()
        if proj is None:
            pytest.skip("no projects in seeded DB")
        project_id = int(proj.id)
    finally:
        db.close()

    client = TestClient(app)
    _login(client)
    r = client.get(f"/api/tax-ledger?project_id={project_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deprecated"] is True
    assert body["status"] == "DEPRECATED"
    assert body["items"] == []
    assert body["total"] == 0
    assert "/api/project-tax-analysis" in body["deprecation_message"]
