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


def test_api_rebuild_returns_stable_contract_for_admin_and_operator(seeded_app, monkeypatch):
    """The JSON command is explicit, period-scoped, and invokes the engine once."""
    import app.routers.tax as tax_router
    from app.main import app

    original_rebuild = tax_router.rebuild_tax_ledger
    calls: list[str] = []

    def counted_rebuild(db, period):
        calls.append(period)
        return original_rebuild(db, period)

    monkeypatch.setattr(tax_router, "rebuild_tax_ledger", counted_rebuild)
    periods_and_users = (("2098-01", "admin"), ("2098-12", "operator"))
    try:
        for period, username in periods_and_users:
            client = TestClient(app)
            _login(client, username)
            csrf_header = "X-CSRF-Token" if username == "admin" else "X-XSRF-TOKEN"
            response = client.post(
                "/api/tax-ledger/rebuild",
                json={"period": period},
                headers={csrf_header: client.cookies.get("tax_csrf")},
            )
            assert response.status_code == 200
            assert response.json() == {
                "status": "success",
                "period": period,
                "row_count": len(_snapshot(period)),
            }
            # A no-input period still has one deterministic zero row per active
            # legal entity; no demo seed or placeholder entity is introduced.
            assert all(
                row[3] == Decimal("0")
                and row[4] == Decimal("0")
                and row[7] == Decimal("0")
                and row[8] == Decimal("0")
                and row[9] == Decimal("0")
                for row in _snapshot(period)
            )
        assert calls == [period for period, _ in periods_and_users]
    finally:
        from app.db import SessionLocal
        from app.models import TaxLedger

        with SessionLocal() as db:
            db.execute(
                delete(TaxLedger).where(
                    TaxLedger.period.in_([period for period, _ in periods_and_users]),
                ),
            )
            db.commit()


def test_api_rebuild_uses_persisted_deterministic_values_and_numeric_rounding(seeded_app):
    """A normal non-zero period keeps the calculator's Numeric(18,2) values."""
    from app.db import SessionLocal
    from app.main import app
    from app.models import Invoice, RealCost, TaxLedger

    period = "2098-06"
    invoice_no = f"api-rounding-{uuid4().hex}"
    cost_note = f"api-rounding-cost-{uuid4().hex}"
    with SessionLocal() as db:
        db.add(Invoice(
            project_id=1, invoice_no=invoice_no, period=period,
            entity_code="A01", direction="out",
            counterparty_code="EXT-TF", category="建筑服务",
            net=Decimal("100.004"), vat=Decimal("13.006"),
            rate=Decimal("0.09"), deductible=False, note="API normal value",
        ))
        db.add(RealCost(
            project_id=1, entity_code="A01", counterparty_code="EXT-TF",
            category="项目管理", subcategory="rounding",
            period=period, amount=Decimal("40.005"), external_cash=True,
            note=cost_note,
        ))
        db.commit()

    try:
        client = TestClient(app)
        _login(client)
        response = client.post(
            "/api/tax-ledger/rebuild",
            json={"period": period},
            headers={"X-CSRF-Token": client.cookies.get("tax_csrf")},
        )
        assert response.status_code == 200
        assert response.json()["period"] == period
        with SessionLocal() as db:
            row = db.execute(
                select(TaxLedger).where(
                    TaxLedger.period == period,
                    TaxLedger.entity_code == "A01",
                ),
            ).scalar_one()
            assert row.output_vat == Decimal("13.01")
            assert row.revenue == Decimal("100.00")
            assert row.real_cost == Decimal("40.01")
            assert row.estimated_profit == Decimal("59.99")
            assert row.estimated_cit == Decimal("15.00")
    finally:
        with SessionLocal() as db:
            db.execute(delete(Invoice).where(Invoice.invoice_no == invoice_no))
            db.execute(delete(RealCost).where(RealCost.note == cost_note))
            db.execute(delete(TaxLedger).where(TaxLedger.period == period))
            db.commit()


def test_api_rebuild_validates_json_period_and_does_not_call_engine(seeded_app, monkeypatch):
    import app.routers.tax as tax_router
    from app.main import app

    def unexpected_rebuild(*args, **kwargs):
        raise AssertionError("invalid JSON command must not call the calculation engine")

    monkeypatch.setattr(tax_router, "rebuild_tax_ledger", unexpected_rebuild)
    client = TestClient(app)
    _login(client)
    headers = {"X-CSRF-Token": client.cookies.get("tax_csrf")}
    for payload in (
        {"period": "2099-00"},
        {"period": "2099-13"},
        {"period": "2099-1"},
        {"period": "2099/12"},
        {},
        {"period": "2099-12", "unexpected": True},
    ):
        response = client.post("/api/tax-ledger/rebuild", json=payload, headers=headers)
        assert response.status_code == 422, payload


