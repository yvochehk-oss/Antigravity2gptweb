from __future__ import annotations

from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import main


def _database_failure(*_args, **_kwargs):
    raise psycopg.Error("TOP_SECRET_DATABASE_DETAILS")


def test_database_errors_are_logged_but_not_returned(monkeypatch, caplog):
    monkeypatch.setattr(main, "repository", SimpleNamespace(
        enabled=True,
        list_reviews=_database_failure,
        get_review=_database_failure,
        complete_review=_database_failure,
    ))
    monkeypatch.setattr(main, "get_latest_result_by_sha", _database_failure)
    monkeypatch.setattr(main, "get_document_for_reprocess", _database_failure)

    client = TestClient(main.app)
    headers = {"X-Request-ID": "test-db-error-001"}
    responses = [
        client.get("/api/v3/documents/by-sha/abc", headers=headers),
        client.get("/api/v3/documents/doc-1", headers=headers),
        client.post("/api/v3/documents/doc-1/reprocess", headers=headers),
        client.get("/api/v3/reviews", headers=headers),
        client.get("/api/v3/reviews/review-1", headers=headers),
        client.post(
            "/api/v3/reviews/review-1/complete",
            headers=headers,
            json={"action": "approve", "reviewer": "tester"},
        ),
    ]

    assert all(response.status_code == 503 for response in responses)
    for response in responses:
        assert "TOP_SECRET_DATABASE_DETAILS" not in response.text
        assert response.json()["detail"]["code"] == "database_unavailable"
        assert response.json()["detail"]["request_id"] == "test-db-error-001"
    assert "TOP_SECRET_DATABASE_DETAILS" in caplog.text


@pytest.mark.parametrize(
    ("error_type", "secret"),
    [
        (RuntimeError, "TOP_SECRET_PIPELINE_RUNTIME_DETAILS"),
        (OSError, "TOP_SECRET_PIPELINE_IO_DETAILS"),
    ],
)
def test_process_runtime_errors_are_logged_but_not_returned(monkeypatch, caplog, error_type, secret):
    monkeypatch.setattr(main, "repository", SimpleNamespace(enabled=False))

    def fail_if_processed(_path):
        raise error_type(secret)

    monkeypatch.setattr(main.pipeline, "process", fail_if_processed)
    response = TestClient(main.app).post(
        "/api/v3/documents/process",
        headers={"X-Request-ID": "test-runtime-process-001"},
        files={"file": ("invoice.pdf", b"%PDF-1.4\n", "application/pdf")},
    )

    assert response.status_code == 503
    assert secret not in response.text
    assert response.json()["detail"] == {
        "code": "service_unavailable",
        "request_id": "test-runtime-process-001",
    }
    assert secret in caplog.text
    assert "test-runtime-process-001" in caplog.text
    assert "operation=process_document" in caplog.text


@pytest.mark.parametrize(
    ("error_type", "secret"),
    [
        (RuntimeError, "TOP_SECRET_REPROCESS_RUNTIME_DETAILS"),
        (OSError, "TOP_SECRET_REPROCESS_IO_DETAILS"),
    ],
)
def test_reprocess_runtime_errors_are_logged_but_not_returned(
    monkeypatch, tmp_path, caplog, error_type, secret
):
    stored = tmp_path / "stored.pdf"
    stored.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(main, "repository", SimpleNamespace(enabled=True))
    monkeypatch.setattr(main, "storage_dir", tmp_path)
    monkeypatch.setattr(
        main,
        "get_document_for_reprocess",
        lambda _repository, _document_id: {
            "id": "doc-1",
            "sha256": "sha-stored",
            "filename": "stored.pdf",
            "file_type": "pdf",
            "file_path": str(stored),
            "document_type": "contract",
            "status": "needs_review",
        },
    )

    def fail_if_reprocessed(_path):
        raise error_type(secret)

    monkeypatch.setattr(main.pipeline, "process", fail_if_reprocessed)
    response = TestClient(main.app).post(
        "/api/v3/documents/doc-1/reprocess",
        headers={"X-Request-ID": "test-runtime-reprocess-001"},
    )

    assert response.status_code == 503
    assert secret not in response.text
    assert response.json()["detail"] == {
        "code": "service_unavailable",
        "request_id": "test-runtime-reprocess-001",
    }
    assert secret in caplog.text
    assert "test-runtime-reprocess-001" in caplog.text
    assert "operation=reprocess_document" in caplog.text
