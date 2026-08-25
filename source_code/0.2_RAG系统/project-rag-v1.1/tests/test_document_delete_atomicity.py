"""Document deletion transaction and cleanup-failure regression tests."""

from __future__ import annotations

import shutil
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select


@pytest.fixture
def staged_storage_fixture(tmp_path, monkeypatch):
    import app.services.storage.write as storage_write

    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(storage_write, "DATA_DIR", data_root)
    monkeypatch.setattr(storage_write, "_DELETE_STAGING_ROOT", data_root / ".document-delete-staging")
    monkeypatch.setattr(storage_write, "_DOCUMENT_STORAGE_ROOTS", (data_root.resolve(),))

    original = data_root / "originals" / "DOC-STORAGE" / "source.txt"
    parsed = data_root / "parsed" / "DOC-STORAGE"
    original.parent.mkdir(parents=True)
    parsed.mkdir(parents=True)
    original.write_bytes(b"staged-original")
    (parsed / "content.md").write_text("staged parsed", encoding="utf-8")
    return {
        "original": original,
        "parsed": parsed,
        "data_root": data_root,
        "storage": storage_write,
    }


def test_storage_document_cleanup_stage_and_finalize(staged_storage_fixture):
    fixture = staged_storage_fixture
    transaction = fixture["storage"].stage_document_cleanup(
        str(fixture["original"]), str(fixture["parsed"])
    )
    assert transaction.transaction_dir is not None
    assert not fixture["original"].exists()
    assert not fixture["parsed"].exists()
    assert transaction.manifest_path.is_file()

    fixture["storage"].finalize_document_cleanup(transaction)
    assert not transaction.transaction_dir.exists()
    assert not fixture["original"].exists()
    assert not fixture["parsed"].exists()


def test_storage_document_cleanup_restores_after_db_rollback(staged_storage_fixture):
    fixture = staged_storage_fixture
    storage = fixture["storage"]
    transaction = storage.stage_document_cleanup(
        str(fixture["original"]), str(fixture["parsed"])
    )
    storage.restore_document_cleanup(transaction)
    assert fixture["original"].read_bytes() == b"staged-original"
    assert (fixture["parsed"] / "content.md").read_text(encoding="utf-8") == "staged parsed"
    assert transaction.rolled_back is True
    assert not list((fixture["data_root"] / ".document-delete-staging").iterdir())


