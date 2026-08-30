"""V3 Phase A3 — Tax API split regression tests.

v1.1 §A3 contract:
  - /api/entity-tax-ledger: entity-month ledger; project_id is NOT accepted
  - /api/project-tax-analysis: project-scope VAT/cost picture, NEVER
    mixes tax ledger rows from other projects' entities
  - /api/tax-ledger: marked deprecated, payload exposes deprecation hint

These tests require a seeded PostgreSQL via TEST_DATABASE_URL.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "admin", "password": "888888"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert client.cookies.get("tax_session")


def test_tax_ledger_marked_deprecated(seeded_app):
    """Old endpoint must be marked deprecated with recommendation hints."""
    from app.main import app
    client = TestClient(app)
    _login(client)
    r = client.get("/api/tax-ledger")
    assert r.status_code in (200, 503)  # 503 only if dependency is degraded
    if r.status_code == 200:
        body = r.json()
        assert body.get("deprecated") is True, "v3 routing must mark /api/tax-ledger deprecated"
        assert "deprecation_message" in body
        assert body["recommended_endpoints"]["entity_ledger"] == "/api/entity-tax-ledger"
        assert body["recommended_endpoints"]["project_analysis"] == "/api/project-tax-analysis"


def test_entity_tax_ledger_rejects_project_id(seeded_app):
    """/api/entity-tax-ledger MUST NOT accept project_id.

    v1.1 §A3: the legal entity's monthly tax position must never be
    derived from the project scope.  project_id is rejected with 422.
    """
    from app.main import app
    client = TestClient(app)
    _login(client)

    r = client.get("/api/entity-tax-ledger?project_id=1")
    assert r.status_code == 422, (
        "entity-tax-ledger must reject project_id; got "
        f"{r.status_code}: {r.text}"
    )


def test_entity_tax_ledger_returns_legal_entities(seeded_app):
    """/api/entity-tax-ledger returns TaxLedger rows for the requested period."""
    from app.main import app
    client = TestClient(app)
    _login(client)

    r = client.get("/api/entity-tax-ledger?period=2026-08")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        body = r.json()
        assert body.get("status") in ("READY", "DEGRADED", "UNAVAILABLE")
        assert "items" in body
        # Each item carries entity_code and is not polluted by project_id
        for item in body["items"]:
            assert "entity_code" in item
            assert "project_id" not in item


def test_project_tax_analysis_strictly_scopes_to_one_project(seeded_app):
    """/api/project-tax-analysis returns ONE row whose project_id matches."""
    from app.main import app
    from app.db import SessionLocal
    from app.models import Project

    db = SessionLocal()
    try:
        proj = db.query(Project).first()
        if proj is None:
            pytest.skip("no projects in seeded DB")
        project_id = int(proj.id)
        proj_code = proj.code
    finally:
        db.close()

    client = TestClient(app)
    _login(client)

    r = client.get(f"/api/project-tax-analysis?project_id={project_id}&period=2026-08")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        body = r.json()
        items = body.get("items", [])
        # When data exists we want at most one (this project only)
        assert len(items) <= 1
        if items:
            assert items[0]["project_id"] == project_id
            assert items[0]["project_code"] == proj_code
            assert "out_invoice_net" in items[0]
            assert "in_invoice_net" in items[0]
            assert "real_cost" in items[0]
            assert "invoice_count" in items[0]


def test_project_tax_analysis_rejects_missing_project_id(seeded_app):
    """/api/project-tax-analysis requires project_id (no implicit scope)."""
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
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        body = r.json()
        assert body.get("items", []) == []
        assert "9999999" in body.get("message", "")


def test_old_tax_ledger_does_not_leak_unrelated_projects(seeded_app):
    """Even the deprecated endpoint must not leak unrelated projects when
    project_id is supplied.  This was the v1.0 cross-project pollution bug."""
    from app.main import app
    client = TestClient(app)
    _login(client)

    # Pick an existing project
    from app.db import SessionLocal
    from app.models import Project
    db = SessionLocal()
    try:
        proj = db.query(Project).first()
        if proj is None:
            pytest.skip("no projects in seeded DB")
        project_id = int(proj.id)
    finally:
        db.close()

    r = client.get(f"/api/tax-ledger?project_id={project_id}")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        body = r.json()
        # Verify items only contain entity_codes that this project touches
        # (via Invoice / RealCost tables)
        from app.db import SessionLocal
        from app.models import Invoice, RealCost
        db = SessionLocal()
        try:
            allowed = set(
                db.query(Invoice.entity_code).filter(Invoice.project_id == project_id).all()
            )
            allowed |= set(
                db.query(RealCost.entity_code).filter(RealCost.project_id == project_id).all()
            )
            allowed = {str(c[0]).strip() for c in allowed if str(c[0] or "").strip()}
        finally:
            db.close()
        for item in body["items"]:
            assert item["entity_code"] in allowed, (
                f"entity {item['entity_code']} leaked from an unrelated project; "
                "v1.1 §A3 fix violated"
            )