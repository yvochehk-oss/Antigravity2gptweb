"""Classic HTML tax-ledger GET/POST boundary regression tests."""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select


def _login(client: TestClient, username: str = "admin", password: str = "TestPass12345!") -> None:
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert client.cookies.get("tax_session")
    assert client.cookies.get("tax_csrf")


def _snapshot(period: str) -> list[tuple[object, ...]]:
    from app.db import SessionLocal
    from app.models import TaxLedger

    with SessionLocal() as db:
        rows = db.execute(
            select(TaxLedger).where(TaxLedger.period == period).order_by(TaxLedger.entity_code, TaxLedger.id),
        ).scalars().all()
        return [
            (
                row.id, row.period, row.entity_code, row.output_vat, row.input_vat,
                row.vat_payable, row.revenue, row.real_cost, row.estimated_profit,
                row.estimated_cit, row.cit_note, row.generated,
            )
            for row in rows
        ]


def test_html_get_requires_login_and_does_not_rebuild(seeded_app, monkeypatch):
    from app.main import app

    client = TestClient(app)
    assert client.get("/tax-ledger", follow_redirects=False).status_code == 302
    assert client.post(
        "/tax-ledger/rebuild", data={"period": "2026-08"}, follow_redirects=False,
    ).status_code == 302

    _login(client)
    before = _snapshot("2026-08")

    def unexpected_rebuild(*args, **kwargs):
        raise AssertionError("GET /tax-ledger must be read-only")

    monkeypatch.setattr("app.routers.tax.rebuild_tax_ledger", unexpected_rebuild)
    response = client.get("/tax-ledger?period=2026-08")
    assert response.status_code == 200
    assert "实际法人月度税务管理台账" in response.text
    assert _snapshot("2026-08") == before


def test_html_post_requires_role_csrf_and_same_origin(seeded_app):
    from app.auth import hash_password
    from app.db import SessionLocal
    from app.main import app
    from app.models import User

    username = f"viewer_{uuid4().hex[:10]}"
    with SessionLocal() as db:
        db.add(User(
            username=username,
            password_hash=":".join(hash_password("ViewerPass12345!")),
            role="viewer",
            display_name="只读测试用户",
            active=True,
        ))
        db.commit()

    try:
        viewer = TestClient(app)
        _login(viewer, username, "ViewerPass12345!")
        response = viewer.post(
            "/tax-ledger/rebuild",
            data={"period": "2099-12", "_csrf": viewer.cookies.get("tax_csrf")},
            follow_redirects=False,
        )
        assert response.status_code == 403

        client = TestClient(app)
        _login(client)
        assert client.post(
            "/tax-ledger/rebuild",
            data={"period": "2099-12"},
            follow_redirects=False,
        ).status_code == 403
        assert client.post(
            "/tax-ledger/rebuild",
            data={"period": "2099-12", "_csrf": "wrong"},
            follow_redirects=False,
        ).status_code == 403
        response = client.post(
            "/tax-ledger/rebuild",
            data={"period": "2099-12", "_csrf": client.cookies.get("tax_csrf")},
            headers={"Origin": "https://attacker.example"},
            follow_redirects=False,
        )
        assert response.status_code == 403
    finally:
        with SessionLocal() as db:
            db.execute(delete(User).where(User.username == username))
            db.commit()


def test_html_post_rebuilds_requested_period_only(seeded_app):
    from app.db import SessionLocal
    from app.main import app
    from app.models import TaxLedger

    period = "2099-11"
    client = TestClient(app)
    _login(client)
    response = client.post(
        "/tax-ledger/rebuild",
        data={"period": period, "_csrf": client.cookies.get("tax_csrf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/tax-ledger?period={period}&rebuild=success"
    with SessionLocal() as db:
        rows = db.execute(select(TaxLedger).where(TaxLedger.period == period)).scalars().all()
        assert rows
        assert all(row.entity_code not in {"A", "B", "C", "D"} for row in rows)
        db.execute(delete(TaxLedger).where(TaxLedger.period == period))
        db.commit()


def test_html_post_failure_keeps_old_period_rows(seeded_app):
    from app.db import SessionLocal
    from app.main import app
    from app.models import Invoice, TaxLedger

    period = "2099-10"
    invoice_no = f"invalid-{uuid4().hex}"
    with SessionLocal() as db:
        db.add(TaxLedger(
            period=period, entity_code="A01",
            output_vat=Decimal("11.00"), input_vat=Decimal("2.00"),
            vat_payable=Decimal("9.00"), revenue=Decimal("100.00"),
            real_cost=Decimal("50.00"), estimated_profit=Decimal("50.00"),
            estimated_cit=Decimal("12.50"), cit_note="pre-existing", generated=True,
        ))
        db.add(Invoice(
            project_id=1, invoice_no=invoice_no, period=period,
            entity_code="INVALID-ENTITY", direction="out",
            counterparty_code="EXT-TEST", category="材料",
            net=Decimal("100.00"), vat=Decimal("13.00"),
            rate=Decimal("0.13"), deductible=True, note="failure regression",
        ))
        db.commit()
        before = _snapshot(period)

    try:
        client = TestClient(app)
        _login(client)
        response = client.post(
            "/tax-ledger/rebuild",
            data={"period": period, "_csrf": client.cookies.get("tax_csrf")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/tax-ledger?period={period}&rebuild=failed"
        assert _snapshot(period) == before
    finally:
        with SessionLocal() as db:
            db.execute(delete(Invoice).where(Invoice.invoice_no == invoice_no))
            db.execute(delete(TaxLedger).where(TaxLedger.period == period))
            db.commit()
