from __future__ import annotations
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.middleware as middleware
import app.routers.v3_canonical as v3_router
from app.integration.idp_canonical.direct_v3_service import DirectV3ProductionError
from app.routers.v3_canonical import get_v3_db, router
from app.wiring import create_app

EXPECTED_PATHS = {
    "/api/v3/boss/projects/{project_id}/snapshot",
    "/api/v3/boss/projects/{project_id}/finance",
    "/api/v3/boss/projects/{project_id}/four-flow",
    "/api/v3/boss/projects/{project_id}/tax",
    "/api/v3/boss/projects/{project_id}/evidence-quality",
    "/api/v3/boss/projects/{project_id}/rag-context",
    "/api/v3/idp/direct",
    "/api/v3/system/status",
}


def _bare_client(monkeypatch, fake_service):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_v3_db] = lambda: object()
    monkeypatch.setattr(v3_router, "V3BossService", fake_service)
    return TestClient(app)


def test_task32_routes_are_registered_and_no_control_write_routes():
    paths = {route.path for route in router.routes}
    assert EXPECTED_PATHS <= paths
    assert not any("seal" in path or "cutover" in path for path in paths)
    methods = {route.path: route.methods for route in router.routes}
    assert methods["/api/v3/system/status"] == {"GET"}
    assert methods["/api/v3/idp/direct"] == {"POST"}


def test_v3_routes_inherit_global_authentication(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(middleware, "current_user_from_request", lambda request: None)
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/v3/system/status")
    assert response.status_code == 401
    assert response.json()["detail"] == "请先登录"


class FakeBoss:
    calls = []
    def __init__(self, db): self.db = db
    def snapshot(self, project_id, **kwargs): self.calls.append(("snapshot", project_id, kwargs)); return {"kind":"snapshot"}
    def finance(self, project_id): self.calls.append(("finance", project_id)); return {"kind":"finance"}
    def four_flow(self, project_id): self.calls.append(("four", project_id)); return {"kind":"four"}
    def tax(self, project_id, **kwargs): self.calls.append(("tax", project_id, kwargs)); return {"kind":"tax"}
    def evidence_quality(self, project_id): self.calls.append(("quality", project_id)); return {"kind":"quality"}
    def rag_context(self, project_id, **kwargs): self.calls.append(("rag", project_id, kwargs)); return {"kind":"rag"}
    def system_status(self): self.calls.append(("status",)); return {"ready":True}


def test_boss_endpoints_are_thin_delegates(monkeypatch):
    FakeBoss.calls = []
    client = _bare_client(monkeypatch, FakeBoss)
    assert client.get("/api/v3/boss/projects/7/finance").json() == {"kind":"finance"}
    assert client.get("/api/v3/boss/projects/7/four-flow").json() == {"kind":"four"}
    assert client.get("/api/v3/boss/projects/7/evidence-quality").json() == {"kind":"quality"}
    assert client.get("/api/v3/boss/projects/7/rag-context?scope=tax").json() == {"kind":"rag"}
    assert client.get("/api/v3/boss/projects/7/tax?reporting_party_id=9&period=2026-09-01").json() == {"kind":"tax"}
    assert client.get("/api/v3/boss/projects/7/snapshot?reporting_party_id=9&period=2026-09-01&rag_scope=tax").json() == {"kind":"snapshot"}
    assert client.get("/api/v3/system/status").json() == {"ready":True}
    tax_call = next(x for x in FakeBoss.calls if x[0] == "tax")
    assert tax_call[2] == {"reporting_party_id":9, "tax_period":date(2026,9,1)}


def test_idp_endpoint_invokes_task27_commit_true(monkeypatch):
    captured = {}
    class FakeDirect:
        def __init__(self, db): captured["db"] = db
        def process(self, request, *, commit=True):
            captured["request"] = request
            captured["commit"] = commit
            raise DirectV3ProductionError("TEST_STOP", "delegated")
    monkeypatch.setattr(v3_router, "DirectV3IngestService", FakeDirect)
    app = FastAPI(); app.include_router(router); app.dependency_overrides[get_v3_db] = lambda: object()
    body = {
      "intake": {
        "source_system":"IDP","source_document_id":"D1","source_extraction_id":"E1",
        "document_sha256":"a"*64,"document_type":"invoice","review_status":"approved",
        "data":{"invoice_no":"I1","seller":{"name":"A","tax_id":"913300000000000001"},"buyer":{"name":"B","tax_id":"913300000000000002"},"amount_excluding_tax":"1","tax_amount":"0","amount_including_tax":"1","currency":"CNY"}
      },
      "invoice_evidence": {
        "source_system":"IDP","source_document_id":"D1","source_extraction_id":"E1","document_sha256":"a"*64,
        "document":{"filename":"a.pdf","mime_type":"application/pdf","validation_status":"VALIDATED","validated_by":"u","validation_reason":"ok"},
        "invoice_status":"VALID","lines":[{"line_no":1,"item_name":"x","net_amount":"1","vat_amount":"0","tax_rate":"0"}],"tax_rules":{"rule_version":"v","reviewed_by":"u","allowed_tax_rates":["0"]}
      }
    }
    response = TestClient(app).post("/api/v3/idp/direct", json=body)
    assert response.status_code == 409
    assert captured["commit"] is True
    assert captured["request"].intake.source_document_id == "D1"


def test_task32_source_has_no_legacy_business_query_or_business_math():
    router_src = Path(v3_router.__file__).read_text(encoding="utf-8")
    import app.services.v3_boss_service as svc
    service_src = Path(svc.__file__).read_text(encoding="utf-8")
    forbidden = ["CashFlow", "Invoice,", "Contract,", "RealCost", "Budget", "Progress", "matching_rows", "project_summary"]
    assert all(token not in router_src + service_src for token in forbidden)
    assert "DirectV3IngestService(db).process(request, commit=True)" in router_src
    assert "CanonicalProjectFinance" in service_src
    assert "CanonicalFourFlow" in service_src
    assert "CanonicalProjectTax" in service_src
    assert "build_canonical_context" in service_src


def test_task32_adds_no_migration_99():
    root = Path(__file__).resolve().parents[1]
    versions = root / "alembic" / "versions"
    assert not any("99_" in p.name for p in versions.glob("*.py"))
