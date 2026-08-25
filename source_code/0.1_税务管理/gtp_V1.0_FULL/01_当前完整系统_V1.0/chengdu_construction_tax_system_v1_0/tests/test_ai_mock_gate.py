"""Mock endpoint safety and explicit degraded-result contracts."""
from __future__ import annotations

import json

import pytest


def test_mock_endpoint_is_never_used_as_a_failure_fallback(monkeypatch):
    from app.ai import adapter
    from app.models import AIModelEndpoint

    endpoint = AIModelEndpoint(
        name="mock-gate-test",
        adapter="mock",
        model="mock",
        enabled=True,
    )
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("AI_ALLOW_MOCK_ENDPOINTS", raising=False)
    monkeypatch.setattr(adapter, "mock_review", forbidden)

    with pytest.raises(adapter.AIEndpointUnavailable):
        adapter.call_endpoint(endpoint, [], {})
    assert called is False


def test_run_review_persists_unavailable_result_for_blocked_mock(
    seeded_app, monkeypatch,
):
    from app.ai.review import run_review
    from app.db import SessionLocal
    from app.models import AIModelEndpoint, AIReviewJob, AIReviewResult

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("AI_ALLOW_MOCK_ENDPOINTS", raising=False)
    db = SessionLocal()
    try:
        endpoint = db.query(AIModelEndpoint).filter(
            AIModelEndpoint.adapter == "mock",
            AIModelEndpoint.enabled.is_(True),
        ).first()
        assert endpoint is not None
        job = AIReviewJob(
            project_id=1,
            scope="equipment",
            endpoint_id=endpoint.id,
            status="pending",
            created_at="2026-08-23T00:00:00+00:00",
        )
        db.add(job)
        db.commit()

        with pytest.raises(Exception, match="mock AI 端点"):
            run_review(db, job)

        persisted = db.get(AIReviewJob, job.id)
        result = db.query(AIReviewResult).filter(
            AIReviewResult.job_id == job.id,
        ).one()
        assert persisted.status == "failed"
        assert result.risk_level == "UNKNOWN"
        assert result.raw_response == ""
        gaps = json.loads(result.data_gaps_json)
        assert any("UNAVAILABLE" in gap for gap in gaps)
        findings = json.loads(result.findings_json)
        assert findings[0]["requires_manual_review"] is True
    finally:
        db.close()


def test_manager_returns_degraded_envelope_without_mock_fallback(
    seeded_app, monkeypatch,
):
    from app.db import SessionLocal
    from app.models import AIModelEndpoint
    from app.routers import manager

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("AI_ALLOW_MOCK_ENDPOINTS", raising=False)
    monkeypatch.setattr(
        manager,
        "call_endpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("real endpoint failure sentinel")
        ),
    )
    db = SessionLocal()
    try:
        endpoint = db.query(AIModelEndpoint).filter(
            AIModelEndpoint.adapter == "openai_compatible",
            AIModelEndpoint.enabled.is_(True),
        ).first()
        assert endpoint is not None
        result = manager._call_ai("请检查", {}, endpoint.id)
    finally:
        db.close()

    assert result["status"] == "DEGRADED"
    assert result["requires_manual_review"] is True
    assert result["data_gaps"]
    assert result["answer"]


def test_remediation_recheck_does_not_turn_blocked_mock_into_success(
    seeded_app, monkeypatch,
):
    from app.ai.orchestrator import recheck_task
    from app.db import SessionLocal
    from app.models import AIModelEndpoint, AIReviewJob, AIReviewResult, RemediationTask

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("AI_ALLOW_MOCK_ENDPOINTS", raising=False)
    db = SessionLocal()
    try:
        endpoint = db.query(AIModelEndpoint).filter(
            AIModelEndpoint.adapter == "mock",
            AIModelEndpoint.enabled.is_(True),
        ).first()
        task = RemediationTask(
            project_id=1,
            scope="equipment",
            title="mock gate recheck",
            description="must remain manual",
            priority="P1",
            owner_role="test",
            status="open",
            created_at="2026-08-23T00:00:00+00:00",
            updated_at="2026-08-23T00:00:00+00:00",
        )
        db.add(task)
        db.commit()

        with pytest.raises(Exception, match="mock AI 端点"):
            recheck_task(db, task, endpoint.id)

        assert task.status == "open"
        failed_job = db.query(AIReviewJob).order_by(AIReviewJob.id.desc()).first()
        assert failed_job.status == "failed"
        assert db.query(AIReviewResult).filter(
            AIReviewResult.job_id == failed_job.id,
        ).one().risk_level == "UNKNOWN"
    finally:
        db.close()


def test_ai_review_api_exposes_unavailable_status_for_blocked_mock(
    seeded_app, monkeypatch,
):
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app
    from app.models import AIModelEndpoint

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("AI_ALLOW_MOCK_ENDPOINTS", raising=False)
    with SessionLocal() as db:
        endpoint = db.query(AIModelEndpoint).filter(
            AIModelEndpoint.adapter == "mock",
            AIModelEndpoint.enabled.is_(True),
        ).first()
        assert endpoint is not None
        endpoint_id = endpoint.id

    client = TestClient(app)
    assert client.post(
        "/login",
        data={"username": "admin", "password": "TestPass12345!"},
        follow_redirects=False,
    ).status_code == 302
    response = client.post(
        "/ai-review/run",
        data={
            "project_id": "1",
            "scope": "equipment",
            "endpoint_id": str(endpoint_id),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    payload = client.get(f"/api/ai-review/{job_id}").json()
    assert payload["status"] == "UNAVAILABLE"
    assert payload["requires_manual_review"] is True
    assert payload["result"]["risk_level"] == "UNKNOWN"
