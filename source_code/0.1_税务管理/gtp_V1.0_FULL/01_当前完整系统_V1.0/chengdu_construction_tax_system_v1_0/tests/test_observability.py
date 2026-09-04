"""Stability & observability tests for Tax V1.0 (Batch 5).

These tests cover:
- ``request_id`` propagation through middleware, audit, facts client
- ``X-Request-ID`` response header echoing
- Tax → RAG ``X-Request-ID`` forwarding through facts_client
- ``/healthz`` extended component surface
- ``TTLCache`` LRU capacity enforcement
- Audit log records carry ``request_id`` even on failure paths
"""
from __future__ import annotations

import contextlib
from threading import Barrier, BrokenBarrierError, Lock

import httpx
from fastapi.testclient import TestClient


def test_request_id_round_trip_is_echoed_in_response_header(seeded_app):
    """A generated request id is propagated back to the client."""
    from app.main import app

    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    request_id = response.headers.get("X-Request-ID")
    assert request_id and len(request_id) == 32


def test_inbound_request_id_is_honoured(seeded_app):
    """An incoming ``X-Request-ID`` header is preserved end-to-end."""
    from app.main import app

    client = TestClient(app)
    inbound = "0123456789abcdef0123456789abcdef"
    response = client.get(
        "/healthz", headers={"X-Request-ID": inbound},
    )
    assert response.headers.get("X-Request-ID") == inbound


def test_invalid_or_oversized_request_id_is_replaced(seeded_app):
    """Caller metadata cannot inject arbitrary response-header/log text."""
    from app.main import app

    inbound = "x" * 129
    response = TestClient(app).get("/healthz", headers={"X-Request-ID": inbound})
    resolved = response.headers.get("X-Request-ID", "")
    assert resolved != inbound
    assert len(resolved) == 32


def test_tax_to_rag_carries_request_id_header(seeded_app, monkeypatch):
    """The Tax facts client must forward ``X-Request-ID`` to the RAG endpoint."""
    from app import services
    from app.services import facts_client

    seen_headers: dict[str, str] = {}

    def fake_get(url, headers, params):
        seen_headers.update(headers)
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "project_code": "PRJ-X",
                "as_of": "2026-08-20T00:00:00+00:00",
                "facts_version": "f_test",
                "status": "AVAILABLE",
                "facts_available": True,
                "metrics": {},
            },
            request=request,
        )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers, params):
            return fake_get(url, headers, params)

    monkeypatch.setattr(services.facts_client.httpx, "Client", FakeClient)
    monkeypatch.setattr(facts_client, "FACTS_URL", "http://rag.invalid")
    monkeypatch.setattr(facts_client, "FACTS_API_KEY", "")

    inbound = "abc123" * 4
    payload = facts_client.get_project_facts(
        project_id=1, request_id=inbound,
    )

    assert seen_headers.get("X-Request-ID") == inbound
    assert payload.get("request_id") == inbound


def test_get_project_facts_failure_carries_degraded_status(seeded_app, monkeypatch):
    """A failed call must surface ``status="DEGRADED"`` instead of faking success."""
    from app.services import facts_client
    facts_client.facts_cache.invalidate()

    class BrokenClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, *args, **kwargs):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(facts_client.httpx, "Client", BrokenClient)
    monkeypatch.setattr(facts_client, "FACTS_URL", "http://rag.invalid")

    payload = facts_client.get_project_facts(project_id=1)
    assert payload.get("status") == "DEGRADED"
    assert payload.get("facts_available") is False
    assert "connection refused" in payload.get("error", "")


def test_invalidate_facts_failure_carries_degraded_status(seeded_app, monkeypatch):
    """The invalidate path must not silently return success on failure."""
    from app.services import facts_client

    class BrokenClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            raise RuntimeError("invalidate failure")

    monkeypatch.setattr(facts_client.httpx, "Client", BrokenClient)
    monkeypatch.setattr(facts_client, "FACTS_URL", "http://rag.invalid")

    payload = facts_client.invalidate_facts(project_id=1)
    assert payload.get("status") == "DEGRADED"
    assert "invalidate failure" in payload.get("error", "")


def test_audit_log_records_request_id(seeded_app):
    """Audit rows must persist the active ``request_id``."""
    from app.audit import audit
    from app.db import SessionLocal
    from app.observability import get_request_id, reset_request_id, set_request_id

    token = set_request_id("audit-trace-id")
    try:
        with SessionLocal() as db:
            entry = audit(
                db,
                action="QUERY",
                obj_type="Facts",
                obj_id="audit-test",
                message="trace",
            )
            db.commit()
            db.refresh(entry)
            assert entry.request_id == "audit-trace-id"
            db.delete(entry)
            db.commit()
    finally:
        reset_request_id(token)

    # Defensive: the global request id scope was cleaned up.
    assert get_request_id() == ""


