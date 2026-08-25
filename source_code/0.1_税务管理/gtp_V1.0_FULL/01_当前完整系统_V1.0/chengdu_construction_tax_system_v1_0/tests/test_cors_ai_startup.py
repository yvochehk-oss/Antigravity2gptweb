"""Regression tests for the Tax browser boundary and AI health probe."""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/api/projects",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )


def test_tax_cors_has_one_explicit_credentialed_allowlist(monkeypatch):
    """Allowed local origins work while an arbitrary origin gets no CORS grant."""
    monkeypatch.delenv("TAX_CORS_ALLOW_ORIGINS", raising=False)
    from app.wiring import create_app

    app = create_app()
    assert sum(item.cls is CORSMiddleware for item in app.user_middleware) == 1
    client = TestClient(app)

    allowed = _preflight(client, "http://localhost:5173")
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert allowed.headers["access-control-allow-credentials"] == "true"

    rejected = _preflight(client, "https://evil.example")
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers

    actual_rejected = client.get("/", headers={"Origin": "https://evil.example"})
    assert actual_rejected.status_code == 200
    assert "access-control-allow-origin" not in actual_rejected.headers


def test_tax_cors_override_is_explicit_and_not_reflected(monkeypatch):
    """A configured origin is accepted exactly; a sibling origin is rejected."""
    monkeypatch.setenv("TAX_CORS_ALLOW_ORIGINS", "http://127.0.0.1:9191")
    from app.wiring import create_app

    client = TestClient(create_app())
    allowed = _preflight(client, "http://127.0.0.1:9191")
    rejected = _preflight(client, "http://127.0.0.1:9192")
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:9191"
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


@pytest.mark.parametrize("raw_origins", ["*", "http://localhost:5173,*", ""])
def test_tax_cors_rejects_wildcard_or_empty_configuration(raw_origins):
    """A bad browser trust configuration fails closed at app construction."""
    from app.wiring import _parse_cors_origins

    with pytest.raises(ValueError):
        _parse_cors_origins(raw_origins)


def test_ai_health_posts_to_configured_chat_path_without_head(
    seeded_app, monkeypatch,
):
    """Health must validate the real OpenAI-compatible route with one token."""
    from app.db import SessionLocal
    from app.models import AIModelEndpoint
    from app.startup import _check_ai

    posts: list[dict[str, object]] = []
    heads: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler protocol name
            heads.append(self.path)
            self.send_response(500)
            self.end_headers()

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler protocol name
            length = int(self.headers.get("Content-Length", "0"))
            posts.append(json.loads(self.rfile.read(length)))
            if self.path != "/v1/chat/completions":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(
                {"choices": [{"message": {"content": "ok"}}]},
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    endpoint_name = f"startup-chat-probe-{time.monotonic_ns()}"
    endpoint_id = None
    previous_enabled: list[tuple[int, bool | None]] = []
    try:
        monkeypatch.setenv("AI_ALLOW_PRIVATE_LLM", "1")
        monkeypatch.setenv("AI_HEALTH_ENDPOINT_DEADLINE_SECONDS", "2")
        with SessionLocal() as db:
            existing = db.query(AIModelEndpoint).all()
            previous_enabled = [(row.id, row.enabled) for row in existing]
            for row in existing:
                row.enabled = False
            endpoint = AIModelEndpoint(
                name=endpoint_name,
                adapter="openai_compatible",
                base_url=f"http://127.0.0.1:{server.server_port}",
                chat_path="/v1/chat/completions",
                model="local-health-model",
                api_key_env="",
                enabled=True,
                timeout_seconds=1,
            )
            db.add(endpoint)
            db.commit()
            endpoint_id = endpoint.id

        result = _check_ai()

        assert result["status"] == "ok"
        assert result["endpoints"] == [{"name": endpoint_name, "status": "ok"}]
        assert not heads
        assert len(posts) == 1
        assert posts[0]["model"] == "local-health-model"
        assert posts[0]["max_tokens"] == 1
        assert posts[0]["stream"] is False
    finally:
        with SessionLocal() as db:
            if endpoint_id is not None:
                db.query(AIModelEndpoint).filter(
                    AIModelEndpoint.id == endpoint_id,
                ).delete(synchronize_session=False)
            for row_id, enabled in previous_enabled:
                row = db.get(AIModelEndpoint, row_id)
                if row is not None:
                    row.enabled = enabled
            db.commit()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)
