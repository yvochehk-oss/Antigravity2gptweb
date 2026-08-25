"""Focused regressions for mount/import and regulation runtime stability."""

from __future__ import annotations

from pathlib import Path

import pytest


class _FakeDB:
    def scalar(self, _statement):
        return None

    def add(self, _value):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None

    def refresh(self, _value):
        return None


def test_register_local_path_reuses_upload_validation(tmp_path, monkeypatch):
    import app.config as config
    from app.models import Project
    from app.services import documents
    from app.services.storage import write as storage_write

    root = tmp_path / "imports"
    root.mkdir()
    monkeypatch.setattr(storage_write, "SAFE_ORIGIN_DIRS", [root])
    monkeypatch.setattr(config, "SAFE_ORIGIN_DIRS", [root])
    source = root / "A01_contract.md"
    source.write_text("# 合同\n第一条 这是受信文档。", encoding="utf-8")
    project = Project(
        id=1,
        project_code="P-LOCAL",
        name="本地导入项目",
        contract_amount=0,
        location="未填写",
    )

    document, job_id = documents.register_local_path(
        _FakeDB(), project, source, metadata={"document_type": "contract"}, auto_parse=False
    )

    assert job_id is None
    assert document.original_path == str(source.resolve())
    assert document.size_bytes == source.stat().st_size
    assert document.document_type == "contract"


def test_register_local_path_rejects_symlink_and_bad_magic(tmp_path, monkeypatch):
    from app.models import Project
    from app.services import documents
    from app.services.storage import PathTraversalError, StorageError
    from app.services.storage import write as storage_write

    root = tmp_path / "imports"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    monkeypatch.setattr(storage_write, "SAFE_ORIGIN_DIRS", [root])
    project = Project(
        id=1,
        project_code="P-LOCAL",
        name="本地导入项目",
        contract_amount=0,
        location="未填写",
    )

    (outside / "escape.md").write_text("secret", encoding="utf-8")
    link = root / "escape.md"
    link.symlink_to(outside / "escape.md")
    with pytest.raises(PathTraversalError):
        documents.register_local_path(_FakeDB(), project, link, auto_parse=False)

    invalid_pdf = root / "bad.pdf"
    invalid_pdf.write_bytes(b"not a pdf")
    with pytest.raises(ValueError, match="signature"):
        documents.register_local_path(_FakeDB(), project, invalid_pdf, auto_parse=False)

    unsupported = root / "payload.exe"
    unsupported.write_bytes(b"not an import")
    with pytest.raises(StorageError, match="Unsupported file type"):
        documents.register_local_path(_FakeDB(), project, unsupported, auto_parse=False)


def test_mount_browser_exposes_only_configured_safe_roots(tmp_path, monkeypatch):
    from app.routers import mounts
    from app.services.storage import write as storage_write

    root = tmp_path / "imports"
    root.mkdir()
    (root / "project").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(storage_write, "SAFE_ORIGIN_DIRS", [root])
    monkeypatch.setattr(mounts, "SAFE_ORIGIN_DIRS", [root])

    body = mounts.browse_fs(str(root))
    assert body["current"] == str(root.resolve())
    assert body["items"][0]["path"] == str((root / "project").resolve())
    with pytest.raises(mounts.HTTPException) as error:
        mounts.browse_fs(str(outside))
    assert error.value.status_code == 400


def test_regulation_article_route_uses_matching_path_parameter():
    source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert '@app.post("/api/v1/regulations/{regulation_id}/articles")' in source
    assert '@app.post("/api/v1/regulations/{registration_id}/articles")' not in source


def test_regulation_url_fetch_rejects_ssrf_before_open(monkeypatch):
    from app.services import ai_regulation_extractor as extractor

    def reject(_url):
        raise ValueError("private destination")

    monkeypatch.setattr(extractor, "validate_outbound_url", reject)
    with pytest.raises(ValueError, match="private destination"):
        extractor.fetch_url_content("https://127.0.0.1/internal")


def test_regulation_url_fetch_enforces_response_limit(monkeypatch):
    from app.services import ai_regulation_extractor as extractor

    class Response:
        headers = {"Content-Length": "5"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return "https://example.com/regulation"

        def read(self, _size):
            return b"large"

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(extractor, "validate_outbound_url", lambda url: url)
    monkeypatch.setattr(extractor.urllib.request, "build_opener", lambda *_args: Opener())
    monkeypatch.setattr(extractor, "_MAX_FETCH_BYTES", 4)
    with pytest.raises(ValueError, match="响应超过"):
        extractor.fetch_url_content("https://example.com/regulation")