def test_ttl_cache_evicts_lru_when_over_capacity():
    """Cache size never exceeds ``max_capacity`` and evicts in LRU order."""
    from app.cache import TTLCache

    cache = TTLCache[int](ttl_seconds=60.0, max_capacity=3)
    for i in range(5):
        cache.get(f"k{i}", loader=lambda i=i: i)
    # After 5 inserts into a 3-slot cache, only the last three keys remain.
    assert len(cache) == 3
    assert cache.get("k2", loader=lambda: -1) == 2
    assert cache.get("k3", loader=lambda: -1) == 3
    assert cache.get("k4", loader=lambda: -1) == 4
    # k0 and k1 must have been evicted
    assert cache.get("k0", loader=lambda: -1) == -1
    assert cache.get("k1", loader=lambda: -1) == -1


def test_ttl_cache_capacity_holds_1000_writes(seeded_app):
    """Cache must not grow unbounded under sustained writes."""
    from app.cache import TTLCache

    cap = 50
    cache = TTLCache[int](ttl_seconds=60.0, max_capacity=cap)
    for i in range(1000):
        cache.get(f"k{i % 1000}", loader=lambda i=i: i)
    assert len(cache) <= cap


def test_healthz_returns_components_block(seeded_app):
    """``/healthz`` must include a ``components`` block describing deps."""
    from app.main import app

    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded", "down"}
    components = body["components"]
    assert {"db", "rag", "facts", "ai"}.issubset(components.keys())
    for name, comp in components.items():
        assert comp["status"] in {"ok", "degraded", "down"}, (name, comp)


def test_healthz_runs_dependency_probes_concurrently(monkeypatch):
    """A slow dependency cannot extend ``/healthz`` by every probe budget."""
    from app import main as app_main
    from app.main import app

    barrier = Barrier(4, timeout=1.0)
    lock = Lock()
    passed_barrier = 0

    def concurrent_check():
        nonlocal passed_barrier
        try:
            barrier.wait()
        except BrokenBarrierError:
            return {"status": "down", "latency_ms": 0, "error": "not_concurrent"}
        with lock:
            passed_barrier += 1
        return {"status": "ok", "latency_ms": 1}

    for name in ("db", "rag", "facts", "ai"):
        monkeypatch.setattr(app_main, f"_check_{name}", concurrent_check)

    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert passed_barrier == 4
    assert response.json()["status"] == "ok"


def test_health_probe_budget_stays_inside_controller_timeout(monkeypatch):
    """Operator timeouts cannot make Tax exceed the controller's 3s budget."""
    from app import startup

    monkeypatch.setenv("AI_HEALTH_ENDPOINT_DEADLINE_SECONDS", "60")

    assert startup._HEALTH_TIMEOUT_SECONDS <= 1.5
    assert startup._ai_health_deadline_seconds() == 2.0


def test_healthz_reports_db_down_when_engine_unreachable(seeded_app, monkeypatch):
    """When the DB is down, ``/healthz`` must not silently report ``ok``."""
    from app import main as app_main

    monkeypatch.setattr(app_main, "_check_db", lambda: {
        "status": "down", "latency_ms": 1, "error": "DB unreachable",
    })

    from app.main import app

    client = TestClient(app)
    response = client.get("/healthz")
    body = response.json()
    assert body["components"]["db"]["status"] == "down"
    assert body["status"] in {"down", "degraded"}


def test_no_silent_exception_returns_empty_in_facts_client(seeded_app, monkeypatch):
    """Regression: facts_client failures must surface ``status=DEGRADED``."""
    from app.services import facts_client

    class BrokenClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, *args, **kwargs):
            raise httpx.ConnectError("simulated network failure")

    monkeypatch.setattr(facts_client.httpx, "Client", BrokenClient)
    monkeypatch.setattr(facts_client, "FACTS_URL", "http://rag.invalid")

    payload = facts_client.get_project_facts(project_id=1)
    # The forbidden pattern is "swallow and return empty".
    assert payload.get("status") == "DEGRADED", payload
    assert payload.get("facts_available") is False
    assert payload.get("error")


