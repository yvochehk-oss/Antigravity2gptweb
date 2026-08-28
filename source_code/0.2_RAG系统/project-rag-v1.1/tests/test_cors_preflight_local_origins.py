"""Regression coverage for the V2 local-browser CORS contract."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RAG_ROOT = Path(__file__).resolve().parents[1]


def _client_with_development_defaults(monkeypatch):
    """Build the production app without an explicit CORS override.

    ``app.main`` reads the allow-list at import time. Importing it only after
    removing the override makes this test exercise the actual development
    defaults rather than a developer's local ``.env`` setting.
    """
    monkeypatch.delenv("RAG_CORS_ALLOW_ORIGINS", raising=False)
    from app import main

    return TestClient(importlib.reload(main).app)


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/api/v1/executive/projects",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )


def test_localhost_5173_preflight_is_allowed(monkeypatch):
    client = _client_with_development_defaults(monkeypatch)
    response = _preflight(client, "http://localhost:5173")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_loopback_5173_preflight_is_allowed(monkeypatch):
    client = _client_with_development_defaults(monkeypatch)
    response = _preflight(client, "http://127.0.0.1:5173")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_unlisted_origin_preflight_is_rejected(monkeypatch):
    client = _client_with_development_defaults(monkeypatch)
    response = _preflight(client, "https://evil.example")

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_explicit_cors_override_does_not_become_wildcard(monkeypatch):
    """LAN access remains opt-in through an explicit origin list."""
    monkeypatch.setenv("RAG_CORS_ALLOW_ORIGINS", "http://192.168.10.36:5173")
    from app import legacy_routes, main

    importlib.reload(legacy_routes)
    client = TestClient(importlib.reload(main).app)
    allowed = _preflight(client, "http://192.168.10.36:5173")
    rejected = _preflight(client, "http://192.168.10.37:5173")

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://192.168.10.36:5173"
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


@pytest.mark.parametrize("raw_origins", ["*", "http://localhost:5173,*"])
def test_wildcard_cors_configuration_rejects_app_initialization(raw_origins):
    """A wildcard must fail startup instead of allowing every browser origin."""
    environment = os.environ.copy()
    environment.update(
        {
            "RAG_CORS_ALLOW_ORIGINS": raw_origins,
            "PROJECT_RAG_DB_URL": "postgresql+psycopg://invalid:invalid@127.0.0.1:1/projectrag_test",
            "PROJECT_RAG_AUTO_START_WORKER": "0",
            "PROJECT_RAG_EMBEDDING_BACKEND": "hash_v1",
            "PROJECT_RAG_RERANKER_BACKEND": "off",
            "PYTHONPATH": str(RAG_ROOT),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=RAG_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "RAG_CORS_ALLOW_ORIGINS" in result.stderr
    assert "wildcard '*' is not allowed" in result.stderr
