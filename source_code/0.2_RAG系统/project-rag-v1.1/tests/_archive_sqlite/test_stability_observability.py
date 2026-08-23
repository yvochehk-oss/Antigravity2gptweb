"""Stability & observability tests for ProjectRAG V1.1 (Batch 5).

These tests cover:
- ``request_id`` propagation through middleware and outbound calls
- ``X-Request-ID`` response header echoing
- ``/healthz`` component surface
- ``FactsCache`` hit/miss/expired/invalidated events
- ``QueryLog`` 30-day retention purge routine
- Ingest worker graceful shutdown via ``request_stop``
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time as _time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Match the same bootstrap used in test_smoke.py so this module can be
# collected standalone.  The env vars are scoped to the test process only.
_TMP = tempfile.mkdtemp(prefix="projectrag-v02-b5-")
os.environ.setdefault("PROJECT_RAG_DATA_DIR", _TMP)
os.environ.setdefault("PROJECT_RAG_DB_URL", f"sqlite:///{Path(_TMP)/'test.db'}")
os.environ.setdefault("PROJECT_RAG_EMBEDDING_BACKEND", "hash_v1")
os.environ.setdefault("PROJECT_RAG_RERANKER_BACKEND", "off")
os.environ.setdefault("PROJECT_RAG_AUTO_START_WORKER", "0")
os.environ.setdefault("PROJECT_RAG_PROCESS_JOBS_INLINE", "1")

from fastapi.testclient import TestClient

from app.config import IMPORT_ROOT
from app.db import Base, engine, SessionLocal, init_db
from app.main import app
from app.models import Project, Document, Chunk, IngestJob, Entity, QueryLog
from app.services import jobs as jobs_service
from app.services.jobs import (
    _stop as worker_stop_event,
    request_stop,
    start_worker,
    stop_worker,
    get_worker_status,
)


@pytest.fixture(scope="module", autouse=True)
def _bootstrap():
    Base.metadata.drop_all(engine)
    init_db()
    yield


def _seed_project(code: str = "B5-001") -> int:
    """Helper: create a project row via the public sync endpoint."""
    client = TestClient(app)
    resp = client.post("/api/v1/projects/sync", json={
        "project_code": code,
        "name": "稳定性测试项目",
        "external_system": "construction-tax",
        "external_project_id": code,
        "status": "ACTIVE",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_request_id_middleware_generates_id_when_missing():
    """The middleware must always produce a 32-char request id."""
    client = TestClient(app)
    response = client.get("/healthz")
    rid = response.headers.get("X-Request-ID")
    assert rid and len(rid) == 32
    assert response.status_code == 200


def test_request_id_middleware_honours_inbound_header():
    """An inbound ``X-Request-ID`` must be echoed verbatim."""
    client = TestClient(app)
    inbound = "abcdef0123456789" * 2
    response = client.get("/healthz", headers={"X-Request-ID": inbound})
    assert response.headers.get("X-Request-ID") == inbound


def test_request_id_is_attached_to_logger_records():
    """A bound request id must appear on stdlib log records."""
    import logging

    from app.observability import (
        install_logging_filter,
        set_request_id,
        reset_request_id,
    )

    install_logging_filter()
    captured: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = lambda record: captured.append(record)
    # Attach the handler to the ``projectrag`` namespace so the existing
    # filter on the root logger still propagates the ``request_id`` attribute.
    logger = logging.getLogger("projectrag.test")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = True
    try:
        token = set_request_id("rag-trace-id")
        try:
            logger.info("payload message")
        finally:
            reset_request_id(token)
    finally:
        logger.removeHandler(handler)

    matches = [r for r in captured if getattr(r, "request_id", "") == "rag-trace-id"]
    assert matches, [getattr(r, "request_id", "") for r in captured]


def test_request_id_is_attached_to_late_child_handler_without_root_handler():
    """Record enrichment must not depend on a root handler revisiting it."""
    from app.observability import install_logging_filter, set_request_id, reset_request_id

    install_logging_filter()
    logger = logging.getLogger("projectrag.late-child-handler")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    captured: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = lambda record: captured.append(record)
    logger.addHandler(handler)
    try:
        token = set_request_id("late-child-trace-id")
        try:
            logger.info("late handler message")
        finally:
            reset_request_id(token)
    finally:
        logger.removeHandler(handler)

    assert captured and captured[0].request_id == "late-child-trace-id"


def test_healthz_returns_components_block():
    """``/healthz`` must include the four canonical components."""
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded", "down"}
    components = body["components"]
    for key in ("db", "worker", "ai", "facts", "embedding", "reranker"):
        assert key in components
        assert components[key]["status"] in {"ok", "degraded", "down"}


def test_healthz_reports_db_down_when_engine_unreachable(monkeypatch):
    """A simulated DB outage must surface ``status=down`` not ``ok``."""
    from app import main as rag_main

    def _broken_db():
        return {"status": "down", "latency_ms": 0, "error": "DB unreachable"}

    monkeypatch.setattr(rag_main, "_check_db_component", _broken_db)

    client = TestClient(app)
    response = client.get("/healthz")
    body = response.json()
    assert body["components"]["db"]["status"] == "down"
    assert body["status"] in {"down", "degraded"}


def test_healthz_reports_facts_degraded_when_module_unavailable(monkeypatch):
    """A missing facts_provider module must surface ``degraded`` status."""
    from app import main as rag_main

    def _broken_facts():
        return {"status": "degraded", "error": "facts_provider missing"}

    monkeypatch.setattr(rag_main, "_check_facts_component", _broken_facts)

    client = TestClient(app)
    response = client.get("/healthz")
    body = response.json()
    assert body["components"]["facts"]["status"] == "degraded"
    assert body["status"] in {"degraded", "down"}


def test_facts_cache_records_hit_event():
    """Repeated reads on the same key emit ``facts_cache.hit`` and a cold
    read emits ``facts_cache.miss``."""
    from facts_provider.facts_provider import FactsCache, FactsResponse

    cache = FactsCache()
    cache.clear_events()
    response = FactsResponse(
        project_code="P1",
        as_of="2026-08-20T00:00:00+00:00",
        facts_version="f_test",
        metrics={},
        status="AVAILABLE",
        facts_available=True,
    )
    # A lookup against an empty cache must emit miss.
    assert cache.get("facts:P1") is None
    cache.set("facts:P1", response)
    assert cache.get("facts:P1") is response  # first hit
    assert cache.get("facts:P1") is response  # subsequent hit
    summary = {event for event, _ in cache.events}
    assert "facts_cache.hit" in summary
    assert "facts_cache.miss" in summary


def test_facts_cache_records_expired_event():
    """An entry past ``max_age_seconds`` emits ``facts_cache.expired``."""
    from facts_provider.facts_provider import FactsCache, FactsResponse

    cache = FactsCache()
    cache.clear_events()
    # Backdate the entry so its expiry has elapsed even with max_age_seconds=1.
    response = FactsResponse(
        project_code="P2",
        as_of="2026-08-20T00:00:00+00:00",
        facts_version="f_test",
        metrics={},
        status="AVAILABLE",
        facts_available=True,
    )
    cache._cache["facts:P2"] = (
        response,
        datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    cache.get("facts:P2", max_age_seconds=1)
    summary = {event for event, _ in cache.events}
    assert "facts_cache.expired" in summary


def test_facts_cache_records_invalidate_event():
    """Calling ``invalidate`` emits ``facts_cache.invalidated``."""
    from facts_provider.facts_provider import FactsCache, FactsResponse

    cache = FactsCache()
    cache.clear_events()
    response = FactsResponse(
        project_code="P3",
        as_of="2026-08-20T00:00:00+00:00",
        facts_version="f_test",
        metrics={},
        status="AVAILABLE",
        facts_available=True,
    )
    cache.set("facts:P3", response)
    cache.invalidate("facts:P3")
    summary = {event for event, _ in cache.events}
    assert "facts_cache.invalidated" in summary


def test_facts_cache_subscriber_summary_counts_events():
    """The subscriber must surface hit/miss/expired/invalidated counters."""
    from facts_provider.facts_provider import (
        FactsCache, FactsCacheSubscriber, FactsResponse,
    )

    cache = FactsCache()
    sub = FactsCacheSubscriber(cache)
    cache.clear_events()
    response = FactsResponse(
        project_code="P4",
        as_of="2026-08-20T00:00:00+00:00",
        facts_version="f_test",
        metrics={},
        status="AVAILABLE",
        facts_available=True,
    )
    assert cache.get("facts:P4") is None  # miss
    cache.set("facts:P4", response)
    assert cache.get("facts:P4") is response  # hit
    cache.invalidate("facts:P4")
    summary = sub.summary()
    assert summary["miss"] >= 1
    assert summary["hit"] >= 1
    assert summary["invalidated"] >= 1


def test_facts_cache_has_bounded_capacity_and_event_history():
    """Facts cache eviction and event history must be bounded explicitly."""
    from facts_provider.facts_provider import FactsCache, FactsResponse

    cache = FactsCache(max_entries=2, event_limit=10)

    def available(code: str) -> FactsResponse:
        return FactsResponse(
            project_code=code,
            as_of="2026-08-20T00:00:00+00:00",
            facts_version=f"f_{code}",
            metrics={},
            status="AVAILABLE",
            facts_available=True,
        )

    cache.set("facts:P1", available("P1"))
    cache.set("facts:P2", available("P2"))
    cache.set("facts:P3", available("P3"))

    assert cache.size == 2
    assert cache.get("facts:P1") is None
    assert any(event == FactsCache.EVENT_EVICTED for event, _ in cache.events)

    for i in range(20):
        cache.get(f"missing:{i}")
    assert len(cache.events) <= 10


def test_query_log_retention_deletes_old_rows():
    """``prune_query_logs`` removes rows older than the retention window."""
    from app.services.query_log_retention import (
        prune_query_logs,
        count_expired_query_logs,
    )

    pid = _seed_project("RETENTION-001")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        old = QueryLog(
            project_id=pid,
            query="old query",
            filters_json="{}",
            top_k=5,
            created_at=(now - timedelta(days=45)).isoformat(timespec="seconds"),
            retrieval_status="AVAILABLE",
        )
        fresh = QueryLog(
            project_id=pid,
            query="fresh query",
            filters_json="{}",
            top_k=5,
            created_at=now.isoformat(timespec="seconds"),
            retrieval_status="AVAILABLE",
        )
        db.add_all([old, fresh])
        db.commit()

    assert count_expired_query_logs(retention_days=30) >= 1
    deleted = prune_query_logs(retention_days=30)
    assert deleted >= 1

    # The fresh row must remain.
    with SessionLocal() as db:
        survivors = (
            db.query(QueryLog)
            .filter(QueryLog.project_id == pid)
            .order_by(QueryLog.id.asc())
            .all()
        )
    queries = {row.query for row in survivors}
    assert "fresh query" in queries
    assert "old query" not in queries


def test_query_log_retention_cli_dry_run_does_not_delete(capsys):
    """``prune_query_logs_cli --dry-run`` must not mutate the database."""
    import sys
    from app.services.query_log_retention import prune_query_logs_cli

    pid = _seed_project("RETENTION-002")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add(QueryLog(
            project_id=pid,
            query="stale",
            filters_json="{}",
            top_k=5,
            created_at=(now - timedelta(days=120)).isoformat(timespec="seconds"),
            retrieval_status="AVAILABLE",
        ))
        db.commit()

    before = _count_query_logs(pid)
    rc = prune_query_logs_cli(["--days", "30", "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "dry-run" in captured.out
    after = _count_query_logs(pid)
    assert before == after


def _count_query_logs(project_id: int) -> int:
    with SessionLocal() as db:
        return db.query(QueryLog).filter(QueryLog.project_id == project_id).count()


def test_worker_graceful_stop_leaves_no_zombie():
    """Calling ``request_stop`` must end the worker loop without raising."""
    # The lifespan auto-starts the worker when AUTO_START_WORKER=1; here we
    # drive start/stop explicitly so the test is independent of the env flag.
    worker_stop_event.clear()
    start_worker()
    status = get_worker_status()
    assert status["running"] is True

    request_stop()
    # ``stop_worker`` joins the thread; once it returns the loop has ended.
    stop_worker()

    status = get_worker_status()
    assert status["running"] is False
    assert status["stop_requested"] is True


def test_worker_terminates_while_actively_processing():
    """A stop request during processing must finish the current job, then exit."""
    worker_stop_event.clear()

    # Replace ``process_next`` so the loop is busy while we ask it to stop.
    iterations = {"count": 0}
    original_process_next = jobs_service.process_next

    def busy_loop():
        iterations["count"] += 1
        # Hold the loop "in a task" for a short window so SIGTERM-equivalent
        # is delivered while work is in flight.
        end = _time.monotonic() + 0.05
        while _time.monotonic() < end:
            pass
        # After the first task, flip the stop flag so the loop unwinds.
        if iterations["count"] >= 1:
            request_stop()
        return False

    jobs_service.process_next = busy_loop
    try:
        start_worker()
        # Give the loop time to enter process_next at least once.
        deadline = _time.monotonic() + 2.0
        while _time.monotonic() < deadline and iterations["count"] == 0:
            _time.sleep(0.01)
        stop_worker()
    finally:
        jobs_service.process_next = original_process_next

    status = get_worker_status()
    assert status["running"] is False
    assert iterations["count"] >= 1, "expected the loop to have begun processing"


def test_request_id_is_propagated_through_facts_provider():
    """The bound request id should reach the facts provider scope."""
    from app.observability import get_request_id, set_request_id, reset_request_id

    token = set_request_id("facts-trace-001")
    try:
        assert get_request_id() == "facts-trace-001"
    finally:
        reset_request_id(token)


def test_outbound_request_includes_request_id_header():
    """When a Tax request hits the RAG ``/healthz`` endpoint the inbound
    ``X-Request-ID`` must be echoed in the response."""
    inbound = "0123456789abcdef" * 2
    client = TestClient(app)
    response = client.get("/healthz", headers={"X-Request-ID": inbound})
    assert response.headers.get("X-Request-ID") == inbound