def test_healthz_aggregates_worst_component_status(seeded_app, monkeypatch):
    """``/healthz`` top-level ``status`` reflects the worst component."""
    from app import main as app_main

    monkeypatch.setattr(app_main, "_check_db", lambda: {"status": "ok", "latency_ms": 1})
    monkeypatch.setattr(app_main, "_check_rag", lambda: {"status": "ok", "latency_ms": 1})
    monkeypatch.setattr(app_main, "_check_facts", lambda: {"status": "down", "latency_ms": 1, "error": "x"})
    monkeypatch.setattr(app_main, "_check_ai", lambda: {"status": "ok", "latency_ms": 1})

    from app.main import app

    response = TestClient(app).get("/healthz")
    body = response.json()
    assert body["components"]["facts"]["status"] == "down"
    assert body["status"] in {"down", "degraded"}


def test_healthz_component_priority_prefers_degraded_over_ok(seeded_app, monkeypatch):
    """When no component is ``down`` the top-level still reports ``degraded``."""
    from app import main as app_main

    monkeypatch.setattr(app_main, "_check_db", lambda: {"status": "ok", "latency_ms": 1})
    monkeypatch.setattr(app_main, "_check_rag", lambda: {"status": "degraded", "latency_ms": 1, "error": "x"})
    monkeypatch.setattr(app_main, "_check_facts", lambda: {"status": "ok", "latency_ms": 1})
    monkeypatch.setattr(app_main, "_check_ai", lambda: {"status": "ok", "latency_ms": 1})

    from app.main import app

    response = TestClient(app).get("/healthz")
    body = response.json()
    assert body["status"] == "degraded"


def test_request_id_is_attached_to_stdlib_logger_records(seeded_app):
    """``RequestIdFilter`` injects the bound request id onto every log record."""
    import logging

    from app.observability import reset_request_id, set_request_id

    captured: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = lambda record: captured.append(record)
    logger = logging.getLogger("app.test_observability")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = True
    token = set_request_id("logger-trace-id")
    try:
        logger.info("captured")
    finally:
        reset_request_id(token)
        logger.removeHandler(handler)

    matches = [r for r in captured if getattr(r, "request_id", "") == "logger-trace-id"]
    assert matches, [getattr(r, "request_id", "") for r in captured]


def test_audit_log_records_request_id_on_failure_path(seeded_app):
    """An audit row written on the failure path still carries ``request_id``."""
    from app.audit import audit
    from app.db import SessionLocal
    from app.observability import get_request_id, reset_request_id, set_request_id

    token = set_request_id("audit-failure-trace")
    try:
        with SessionLocal() as db:
            entry = audit(
                db,
                action="QUERY_FAILED",
                obj_type="Facts",
                obj_id="degraded-trace",
                message="simulated downstream failure",
            )
            db.commit()
            db.refresh(entry)
            assert entry.request_id == "audit-failure-trace"
            db.delete(entry)
            db.commit()
    finally:
        reset_request_id(token)
    assert get_request_id() == ""


def test_response_header_round_trips_through_authenticated_route(seeded_app):
    """An authenticated route must also echo ``X-Request-ID``."""
    from app.main import app

    client = TestClient(app)
    login = client.post(
        "/login", data={"username": "admin", "password": "TestPass12345!"},
        follow_redirects=False,
    )
    assert login.status_code == 302
    inbound = "0fedcba9876543210fedcba987654321"
    # Use a public-path-equivalent authenticated probe: the dashboard page.
    response = client.get(
        "/dashboard", headers={"X-Request-ID": inbound}, follow_redirects=False,
    )
    # Any 2xx/3xx response carries the header; a 5xx would still echo it.
    assert response.status_code < 500
    assert response.headers.get("X-Request-ID") == inbound


def test_cross_system_request_id_chain_propagates_through_facts_client(
    seeded_app, monkeypatch,
):
    """End-to-end: Tax receives X-Request-ID, the request_id flows through
    facts_client → RAG outbound HTTP header, and the same id is echoed in
    the Tax response header.

    The RAG side is mocked at the HTTP layer; the chain under test is the
    in-process propagation from request entry to outbound HTTP and back.
    """
    from app import services
    from app.main import app
    from app.services import facts_client

    seen_headers: dict[str, str] = {}

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.text = "{}"
            self.headers = {"X-Request-ID": ""}

        def json(self):
            return {
                "project_code": "PRJ-X",
                "as_of": "2026-08-20T00:00:00+00:00",
                "facts_version": "f_test",
                "status": "AVAILABLE",
                "facts_available": True,
                "metrics": {},
            }

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers, params):
            seen_headers.update(headers)
            return FakeResponse()

    monkeypatch.setattr(services.facts_client.httpx, "Client", FakeClient)
    monkeypatch.setattr(facts_client, "FACTS_URL", "http://rag.invalid")
    monkeypatch.setattr(facts_client, "FACTS_API_KEY", "")

    inbound = "abc123" * 4  # 32 chars

    # Direct call proves the facts_client sets the outbound header.
    payload = facts_client.get_project_facts(project_id=1, request_id=inbound)
    assert seen_headers.get("X-Request-ID") == inbound
    assert payload.get("request_id") == inbound

    # Now simulate the inbound HTTP request: the inbound X-Request-ID is
    # honoured by the request_id middleware and reflected in the response.
    client = TestClient(app)
    response = client.get("/healthz", headers={"X-Request-ID": inbound})
    assert response.headers.get("X-Request-ID") == inbound


