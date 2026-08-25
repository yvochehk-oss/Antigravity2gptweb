"""AI health queue, deadline and lease-recovery regression coverage."""
from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest


def _batch_args(suffix: str) -> dict:
    return {
        "project_id": 1,
        "profile": "quick",
        "scopes": ["tax"],
        "endpoint_ids": [1],
        "user_instruction": f"health async {suffix}",
        "actor": "health-test",
    }


def test_adapter_enforces_total_deadline_across_retries(monkeypatch):
    from app.ai import adapter
    from app.models import AIModelEndpoint

    endpoint = AIModelEndpoint(
        name="slow-health-endpoint",
        adapter="openai_compatible",
        base_url="https://ai.example.test",
        chat_path="/v1/chat/completions",
        model="slow-model",
        api_key_env="OPENAI_API_KEY",
        enabled=True,
        timeout_seconds=90,
    )
    clock = [0.0]
    attempts: list[float] = []

    class Response:
        status_code = 500

        def raise_for_status(self):
            raise RuntimeError("slow endpoint 500")

        def json(self):  # pragma: no cover - never reached
            return {}

    class Client:
        def __init__(self, *args, **kwargs):
            self.timeout = kwargs["timeout"]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            attempts.append(kwargs["timeout"])
            return Response()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_ALLOWED_HOSTS", "ai.example.test")
    monkeypatch.setattr(adapter.httpx, "Client", Client)
    monkeypatch.setattr(adapter.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        adapter.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    with pytest.raises(adapter.AIEndpointTimeout):
        adapter.call_endpoint(endpoint, [], {}, deadline=1.0)
    assert len(attempts) == 1
    assert endpoint.timeout_seconds == 90


def test_health_submission_is_deduplicated_concurrently(seeded_app):
    from app.ai.orchestrator import claim_health_batch
    from app.db import SessionLocal

    def submit(worker: int) -> tuple[int, bool]:
        with SessionLocal() as db:
            args = _batch_args(f"concurrent-{worker}")
            args["user_instruction"] = "same health request"
            batch, created = claim_health_batch(
                db, **args,
            )
            return batch.id, created

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(submit, [1, 2]))
    assert len({batch_id for batch_id, _created in values}) == 1
    assert sum(created for _batch_id, created in values) == 1


def test_worker_exception_is_converged_to_failed_batch(seeded_app, monkeypatch):
    from app.ai import orchestrator
    from app.db import SessionLocal

    with SessionLocal() as db:
        batch, _ = orchestrator.claim_health_batch(
            db, **_batch_args("worker-exception"),
        )
        batch_id = batch.id

    def fail(*args, **kwargs):
        raise RuntimeError("worker exception sentinel")

    monkeypatch.setattr(orchestrator, "run_health_check", fail)
    assert orchestrator.enqueue_health_check(batch_id) is True
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            status = db.get(orchestrator.AIReviewBatch, batch_id).status
        if status in {"failed", "completed_with_errors"}:
            break
        time.sleep(0.02)
    assert status == "failed"


