"""Integration tests for project data deletion endpoint."""
from __future__ import annotations

from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


def _login(client: TestClient, username: str = "admin") -> None:
    response = client.post(
        "/login",
        data={"username": username, "password": "TestPass12345!"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_delete_project_data_success(seeded_app):
    from app.db import SessionLocal
    from app.models import Project, Contract, Invoice, CashFlow
    from app.main import app

    with TestClient(app) as client:
        _login(client)

        # 1. Create a dedicated test project with data
        with SessionLocal() as db:
            p = Project(
                code="PRJ-TEST-DEL",
                project_code="PRJ-TEST-DEL",
                name="测试待删除项目",
                city="成都",
                contract_total=Decimal("10000000.00"),
                contract_amount=Decimal("10000000.00"),
            )
            db.add(p)
            db.commit()
            db.refresh(p)
            pid = p.id

            db.add(Contract(
                project_id=pid,
                contract_no="TEST-DELETE-001",
                category="subcontract",
                buyer_code="A01",
                seller_code="B01",
                amount=Decimal("1000000.00"),
                internal_trade=False,
            ))
            db.add(Invoice(
                project_id=pid,
                invoice_no="TEST-INV-999",
                direction="in",
                category="subcontract",
                entity_code="A01",
                counterparty_code="B01",
                net=Decimal("100000.00"),
                vat=Decimal("9000.00"),
                rate=Decimal("0.09"),
                deductible=True,
                period="2026-03",
            ))
            db.add(CashFlow(
                project_id=pid,
                amount=Decimal("50000.00"),
                counterparty_code="B01",
                direction="out",
                period="2026-03",
                transaction_date="2026-03-20",
                bank_reference="TEST-BANK-999",
                entity_code="A01",
            ))
            db.commit()

        # 2. Wrong password -> 400
        bad_resp = client.post(f"/api/projects/{pid}/delete-data", json={"password": "WrongPassword!"})
        assert bad_resp.status_code == 400
        assert "密码错误" in bad_resp.json()["detail"]

        # 3. Correct password -> 200
        response = client.post(f"/api/projects/{pid}/delete-data", json={"password": "TestPass12345!"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["project_id"] == pid
        assert data["deleted_counts"]["contracts"] >= 1
        assert data["deleted_counts"]["invoices"] >= 1
        assert data["deleted_counts"]["cashflows"] >= 1

        # 4. Verify in database that project records and the project row itself are wiped
        with SessionLocal() as db:
            cnt_projects = db.execute(text("SELECT count(*) FROM projects WHERE id = :pid"), {"pid": pid}).scalar()
            cnt_contracts = db.execute(text("SELECT count(*) FROM contracts WHERE project_id = :pid"), {"pid": pid}).scalar()
            cnt_invoices = db.execute(text("SELECT count(*) FROM invoices WHERE project_id = :pid"), {"pid": pid}).scalar()
            cnt_cashflows = db.execute(text("SELECT count(*) FROM cashflows WHERE project_id = :pid"), {"pid": pid}).scalar()
            assert cnt_projects == 0
            assert cnt_contracts == 0
            assert cnt_invoices == 0
            assert cnt_cashflows == 0


def test_delete_project_data_not_found(seeded_app):
    from app.main import app
    with TestClient(app) as client:
        _login(client)
        response = client.post("/api/projects/999999/delete-data", json={"password": "TestPass12345!"})
        assert response.status_code == 404