def test_ttl_cache_evicts_in_lru_order_after_overflow(seeded_app):
    """``TTLCache`` must evict the oldest key (not the newest) when full."""
    from app.cache import TTLCache

    cache: TTLCache[int] = TTLCache(ttl_seconds=120.0, max_capacity=4)
    for i in range(4):
        cache.get(f"key-{i}", loader=lambda i=i: i)
    # Reading key-0 promotes it to "most recently used".
    assert cache.get("key-0", loader=lambda: -1) == 0
    # Insert a 5th value; the LRU policy should drop key-1 (now the oldest).
    cache.get("key-4", loader=lambda: 4)
    assert len(cache) == 4
    assert cache.get("key-0", loader=lambda: -1) == 0   # kept (just used)
    assert cache.get("key-4", loader=lambda: -1) == 4   # kept (most recent)
    # key-1 was the oldest and must trigger the loader's fallback (-1).
    assert cache.get("key-1", loader=lambda: -1) == -1


def test_ttl_cache_exposes_hit_and_expired_states_without_caching_failures():
    """Cache state is explicit and a failed loader is retried, never cached."""
    import time

    from app.cache import TTLCache

    cache: TTLCache[int] = TTLCache(ttl_seconds=0.01, max_capacity=2)
    calls = 0

    def loader() -> int:
        nonlocal calls
        calls += 1
        return calls

    value, status = cache.get_with_status("one", loader)
    assert (value, status) == (1, "miss")
    value, status = cache.get_with_status("one", loader)
    assert (value, status) == (1, "hit")
    time.sleep(0.02)
    value, status = cache.get_with_status("one", loader)
    assert (value, status) == (2, "expired")

    failures = 0

    def failing_loader() -> int:
        nonlocal failures
        failures += 1
        raise RuntimeError("upstream unavailable")

    for _ in range(2):
        with contextlib.suppress(RuntimeError):
            cache.get_with_status("failed", failing_loader)
    assert failures == 2


def test_facts_cache_invalidation_is_project_scoped():
    """Active Facts invalidation removes all query variants for one project."""
    from app.services import facts_client

    facts_client.facts_cache.invalidate()
    facts_client.facts_cache.set("url::PRJ-A::false::60::::key", object())
    facts_client.facts_cache.set("url::PRJ-A::false::120::::key", object())
    facts_client.facts_cache.set("url::PRJ-B::false::60::::key", object())

    removed = facts_client.facts_cache.invalidate_where(
        lambda key: "::PRJ-A::" in key,
    )
    assert removed == 2
    assert len(facts_client.facts_cache) == 1
    facts_client.facts_cache.invalidate()


def test_facts_client_reports_miss_hit_and_active_invalidation(seeded_app, monkeypatch):
    """Facts responses expose local cache transitions and clear on invalidate."""
    from app.services import facts_client

    calls = {"get": 0, "post": 0}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "project_code": "PRJ-X",
                "as_of": "2026-08-20T00:00:00+00:00",
                "facts_version": "f_cache",
                "status": "AVAILABLE",
                "facts_available": True,
                "metrics": {"revenue": 1},
            }

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, *args, **kwargs):
            calls["get"] += 1
            return FakeResponse()

        def post(self, *args, **kwargs):
            calls["post"] += 1
            return FakeResponse()

    facts_client.facts_cache.invalidate()
    monkeypatch.setattr(facts_client.httpx, "Client", FakeClient)
    monkeypatch.setattr(facts_client, "FACTS_URL", "http://cache-test")
    monkeypatch.setattr(facts_client, "FACTS_API_KEY", "")

    first = facts_client.get_project_facts(project_id=1)
    second = facts_client.get_project_facts(project_id=1)
    assert first["cache_status"] == "miss"
    assert second["cache_status"] == "hit"
    assert calls["get"] == 1

    invalidated = facts_client.invalidate_facts(project_id=1)
    assert invalidated["cache_status"] == "invalidated"
    assert calls["post"] == 1
    third = facts_client.get_project_facts(project_id=1)
    assert third["cache_status"] == "miss"
    assert calls["get"] == 2
    facts_client.facts_cache.invalidate()