def test_worker_cancellation_is_converged_to_failed_batch(seeded_app, monkeypatch):
    from app.ai import orchestrator
    from app.db import SessionLocal

    with SessionLocal() as db:
        batch, _ = orchestrator.claim_health_batch(
            db, **_batch_args("worker-cancel"),
        )
        batch_id = batch.id

    def cancel(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(orchestrator, "run_health_check", cancel)
    assert orchestrator.enqueue_health_check(batch_id) is True
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            status = db.get(orchestrator.AIReviewBatch, batch_id).status
        if status in {"failed", "completed_with_errors"}:
            break
        time.sleep(0.02)
    assert status == "failed"


def test_stale_running_batch_and_jobs_are_recovered(seeded_app):
    from app.ai.orchestrator import recover_stale_health_batches
    from app.db import SessionLocal
    from app.models import (
        AIConsensusReport,
        AIModelEndpoint,
        AIReviewBatch,
        AIReviewJob,
        AIReviewResult,
    )

    # The legacy V2 schema stores timestamps in VARCHAR(30); keep the test
    # value in the same canonical second-precision format as ``now_iso``.
    old = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat(timespec="seconds")
    with SessionLocal() as db:
        endpoint = db.query(AIModelEndpoint).filter(
            AIModelEndpoint.enabled.is_(True),
        ).first()
        batch = AIReviewBatch(
            project_id=1,
            profile="quick",
            scopes_json=json.dumps(["tax"]),
            endpoint_ids_json=json.dumps([endpoint.id]),
            status="running",
            created_at=old,
            actor="health-test",
        )
        db.add(batch)
        db.flush()
        job = AIReviewJob(
            project_id=1,
            scope="tax",
            endpoint_id=endpoint.id,
            batch_id=batch.id,
            status="running",
            started_at=old,
            created_at=old,
        )
        db.add(job)
        db.commit()
        batch_id = batch.id

        # Other concurrent health tests may leave an additional stale batch
        # in the same disposable database; the contract here is that this
        # exact batch is recovered, not that it is the only candidate.
        assert recover_stale_health_batches(db, stale_after_seconds=1) >= 1
        assert db.get(AIReviewBatch, batch_id).status == "failed"
        assert db.query(AIReviewJob).filter(
            AIReviewJob.batch_id == batch_id,
        ).one().status == "failed"


def test_health_http_submission_returns_before_worker(seeded_app, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import health_check

    captured: list[int] = []
    monkeypatch.setattr(
        health_check,
        "enqueue_health_check",
        lambda batch_id: captured.append(batch_id) or True,
    )
    client = TestClient(app)
    assert client.post(
        "/login",
        data={"username": "admin", "password": "TestPass12345!"},
        follow_redirects=False,
    ).status_code == 302
    started = time.monotonic()
    response = client.post(
        "/health-check/run",
        data={
            "project_id": "1",
            "profile": "quick",
            "endpoint_ids": "1",
            "user_instruction": "http async budget",
        },
        follow_redirects=False,
    )
    elapsed = time.monotonic() - started
    assert response.status_code == 303
    assert elapsed < 1.0
    assert captured


def test_local_slow_http_health_is_bounded_and_reaches_terminal_state(
    seeded_app, monkeypatch,
):
    """A real loopback LLM cannot hold the health request or a job lease open.

    This deliberately uses a local HTTP server rather than replacing httpx:
    the test proves the internal-deployment SSRF exception, the per-request
    timeout, the total retry deadline, and the asynchronous HTTP boundary as
    one contract.  It only runs against the disposable PostgreSQL fixture.
    """
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app
    from app.models import (
        AIConsensusReport,
        AIModelEndpoint,
        AIReviewBatch,
        AIReviewJob,
        AIReviewResult,
    )

    class SlowHandler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler protocol name
            # Longer than the configured endpoint/deadline budget.  The
            # client must time out and the server may observe a broken pipe
            # when it eventually tries to answer.
            time.sleep(2.0)
            try:
                self.send_response(500)
                self.end_headers()
            except OSError:
                pass

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    server.daemon_threads = True
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    endpoint_id = None
    try:
        # This is the explicitly approved internal deployment policy.  The
        # RAG validator is not involved and remains strict in its own tests.
        monkeypatch.setenv("AI_ALLOW_PRIVATE_LLM", "1")
        monkeypatch.setenv("AI_ALLOWED_HOSTS", "public.example.test")
        monkeypatch.setenv("AI_HEALTH_ENDPOINT_DEADLINE_SECONDS", "1.5")

        with SessionLocal() as db:
            endpoint = AIModelEndpoint(
                name=f"local-slow-health-{time.monotonic_ns()}",
                adapter="openai_compatible",
                base_url=f"http://127.0.0.1:{server.server_port}",
                chat_path="/v1/chat/completions",
                model="local-slow-test",
                api_key_env="",
                enabled=True,
                timeout_seconds=1,
            )
            db.add(endpoint)
            db.commit()
            endpoint_id = endpoint.id

        client = TestClient(app)
        login = client.post(
            "/login",
            data={"username": "admin", "password": "TestPass12345!"},
            follow_redirects=False,
        )
        assert login.status_code == 302

        started = time.monotonic()
        response = client.post(
            "/health-check/run",
            data={
                "project_id": "1",
                "profile": "quick",
                "endpoint_ids": [str(endpoint_id)],
                "user_instruction": "real local slow endpoint deadline test",
            },
            follow_redirects=False,
        )
        elapsed = time.monotonic() - started
        assert response.status_code == 303
        assert elapsed < 3.0
        location = response.headers["location"]
        batch_id = int(location.rsplit("/", 1)[-1])

        terminal = {"completed", "completed_with_errors", "failed"}
        wait_until = time.monotonic() + 8.0
        snapshot = None
        while time.monotonic() < wait_until:
            with SessionLocal() as db:
                batch = db.get(AIReviewBatch, batch_id)
                jobs = db.query(AIReviewJob).filter(
                    AIReviewJob.batch_id == batch_id,
                ).all()
                snapshot = (
                    batch.status if batch else None,
                    [job.status for job in jobs],
                )
            if (
                snapshot[0] in terminal
                and snapshot[1]
                and all(status in {"completed", "failed"} for status in snapshot[1])
            ):
                break
            time.sleep(0.05)

        assert snapshot is not None
        assert snapshot[0] in {"completed_with_errors", "failed"}
        assert snapshot[1]
        assert all(status in {"completed", "failed"} for status in snapshot[1])
        assert not any(status in {"pending", "running"} for status in snapshot[1])
    finally:
        if endpoint_id is not None:
            # Keep the disposable fixture tidy if the test is run repeatedly;
            # no formal projectrag data is ever used by this test.
            with SessionLocal() as db:
                test_jobs = db.query(AIReviewJob).filter(
                    AIReviewJob.endpoint_id == endpoint_id,
                ).all()
                test_job_ids = [job.id for job in test_jobs]
                test_batch_ids = [
                    job.batch_id for job in test_jobs if job.batch_id is not None
                ]
                if test_job_ids:
                    db.query(AIReviewResult).filter(
                        AIReviewResult.job_id.in_(test_job_ids),
                    ).delete(synchronize_session=False)
                if test_batch_ids:
                    db.query(AIConsensusReport).filter(
                        AIConsensusReport.batch_id.in_(test_batch_ids),
                    ).delete(synchronize_session=False)
                db.query(AIReviewJob).filter(
                    AIReviewJob.endpoint_id == endpoint_id,
                ).delete(synchronize_session=False)
                if test_batch_ids:
                    db.query(AIReviewBatch).filter(
                        AIReviewBatch.id.in_(test_batch_ids),
                    ).delete(synchronize_session=False)
                db.query(AIModelEndpoint).filter(
                    AIModelEndpoint.id == endpoint_id,
                ).delete(synchronize_session=False)
                db.commit()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)
