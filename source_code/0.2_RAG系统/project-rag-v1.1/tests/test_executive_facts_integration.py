"""Facts Provider integration tests for the executive mobile router.

These tests verify the contract between ``facts_provider.FactsProvider`` and
the ``/api/v1/executive/*`` endpoints:

* When FactsProvider returns a complete AVAILABLE response, the cockpit
  endpoint reports ``_meta.data_source == "live"`` and the KPI block is
  populated with real metric values (no fabricated defaults).
* When FactsProvider returns a DEGRADED response, the cockpit endpoint
  reports ``_meta.data_source == "unavailable"`` and the KPI block
  contains ``None`` for every metric.
* When a subset of projects has Facts and others don't, the projects
  endpoint surfaces per-project ``facts_available`` flags without
  crashing or inventing numbers.
* The AI chat endpoint prepends a deterministic facts summary block so the
  language model cannot fabricate financial figures.

The router instantiates :class:`FactsProvider` directly with the request's
SQLAlchemy session; we patch ``FactsProvider.get_facts`` so every test
runs against the disposable test database without depending on a real
``analytics_project_full`` view.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from facts_provider.facts_provider import FactsResponse, MetricValue

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

_TEST_JWT_SECRET = "test-suite-jwt-secret-32-bytes-or-more"


def _issue_admin_jwt() -> str:
    payload = {
        "sub": "1",
        "username": "admin.user",
        "role": "admin",
        "display_name": "Admin User",
        "type": "access",
    }
    return jwt.encode(payload, _TEST_JWT_SECRET, algorithm="HS256")


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _unique_project_code(prefix: str = "P") -> str:
    """Generate a unique project_code so tests don't collide on UNIQUE indexes."""

    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Factories: build complete and degraded FactsResponse objects deterministically
# ---------------------------------------------------------------------------

def _available_facts(
    project_code: str,
    *,
    recognized_revenue: float = 100.0,
    real_profit: float = 40.0,
    collection_rate: float = 0.8,
    health_score: float = 82.0,
) -> FactsResponse:
    """Return a fully-populated FactsResponse mirroring ``analytics_project_full``."""

    metrics = {
        "recognized_revenue": MetricValue(recognized_revenue, "1.3", "CNY"),
        "real_project_cost": MetricValue(60.0, "1.0", "CNY"),
        "real_profit": MetricValue(real_profit, "2.1", "CNY"),
        "collected_amount": MetricValue(80.0, "1.0", "CNY"),
        "unpaid_amount": MetricValue(20.0, "1.0", "CNY"),
        "collection_rate": MetricValue(collection_rate, "1.2", "percent"),
        "eac_revenue": MetricValue(110.0, "1.6", "CNY"),
        "eac_cost": MetricValue(70.0, "1.6", "CNY"),
        "eac_profit": MetricValue(40.0, "1.6", "CNY"),
        "eac_margin": MetricValue(0.3636, "1.6", "percent"),
        "cash_gap_30d": MetricValue(5.0, "1.0", "CNY"),
        "tax_burden_rate": MetricValue(0.03, "1.0", "percent"),
        "cost_variance": MetricValue(2.0, "1.0", "CNY"),
        "health_score": MetricValue(health_score, "1.0", "score"),
    }
    return FactsResponse(
        project_code=project_code,
        as_of="2026-08-20T00:00:00+00:00",
        facts_version="f_test_available",
        metrics=metrics,
        status="AVAILABLE",
        facts_available=True,
        reason=None,
        source="analytics_project_full",
    )


def _degraded_facts(
    project_code: str,
    reason: str = "analytics_project_full unavailable: OperationalError",
) -> FactsResponse:
    """Return a DEGRADED FactsResponse with empty metrics."""

    return FactsResponse(
        project_code=project_code,
        as_of="2026-08-20T00:00:00+00:00",
        facts_version="f_test_degraded",
        metrics={},
        status="DEGRADED",
        facts_available=False,
        reason=reason,
        source="analytics_project_full",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_JWT_SECRET)
    yield _TEST_JWT_SECRET


@pytest.fixture
def client(jwt_secret):
    """Build a TestClient wrapping the production ``app.main.app``."""

    from app.db import init_db
    from app.main import app

    init_db()
    return TestClient(app)


