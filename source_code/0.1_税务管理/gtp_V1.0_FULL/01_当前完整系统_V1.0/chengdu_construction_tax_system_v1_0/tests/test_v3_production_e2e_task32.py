from __future__ import annotations
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.middleware as middleware
from app.db import engine
from app.routers.v3_canonical import get_v3_db
from app.wiring import create_app
from scripts.v3.idp_direct_v3_gate_27_support import contract_submission, invoice_submission, party
from scripts.v3.payment_direct_v3_gate_28 import submission as payment_submission

pytestmark = pytest.mark.skipif(engine.dialect.name != "postgresql", reason="Task32 Production E2E is PostgreSQL-only")


def _run_http(monkeypatch, builder):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(middleware, "current_user_from_request", lambda request: SimpleNamespace(id=1, username="task32", role="admin", is_active=True))
    conn = engine.connect(); outer = conn.begin()
    session = Session(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    app = create_app(); app.dependency_overrides[get_v3_db] = lambda: session
    try:
        with TestClient(app) as client:
            return builder(client, session)
    finally:
        session.close()
        if outer.is_active: outer.rollback()
        conn.close()


def test_invoice_production_http_e2e(monkeypatch):
    def scenario(client, session):
        token=uuid4().hex[:8].upper(); seller,st=party(session,token,"I_S"); buyer,bt=party(session,token,"I_B")
        req=invoice_submission(token,"HTTP",seller,st,buyer,bt)
        response=client.post("/api/v3/idp/direct",json=req.model_dump(mode="json"))
        assert response.status_code==200, response.text
        body=response.json(); assert body["document_type"]=="invoice"; assert body["validation_status"]=="VALID"; assert body["outcome"]=="COMPLETED"
    _run_http(monkeypatch, scenario)


def test_payment_production_http_e2e(monkeypatch):
    def scenario(client, session):
        token=uuid4().hex[:8].upper(); payer,pt=party(session,token,"P_R"); payee,qt=party(session,token,"P_E")
        req=payment_submission(token,"HTTP",payer,pt,payee,qt)
        response=client.post("/api/v3/idp/direct",json=req.model_dump(mode="json"))
        assert response.status_code==200, response.text
        body=response.json(); assert body["document_type"] in {"payment","bank_receipt"}; assert body["validation_status"]=="VALID"; assert body["payment_evidence"] is not None
    _run_http(monkeypatch, scenario)


def test_contract_review_production_http_e2e(monkeypatch):
    def scenario(client, session):
        token=uuid4().hex[:8].upper(); a,at=party(session,token,"C_A"); b,bt=party(session,token,"C_B")
        req=contract_submission(token,"HTTP",a,at,b,bt,reverse=True)
        response=client.post("/api/v3/idp/direct",json=req.model_dump(mode="json"))
        assert response.status_code==200, response.text
        body=response.json(); assert body["document_type"]=="contract"; assert body["validation_status"]=="NEEDS_REVIEW"; assert body["contract_roles"] is not None
    _run_http(monkeypatch, scenario)
