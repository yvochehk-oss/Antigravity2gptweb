from __future__ import annotations

from fastapi.testclient import TestClient

from app import main


def test_upload_limit_is_checked_before_pipeline(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "max_upload_bytes", 1)

    def fail_if_processed(_path):
        raise AssertionError("oversized upload must be rejected before processing")

    monkeypatch.setattr(main.pipeline, "process", fail_if_processed)
    response = client.post(
        "/api/v3/documents/process",
        files={"file": ("invoice.pdf", b"12", "application/pdf")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "file_too_large"