@pytest.fixture
def admin_token(jwt_secret) -> str:
    return _issue_admin_jwt()


@pytest.fixture(autouse=True)
def _isolate_projects(client, admin_token):
    """Truncate ``projects`` and ``documents`` before each test in this module.

    Other test modules (e.g. ``test_data_source_meta``) insert rows that this
    module's cockpit-summary assertions would otherwise aggregate. Cleaning
    the table here keeps each facts-integration test hermetic without
    coupling to the test execution order.
    """

    from app.db import SessionLocal

    with SessionLocal() as db:
        try:
            db.execute(text("DELETE FROM documents"))
        except Exception:
            pass
        try:
            db.execute(text("DELETE FROM projects"))
        except Exception:
            pass
        db.commit()
    yield


@pytest.fixture
def facts_table(client, admin_token):
    """Create one project row and return its id (cockpit needs >=1 project)."""

    from app.db import SessionLocal

    code = _unique_project_code()
    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO projects (project_code, name, status, external_system, "
                "external_project_id, contract_amount, start_date, expected_end_date, "
                "location, project_type, note, created_at, updated_at) VALUES "
                "(:code, :name, 'ACTIVE', 'construction-tax', :code, 0.0, '', '', "
                "'', '', '', '', '')"
            ),
            {"code": code, "name": "Test project"},
        )
        db.commit()
        row = db.execute(
            text("SELECT id, project_code FROM projects WHERE project_code = :code"),
            {"code": code},
        ).first()
    assert row is not None
    return {"id": int(row[0]), "project_code": row[1]}


# ---------------------------------------------------------------------------
# Cockpit summary tests
# ---------------------------------------------------------------------------

