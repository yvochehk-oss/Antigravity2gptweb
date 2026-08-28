from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main


client = TestClient(main.app)


def _result(sha256: str = "sha-test") -> dict:
    return {
        "sha256": sha256,
        "document_type": "invoice",
        "parser": "native_pdf",
        "page_count": 1,
        "ocr_confidence": None,
        "data": {"invoice_no": "TEST-001"},
        "validation": {"errors": [], "warnings": []},
        "audit": {"status": "disabled", "risks": []},
        "status": "approved",
        "_raw_text": "测试发票",
    }


def test_duplicate_sha_returns_persisted_result_without_running_pipeline(monkeypatch):
    monkeypatch.setattr(main, "repository", SimpleNamespace(enabled=True))
    monkeypatch.setattr(main.pipeline, "sha256", lambda _path: "sha-existing")

    def fail_if_processed(_path):
        raise AssertionError("duplicate SHA must not run OCR/LLM pipeline")

    monkeypatch.setattr(main.pipeline, "process", fail_if_processed)
    monkeypatch.setattr(
        main,
        "get_latest_result_by_sha",
        lambda _repository, _sha: {
            "sha256": "sha-existing",
            "document_type": "invoice",
            "parser": "native_pdf",
            "page_count": 1,
            "ocr_confidence": None,
            "data": {"invoice_no": "OLD-001"},
            "validation": {},
            "audit": {},
            "status": "committed",
            "persistence": {
                "enabled": True,
                "stored": True,
                "duplicate": True,
                "document_id": "doc-1",
                "extraction_id": "ext-1",
                "review_id": None,
                "status": "committed",
                "committed": True,
            },
        },
    )

    response = client.post(
        "/api/v3/documents/process",
        files={"file": ("invoice.pdf", b"%PDF-1.4\n", "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["duplicate"] is True
    assert body["status"] == "committed"
    assert body["data"]["invoice_no"] == "OLD-001"


def test_force_true_bypasses_sha_shortcut_and_runs_pipeline(monkeypatch):
    monkeypatch.setattr(main, "repository", SimpleNamespace(enabled=True))
    monkeypatch.setattr(main.pipeline, "sha256", lambda _path: "sha-force")

    def fail_if_lookup_runs(_repository, _sha):
        raise AssertionError("force=true must bypass duplicate shortcut lookup")

    monkeypatch.setattr(main, "get_latest_result_by_sha", fail_if_lookup_runs)

    calls = []

    def fake_process(path):
        calls.append(path)
        return _result("sha-force")

    monkeypatch.setattr(main.pipeline, "process", fake_process)
    monkeypatch.setattr(main, "_store_original", lambda path, _sha, _suffix: path)
    monkeypatch.setattr(
        main,
        "_persist_result",
        lambda **_kwargs: {
            "enabled": True,
            "stored": True,
            "status": "committed",
            "document_id": "doc-force",
            "extraction_id": "ext-force",
            "review_id": None,
            "committed": True,
        },
    )

    response = client.post(
        "/api/v3/documents/process?force=true",
        files={"file": ("invoice.pdf", b"%PDF-1.4\n", "application/pdf")},
    )

    assert response.status_code == 200
    assert len(calls) == 1
    body = response.json()
    assert body["status"] == "committed"
    assert body["persistence"]["forced_reprocess"] is True