def test_storage_refuses_shared_parsed_directory(staged_storage_fixture):
    fixture = staged_storage_fixture
    shared = fixture["original"].parent
    (shared / "another-document.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(fixture["storage"].StorageError, match="beyond the document original"):
        fixture["storage"].stage_document_cleanup(str(fixture["original"]), str(shared))
    assert fixture["original"].exists()
    assert (shared / "another-document.txt").exists()


@pytest.fixture
def delete_document_fixture(postgres_test_database_url, tmp_path, monkeypatch):
    """Create one isolated document and point storage at a temporary root."""
    del postgres_test_database_url

    import app.services.storage.write as storage_write

    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(storage_write, "DATA_DIR", data_root)
    monkeypatch.setattr(storage_write, "_DELETE_STAGING_ROOT", data_root / ".document-delete-staging")
    monkeypatch.setattr(storage_write, "_DOCUMENT_STORAGE_ROOTS", (data_root.resolve(),))

    from app.db import SessionLocal
    from app.models import Chunk, Document, IngestJob, Project

    project_code = f"DELETE-TEST-{uuid.uuid4().hex[:12].upper()}"
    original = data_root / "originals" / project_code / "DOC-DELETE"
    parsed = data_root / "parsed" / "DOC-DELETE"
    original.parent.mkdir(parents=True)
    parsed.mkdir(parents=True)
    original.write_bytes(b"delete-transaction-original")
    (parsed / "content.md").write_text("parsed content", encoding="utf-8")

    with SessionLocal() as db:
        project = Project(
            project_code=project_code,
            name="delete transaction test",
            contract_amount=0,
            location="test",
        )
        db.add(project)
        db.flush()
        document = Document(
            project_id=project.id,
            document_code="DOC-DELETE",
            filename="delete.txt",
            file_type="txt",
            file_hash="0" * 64,
            size_bytes=original.stat().st_size,
            original_path=str(original),
            parsed_dir=str(parsed),
        )
        db.add(document)
        db.flush()
        db.add(
            Chunk(
                document_id=document.id,
                project_id=project.id,
                chunk_index=0,
                content="chunk",
                embedding_json="[]",
                search_text="chunk",
            )
        )
        db.add(IngestJob(document_id=document.id))
        db.commit()
        document_id = document.id

    yield {
        "document_id": document_id,
        "project_id": project.id,
        "project_code": project_code,
        "original": original,
        "parsed": parsed,
        "data_root": data_root,
    }

    with SessionLocal() as db:
        db.execute(delete(Chunk).where(Chunk.document_id == document_id))
        db.execute(delete(IngestJob).where(IngestJob.document_id == document_id))
        db.execute(delete(Document).where(Document.id == document_id))
        db.execute(delete(Project).where(Project.id == project.id))
        db.commit()
    staging = data_root / ".document-delete-staging"
    if staging.exists():
        shutil.rmtree(staging)


def _get_client():
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_delete_document_removes_rows_vectors_jobs_and_storage(delete_document_fixture):
    fixture = delete_document_fixture
    from app.db import SessionLocal
    from app.models import Chunk, Document, IngestJob

    with _get_client() as client:
        response = client.delete(f"/api/v1/documents/{fixture['document_id']}")

    assert response.status_code == 200
    assert response.json() == {
        "deleted": True,
        "database_deleted": True,
        "cleanup_status": "complete",
        "document_id": fixture["document_id"],
    }
    assert not fixture["original"].exists()
    assert not fixture["parsed"].exists()
    with SessionLocal() as db:
        assert db.scalar(select(Document.id).where(Document.id == fixture["document_id"])) is None
        assert db.scalar(select(Chunk.id).where(Chunk.document_id == fixture["document_id"])) is None
        assert db.scalar(select(IngestJob.id).where(IngestJob.document_id == fixture["document_id"])) is None


def test_delete_document_restores_storage_when_db_commit_fails(delete_document_fixture, monkeypatch):
    fixture = delete_document_fixture
    from app import session as app_session

    real_session_factory = app_session.SessionLocal

    class CommitFailingSession:
        def __init__(self):
            self._session = real_session_factory()

        def commit(self):
            raise RuntimeError("injected commit failure")

        def __getattr__(self, name):
            return getattr(self._session, name)

    monkeypatch.setattr(app_session, "SessionLocal", CommitFailingSession)
    with _get_client() as client:
        response = client.delete(f"/api/v1/documents/{fixture['document_id']}")

    assert response.status_code == 500
    body = response.json()["detail"]
    assert body["deleted"] is False
    assert body["database_deleted"] is False
    assert body["cleanup_status"] == "rolled_back"
    assert fixture["original"].read_bytes() == b"delete-transaction-original"
    assert (fixture["parsed"] / "content.md").read_text(encoding="utf-8") == "parsed content"


def test_delete_document_reports_pending_when_cleanup_fails(delete_document_fixture, monkeypatch):
    fixture = delete_document_fixture
    from app import main
    from app.db import SessionLocal
    from app.models import Chunk, Document, IngestJob
    from app.services.storage.write import StorageError

    def fail_finalize(_transaction):
        raise StorageError("injected cleanup failure")

    monkeypatch.setattr(main, "finalize_document_cleanup", fail_finalize)
    with _get_client() as client:
        response = client.delete(f"/api/v1/documents/{fixture['document_id']}")

    assert response.status_code == 503
    body = response.json()["detail"]
    assert body["deleted"] is False
    assert body["database_deleted"] is True
    assert body["cleanup_status"] == "pending"
    assert not fixture["original"].exists()
    manifests = list((fixture["data_root"] / ".document-delete-staging").glob("*/manifest.json"))
    assert manifests
    with SessionLocal() as db:
        assert db.scalar(select(Document.id).where(Document.id == fixture["document_id"])) is None
        assert db.scalar(select(Chunk.id).where(Chunk.document_id == fixture["document_id"])) is None
        assert db.scalar(select(IngestJob.id).where(IngestJob.document_id == fixture["document_id"])) is None
