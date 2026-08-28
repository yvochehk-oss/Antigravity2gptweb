"""Integration contracts for the project counterparty aggregation endpoint.

The Tax system must render every unit a project actually references, regardless
of whether that unit lives in ``entities`` (canonical 26-unit master) or
``external_parties`` (RAG-confirmed counterparty master).  This test seeds a
disposable PostgreSQL with the exact pattern the project uses in production:

  * Contract.buyer_code / seller_code
  * Invoice.entity_code / counterparty_code
  * CashFlow.entity_code / counterparty_code
  * RealCost.entity_code / counterparty_code
  * Fulfillment.counterparty_code

…then asserts that ``/api/projects/{pid}/counterparties`` lists every distinct
code, classifies canonical vs external, and aggregates the numeric columns
without fabrication.

Requires ``TEST_DATABASE_URL`` pointing at a disposable PostgreSQL (enforced by
``conftest.require_test_database``).  The SQLAlchemy engine import in
``app.db`` needs a working ``psycopg2`` driver.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient


def _login(client: TestClient, username: str = "admin") -> None:
    response = client.post(
        "/login",
        data={"username": username, "password": "TestPass12345!"},
        follow_redirects=False,
    )
    assert response.status_code == 302


@pytest.fixture
def seeded_counterparty_project(seeded_app):
    """Add an RAG-style counterparty to project 1 and return the project id."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import (
        CashFlow,
        Contract,
        ExternalParty,
        Fulfillment,
        Invoice,
        Project,
        RealCost,
    )

    with SessionLocal() as db:
        project = db.get(Project, 1)
        assert project is not None

        ext = db.execute(
            select(ExternalParty).where(ExternalParty.code == "EXT-TF-001")
        ).scalars().first()
        if ext is None:
            ext = ExternalParty(
                code="EXT-TF-001",
                name="成都土方供应有限公司",
                short_name="成都土方",
                kind="rag_confirmed",
                tax_id="91510100MAEXT001",
                active=True,
            )
            db.add(ext)
            db.flush()

        # Contract referencing the external party as seller
        db.add(Contract(
            project_id=project.id,
            contract_no="HT-2026-001",
            buyer_code="A08",
            seller_code="EXT-TF-001",
            category="土方工程",
            amount=Decimal("1200000"),
            internal_trade=False,
            note="RAG 抽取的外部合同",
        ))

        db.add(Invoice(
            project_id=project.id,
            invoice_no="FP-2026-001",
            period="2026-08",
            entity_code="A08",
            direction="in",
            counterparty_code="EXT-TF-001",
            category="土方",
            net=Decimal("100000"),
            vat=Decimal("9000"),
            rate=Decimal("0.09"),
            deductible=True,
        ))

        db.add(CashFlow(
            project_id=project.id,
            entity_code="A08",
            counterparty_code="EXT-TF-001",
            direction="out",
            amount=Decimal("90000"),
            period="2026-08",
            note="土方预付款",
        ))

        db.add(RealCost(
            project_id=project.id,
            entity_code="A08",
            counterparty_code="EXT-TF-001",
            category="土方",
            subcategory="挖填",
            period="2026-08",
            amount=Decimal("80000"),
            external_cash=True,
            note="外部机械 + 人员",
        ))

        db.add(Fulfillment(
            project_id=project.id,
            counterparty_code="EXT-TF-001",
            kind="external_construction",
            category="土方",
            quantity=Decimal("1000"),
            amount=Decimal("80000"),
            evidence_complete=True,
            note="履约确认",
        ))

        db.commit()
        return project.id


def test_counterparties_lists_canonical_and_external_codes(
    seeded_app, seeded_counterparty_project
):
    pid = seeded_counterparty_project

    from app.main import app

    with TestClient(app) as client:
        _login(client)
        response = client.get(f"/api/projects/{pid}/counterparties")
        assert response.status_code == 200, response.text

    body = response.json()
    codes = [item["party_code"] for item in body["items"]]
    assert "A08" in codes
    assert "EXT-TF-001" in codes

    by_code = {item["party_code"]: item for item in body["items"]}
    a08 = by_code["A08"]
    ext = by_code["EXT-TF-001"]

    assert a08["isInternal"] is True
    assert a08["source"] == "entities"
    assert ext["isInternal"] is False
    assert ext["source"] == "external_parties"
    assert ext["party_name"] == "成都土方供应有限公司"

    # External party aggregate sums every flow that references its code
    assert ext["contract_count"] == 1
    assert ext["contract_amount"] == pytest.approx(1200000.0)
    assert ext["invoice_in_count"] == 1
    assert ext["invoice_in_net"] == pytest.approx(100000.0)
    assert ext["invoice_in_vat"] == pytest.approx(9000.0)
    assert ext["cashflow_out_count"] == 1
    assert ext["cashflow_out_amount"] == pytest.approx(90000.0)
    assert ext["real_cost_amount"] == pytest.approx(80000.0)
    assert ext["fulfillment_count"] == 1
    assert ext["fulfillment_amount"] == pytest.approx(80000.0)


def test_counterparties_sorts_canonical_before_external(
    seeded_app, seeded_counterparty_project
):
    pid = seeded_counterparty_project

    from app.main import app

    with TestClient(app) as client:
        _login(client)
        response = client.get(f"/api/projects/{pid}/counterparties")
        assert response.status_code == 200

    codes = [item["party_code"] for item in response.json()["items"]]
    # Every canonical A/B/C/D code must come before every EXT-* code
    from app.domain.entities import CANONICAL_ENTITY_CODES

    canonical_idx = [codes.index(c) for c in codes if c in CANONICAL_ENTITY_CODES]
    external_idx = [codes.index(c) for c in codes if c.startswith("EXT-")]
    if canonical_idx and external_idx:
        assert max(canonical_idx) < min(external_idx)


def test_counterparties_rejects_unknown_project(seeded_app):
    from app.main import app

    with TestClient(app) as client:
        _login(client)
        response = client.get("/api/projects/9999999/counterparties")
        assert response.status_code == 404