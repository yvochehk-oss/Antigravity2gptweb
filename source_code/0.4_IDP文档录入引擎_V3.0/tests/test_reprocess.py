from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main


client = TestClient(main.app)


def test_reprocess_uses_stored_original_and_marks_forced(monkeypatch, tmp_path):
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

    calls = []

    def fake_process(path):
        calls.append(path)
        return {
            "sha256": "sha-stored",
            "document_type": "contract",
            "parser": "native_pdf",
            "page_count": 1,
            "ocr_confidence": None,
            "data": {"contract_no": "HT-001"},
            "validation": {"errors": [], "warnings": []},
            "audit": {"status": "disabled", "risks": []},
            "status": "approved",
            "_raw_text": "测试合同",
        }

    monkeypatch.setattr(main.pipeline, "process", fake_process)
    monkeypatch.setattr(
        main,
        "_persist_result",
        lambda **_kwargs: {
            "enabled": True,
            "stored": True,
            "status": "committed",
            "document_id": "doc-1",
            "extraction_id": "ext-2",
            "review_id": None,
            "committed": True,
        },
    )

    response = client.post("/api/v3/documents/doc-1/reprocess")

    assert response.status_code == 200
    assert calls == [stored]
    body = response.json()
    assert body["status"] == "committed"
    assert body["persistence"]["forced_reprocess"] is True
