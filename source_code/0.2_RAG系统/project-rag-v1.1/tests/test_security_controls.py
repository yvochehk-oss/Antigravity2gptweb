"""Regression tests for the RAG security boundary."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import security
from app.security import RAGSecurityMiddleware, validate_file_content, validate_outbound_url
from app.services.storage import PathTraversalError, validate_stored_file


def test_upload_size_and_magic_bytes_are_enforced(monkeypatch):
    monkeypatch.setattr(security, "MAX_UPLOAD_SIZE", 4)
    with pytest.raises(ValueError, match="maximum size"):
        validate_file_content("x.txt", b"12345")
    monkeypatch.setattr(security, "MAX_UPLOAD_SIZE", 1024)
    with pytest.raises(ValueError, match="signature"):
        validate_file_content("x.pdf", b"not-a-pdf")
    validate_file_content("x.txt", b"ok")  # text has no binary signature


def test_outbound_url_rejects_private_and_non_https_targets(monkeypatch):
    with pytest.raises(ValueError):
        validate_outbound_url("http://127.0.0.1:8080/v1")
    with pytest.raises(ValueError):
        validate_outbound_url("https://metadata.google.internal/v1")
    monkeypatch.setattr(security.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 443))])
    assert validate_outbound_url("https://example.com/v1").endswith("/v1")


def test_api_key_middleware_fails_closed_when_required(monkeypatch):
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "RAG_SHARED_API_KEY", "test-api-key")
    app = FastAPI()
    app.add_middleware(RAGSecurityMiddleware)

    @app.get("/api/v1/private")
    def private():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/api/v1/private").status_code == 401
    assert client.get("/api/v1/private", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v1/private", headers={"Authorization": "Bearer test-api-key"}).status_code == 200


def test_download_path_must_stay_in_original_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.ORIGINAL_DIR", tmp_path / "originals")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(PathTraversalError):
        validate_stored_file(str(outside))