def test_api_rebuild_rbac_csrf_and_cross_origin_boundaries(seeded_app):
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
        anonymous = TestClient(app)
        assert anonymous.post(
            "/api/tax-ledger/rebuild", json={"period": "2098-02"},
        ).status_code == 401

        viewer = TestClient(app)
        _login(viewer, username, "ViewerPass12345!")
        assert viewer.post(
            "/api/tax-ledger/rebuild",
            json={"period": "2098-02"},
            headers={"X-CSRF-Token": viewer.cookies.get("tax_csrf")},
        ).status_code == 403

        client = TestClient(app)
        _login(client)
        assert client.post(
            "/api/tax-ledger/rebuild", json={"period": "2098-02"},
        ).status_code == 403
        assert client.post(
            "/api/tax-ledger/rebuild",
            json={"period": "2098-02"},
            headers={"X-CSRF-Token": "wrong"},
        ).status_code == 403
        assert client.post(
            "/api/tax-ledger/rebuild",
            json={"period": "2098-02"},
            headers={
                "X-CSRF-Token": client.cookies.get("tax_csrf"),
                "Origin": "https://attacker.example",
            },
        ).status_code == 403
    finally:
        with SessionLocal() as db:
            db.execute(delete(User).where(User.username == username))
            db.commit()


def test_api_get_remains_read_only_and_does_not_rebuild(seeded_app, monkeypatch):
    import app.routers.tax as tax_router
    from app.main import app

    period = "2098-03"
    before = _snapshot(period)

    def unexpected_rebuild(*args, **kwargs):
        raise AssertionError("GET /api/tax-ledger must remain read-only")

    monkeypatch.setattr(tax_router, "rebuild_tax_ledger", unexpected_rebuild)
    client = TestClient(app)
    _login(client)
    response = client.get(f"/api/tax-ledger?period={period}")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert _snapshot(period) == before


def test_api_rebuild_calculation_failure_is_explicit_and_rolls_back(seeded_app, monkeypatch):
    """A failed calculation is not reported as success and preserves old rows."""
    import app.routers.tax as tax_router
    from app.db import SessionLocal
    from app.main import app
    from app.models import Invoice, TaxLedger

    period = "2098-04"
    invoice_no = f"invalid-api-{uuid4().hex}"
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

    original_rebuild = tax_router.rebuild_tax_ledger
    calls: list[str] = []

    def counted_rebuild(db, requested_period):
        calls.append(requested_period)
        return original_rebuild(db, requested_period)

    monkeypatch.setattr(tax_router, "rebuild_tax_ledger", counted_rebuild)
    try:
        client = TestClient(app)
        _login(client)
        response = client.post(
            "/api/tax-ledger/rebuild",
            json={"period": period},
            headers={"X-CSRF-Token": client.cookies.get("tax_csrf")},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "税务台账输入或税务规则无效"
        assert calls == [period]
        assert _snapshot(period) == before
    finally:
        with SessionLocal() as db:
            db.execute(delete(Invoice).where(Invoice.invoice_no == invoice_no))
            db.execute(delete(TaxLedger).where(TaxLedger.period == period))
            db.commit()


def test_api_rebuild_unexpected_failure_is_500_and_rolls_back(seeded_app, monkeypatch):
    import app.routers.tax as tax_router
    from app.db import SessionLocal
    from app.main import app
    from app.models import TaxLedger

    period = "2098-05"
    with SessionLocal() as db:
        db.add(TaxLedger(
            period=period, entity_code="A01",
            output_vat=Decimal("1.00"), input_vat=Decimal("0.00"),
            vat_payable=Decimal("1.00"), revenue=Decimal("10.00"),
            real_cost=Decimal("0.00"), estimated_profit=Decimal("10.00"),
            estimated_cit=Decimal("2.50"), cit_note="pre-existing", generated=True,
        ))
        db.commit()
        before = _snapshot(period)

    calls: list[str] = []

    def broken_rebuild(db, requested_period):
        calls.append(requested_period)
        db.add(TaxLedger(
            period=requested_period, entity_code="A02",
            output_vat=Decimal("9.00"), input_vat=Decimal("0.00"),
            vat_payable=Decimal("9.00"), revenue=Decimal("90.00"),
            real_cost=Decimal("0.00"), estimated_profit=Decimal("90.00"),
            estimated_cit=Decimal("22.50"), cit_note="uncommitted", generated=True,
        ))
        raise RuntimeError("calculation sentinel")

    monkeypatch.setattr(tax_router, "rebuild_tax_ledger", broken_rebuild)
    try:
        client = TestClient(app)
        _login(client)
        response = client.post(
            "/api/tax-ledger/rebuild",
            json={"period": period},
            headers={"X-CSRF-Token": client.cookies.get("tax_csrf")},
        )
        assert response.status_code == 500
        assert response.json()["detail"] == "税务台账重建失败"
        assert calls == [period]
        assert _snapshot(period) == before
    finally:
        with SessionLocal() as db:
            db.execute(delete(TaxLedger).where(TaxLedger.period == period))
            db.commit()