def test_cockpit_summary_facts_available(client, admin_token, facts_table):
    """When FactsProvider returns AVAILABLE, cockpit flips to ``data_source=live``."""

    def _fake_get_facts(self, code: str, *args: Any, **kwargs: Any) -> FactsResponse:
        return _available_facts(code)

    with patch.object(
        __import__("facts_provider.facts_provider", fromlist=["FactsProvider"]).FactsProvider,
        "get_facts",
        _fake_get_facts,
    ):
        resp = client.get(
            "/api/v1/executive/cockpit/summary",
            headers=_bearer(admin_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    meta = body["_meta"]
    # The headline contract: live data + facts_available=True + an as_of.
    assert meta["data_source"] == "live"
    assert meta["facts_available"] is True
    assert "as_of" in meta

    kpi = body["kpi"]
    # Aggregated KPI values are non-None when facts are available.  We assert
    # on metrics that the router explicitly aggregates (recognized_revenue,
    # real_profit, collection_rate, health_score_avg) rather than every field
    # so a local regression in one KPI mapping does not mask the headline
    # contract.
    assert kpi["revenue_recognized"] == pytest.approx(100.0)
    assert kpi["real_profit"] == pytest.approx(40.0)
    assert kpi["collection_rate"] == pytest.approx(0.8)
    assert kpi["health_score_avg"] == pytest.approx(82.0)


def test_cockpit_summary_facts_degraded(client, admin_token, facts_table):
    """When FactsProvider returns DEGRADED, every metric is ``None``."""

    def _fake_get_facts(self, code: str, *args: Any, **kwargs: Any) -> FactsResponse:
        return _degraded_facts(code)

    with patch.object(
        __import__("facts_provider.facts_provider", fromlist=["FactsProvider"]).FactsProvider,
        "get_facts",
        _fake_get_facts,
    ):
        resp = client.get(
            "/api/v1/executive/cockpit/summary",
            headers=_bearer(admin_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    meta = body["_meta"]
    assert meta["data_source"] == "unavailable"
    assert meta["facts_available"] is False
    assert meta.get("facts_reason")

    kpi = body["kpi"]
    # The KPI block must not contain any fabricated numbers.
    for key in (
        "revenue_recognized",
        "real_profit",
        "gross_margin",
        "cash_inflow",
        "cash_outflow",
        "net_cashflow",
        "collection_rate",
        "tax_burden_rate",
        "eac_revenue",
        "eac_profit",
        "eac_margin",
        "health_score_avg",
    ):
        assert kpi[key] is None, f"kpi[{key}] should be None when facts unavailable"


# ---------------------------------------------------------------------------
# Projects endpoint mixed-scenario test
# ---------------------------------------------------------------------------

def test_projects_facts_mixed(client, admin_token):
    """Some projects have Facts, some don't: per-project flags must differ."""

    from app.db import SessionLocal

    codes = [_unique_project_code("AVAIL"), _unique_project_code("DEGRADED")]
    with SessionLocal() as db:
        for code in codes:
            db.execute(
                text(
                    "INSERT INTO projects (project_code, name, status, external_system, "
                    "external_project_id, contract_amount, start_date, expected_end_date, "
                    "location, project_type, note, created_at, updated_at) VALUES "
                    "(:code, :name, 'ACTIVE', 'construction-tax', :code, 0.0, '', '', "
                    "'', '', '', '', '')"
                ),
                {"code": code, "name": code},
            )
        db.commit()

    # Mock FactsProvider so the AVAIL project gets an AVAILABLE response and
    # the DEGRADED project gets a DEGRADED response.
    def _fake_get_facts(self, code: str, *args: Any, **kwargs: Any) -> FactsResponse:
        if code == codes[0]:
            return _available_facts(code)
        return _degraded_facts(code)

    with patch.object(
        __import__("facts_provider.facts_provider", fromlist=["FactsProvider"]).FactsProvider,
        "get_facts",
        _fake_get_facts,
    ):
        resp = client.get(
            "/api/v1/executive/projects",
            headers=_bearer(admin_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    projects = {p["project_code"]: p for p in body["projects"]}

    assert codes[0] in projects
    assert codes[1] in projects

    avail = projects[codes[0]]
    assert avail["facts_available"] is True
    assert avail["revenue"] == pytest.approx(100.0)
    assert avail["real_profit"] == pytest.approx(40.0)
    assert avail["health_score"] == pytest.approx(82.0)

    degraded = projects[codes[1]]
    assert degraded["facts_available"] is False
    assert degraded["revenue"] is None
    assert degraded["real_profit"] is None
    assert degraded["health_score"] is None
    # The router must surface a non-empty reason so the front-end can render
    # an honest "facts unavailable" tooltip.
    assert degraded.get("facts_reason")

    # The endpoint flips to ``live`` if at least one project returned facts.
    assert body["_meta"]["data_source"] == "live"


# ---------------------------------------------------------------------------
# AI chat — facts summary is prepended
# ---------------------------------------------------------------------------

def test_ai_chat_includes_facts_summary(client, admin_token, facts_table):
    """The AI reply must start with a deterministic facts summary block."""

    project_id = facts_table["id"]
    project_code = facts_table["project_code"]
    def _fake_get_facts(self, code: str, *args: Any, **kwargs: Any) -> FactsResponse:
        return _available_facts(code)

    with patch.object(
        __import__("facts_provider.facts_provider", fromlist=["FactsProvider"]).FactsProvider,
        "get_facts",
        _fake_get_facts,
    ):
        resp = client.post(
            "/api/v1/executive/ai/chat",
            json={"message": "利润分析", "project_id": project_id},
            headers=_bearer(admin_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    reply = body["reply"]

    # The router MUST prepend a Canonical Facts summary block so the LLM
    # cannot fabricate numbers. The summary is deterministic and explicitly
    # instructs the model to ground its answer in the supplied metrics.
    assert "Canonical Facts" in reply
    assert project_code in reply
    assert "recognized_revenue" in reply
    assert "real_profit" in reply
    # The reply must never claim demo data is available.
    assert "demo" not in reply.lower()

    meta = body["_meta"]
    assert meta["data_source"] == "live"
    assert meta["facts_available"] is True


def test_ai_chat_includes_facts_unavailable_summary(client, admin_token, facts_table):
    """When Facts are degraded, the summary must explicitly say so."""

    project_id = facts_table["id"]

    def _fake_get_facts(self, code: str, *args: Any, **kwargs: Any) -> FactsResponse:
        return _degraded_facts(code)

    with patch.object(
        __import__("facts_provider.facts_provider", fromlist=["FactsProvider"]).FactsProvider,
        "get_facts",
        _fake_get_facts,
    ):
        resp = client.post(
            "/api/v1/executive/ai/chat",
            json={"message": "利润分析", "project_id": project_id},
            headers=_bearer(admin_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    reply = body["reply"]

    assert "Canonical Facts" in reply
    assert "未接入" in reply or "数据不完整" in reply
    # The reply must NOT contain a numeric value that was never sourced
    # from a Canonical Facts row.
    assert "recognized_revenue = " not in reply

    meta = body["_meta"]
    assert meta["data_source"] == "unavailable"
    assert meta["facts_available"] is False
    assert meta.get("facts_reason")


# ---------------------------------------------------------------------------
# Project 360 — fail closed when Canonical Facts are unavailable
# ---------------------------------------------------------------------------

def test_project_360_facts_unavailable_withholds_all_financial_amounts(
    client, admin_token, facts_table
):
    """360 must not expose old/demo operational rows when Facts is degraded."""

    project_id = facts_table["id"]
    project_code = facts_table["project_code"]

    def _fake_get_facts(self, code: str, *args: Any, **kwargs: Any) -> FactsResponse:
        assert code == project_code
        return _degraded_facts(code, "entity mapping gap: analytics row unavailable")

    with patch.object(
        __import__("facts_provider.facts_provider", fromlist=["FactsProvider"]).FactsProvider,
        "get_facts",
        _fake_get_facts,
    ):
        resp = client.get(
            f"/api/v1/executive/projects/{project_id}/360",
            headers=_bearer(admin_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "DEGRADED"
    assert body["_meta"]["data_source"] == "unavailable"
    assert body["facts_summary"]["available"] is False
    assert "canonical_facts_unavailable" in body["data_gaps"]
    assert "entity_mapping_gap" in body["data_gaps"]

    financial = body["financial_penetration"]
    assert all(
        financial[key] is None
        for key in (
            "contract_total",
            "recognized_revenue",
            "actual_cost",
            "real_profit",
            "gross_margin_pct",
            "eac_forecast_cost",
            "eac_forecast_margin",
        )
    )

    penetration = body["system_penetration"]
    assert all(
        penetration[key] is None
        for key in (
            "recognized_revenue",
            "external_invoice_revenue",
            "internal_trade_volume_eliminated",
            "system_external_real_cost",
            "project_tax_paid",
            "management_profit_after_tax",
        )
    )
    assert body["cost_breakdown"]["materials"]["amount"] is None
    assert body["cost_breakdown"]["materials"]["pct"] is None
    assert body["tax_details"]["prepaid_tax"] is None
    assert body["system_penetration"]["revenueDetails"] == []
    assert body["system_penetration"]["externalDetails"] == []

    # Regression guard for the formally observed demo values.
    assert "23000000" not in resp.text
    assert "22000000" not in resp.text
    assert "1000000" not in resp.text


def test_project_360_facts_available_uses_canonical_metrics_only(
    client, admin_token, facts_table
):
    """Available 360 values come from Facts, not project-table re-aggregation."""

    project_id = facts_table["id"]

    def _fake_get_facts(self, code: str, *args: Any, **kwargs: Any) -> FactsResponse:
        return _available_facts(
            code,
            recognized_revenue=123.0,
            real_profit=45.0,
        )

    with patch.object(
        __import__("facts_provider.facts_provider", fromlist=["FactsProvider"]).FactsProvider,
        "get_facts",
        _fake_get_facts,
    ):
        resp = client.get(
            f"/api/v1/executive/projects/{project_id}/360",
            headers=_bearer(admin_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["facts_summary"]["available"] is True
    assert body["financial_penetration"]["recognized_revenue"] == pytest.approx(123.0)
    assert body["financial_penetration"]["real_profit"] == pytest.approx(45.0)
    assert body["system_penetration"]["recognized_revenue"] == pytest.approx(123.0)
    assert body["system_penetration"]["management_profit_after_tax"] == pytest.approx(45.0)
    assert body["system_penetration"]["external_invoice_revenue"] is None
    assert body["cost_breakdown"]["materials"]["amount"] is None
