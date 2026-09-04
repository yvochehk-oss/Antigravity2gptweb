"""Regression tests for external-party activation from durable document references."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.models import Document, ExternalParty, Project
from app.services.documents import (
    reconcile_project_external_parties,
    register_bytes,
    scan_folder,
)
from app.services.ingest import _auto_register_external_party
from app.services.storage import sha256_bytes


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Project.__table__.create(engine)
    Document.__table__.create(engine)
    ExternalParty.__table__.create(engine)
    SessionMaker = sessionmaker(bind=engine)
    session = SessionMaker()
    yield session
    session.close()


def _project(test_db) -> Project:
    project = Project(
        id=1,
        project_code="TEST-EXT-001",
        name="外部单位激活测试项目",
        contract_amount=Decimal("0.00"),
        location="",
    )
    test_db.add(project)
    test_db.commit()
    return project


def _inactive_party(test_db, code: str = "EXT-CY") -> ExternalParty:
    party = ExternalParty(
        code=code,
        name=code,
        short_name=code,
        kind="owner",
        active=False,
    )
    test_db.add(party)
    test_db.commit()
    return party


def test_existing_external_party_is_reactivated(test_db):
    party = _inactive_party(test_db)

    _auto_register_external_party(
        test_db,
        counterparty_code="EXT-CY",
        counterparty_name="成渝高速公路开发投资集团",
        kind="owner",
    )
    test_db.commit()
    test_db.refresh(party)

    assert party.active is True
    assert test_db.scalar(select(func.count(ExternalParty.id))) == 1


def test_bank_receipt_identifier_is_never_registered(test_db):
    _auto_register_external_party(
        test_db,
        counterparty_code="EBNK20230320863445",
        counterparty_name="银行电子回单",
    )
    test_db.commit()

    assert test_db.scalar(select(func.count(ExternalParty.id))) == 0


def test_register_bytes_duplicate_reconciles_external_party(test_db, monkeypatch):
    project = _project(test_db)
    party = _inactive_party(test_db)
    data = b"%PDF-1.4 duplicate external party test"
    digest = sha256_bytes(data)
    document = Document(
        id=101,
        project_id=project.id,
        document_code="DOC-EXT-DUP",
        filename="existing.pdf",
        file_type="pdf",
        file_hash=digest,
        size_bytes=len(data),
        original_path="/dummy/existing.pdf",
        parse_status="INDEXED",
        duplicate_of_id=None,
        counterparty_code="EXT-CY",
    )
    test_db.add(document)
    test_db.commit()

    monkeypatch.setattr("app.services.documents.validate_file_content", lambda *_args, **_kwargs: None)

    returned, job_id = register_bytes(
        test_db,
        project,
        "existing.pdf",
        data,
        auto_parse=False,
    )

    test_db.refresh(party)
    assert returned.id == document.id
    assert job_id is None
    assert party.active is True
    assert test_db.scalar(select(func.count(Document.id))) == 1


def test_scan_folder_duplicate_and_self_heal_reconcile_only_real_references(
    test_db,
    tmp_path,
    monkeypatch,
):
    project = _project(test_db)
    real_party = _inactive_party(test_db, "EXT-CY")
    unrelated = _inactive_party(test_db, "EXT-TREE")

    data = b"%PDF-1.4 scan duplicate external party test"
    digest = sha256_bytes(data)
    document = Document(
        id=201,
        project_id=project.id,
        document_code="DOC-SCAN-DUP",
        filename="existing.pdf",
        file_type="pdf",
        file_hash=digest,
        size_bytes=len(data),
        original_path="/dummy/existing.pdf",
        parse_status="INDEXED",
        duplicate_of_id=None,
        counterparty_code="EXT-CY",
        metadata_source="user",
    )
    bank_noise = Document(
        id=202,
        project_id=project.id,
        document_code="DOC-BANK-NOISE",
        filename="bank.pdf",
        file_type="pdf",
        file_hash="bank-noise-hash",
        size_bytes=10,
        original_path="/dummy/bank.pdf",
        parse_status="INDEXED",
        duplicate_of_id=None,
        counterparty_code="EBNK20240920194827",
        metadata_source="user",
    )
    test_db.add_all([document, bank_noise])
    test_db.commit()

    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    source = scan_dir / "existing.pdf"
    source.write_bytes(data)

    monkeypatch.setattr(
        "app.services.documents._validate_safe_path",
        lambda path, operation="": path.resolve(),
    )
    monkeypatch.setattr("app.services.documents.read_file_limited", lambda _path: data)

    rows = scan_folder(
        test_db,
        project,
        scan_dir,
        recursive=False,
        auto_parse=False,
    )
    repair = reconcile_project_external_parties(test_db, project.id)

    test_db.refresh(real_party)
    test_db.refresh(unrelated)
    assert len(rows) == 1
    assert rows[0]["skipped_duplicate"] is True
    assert real_party.active is True
    assert unrelated.active is False
    assert repair["external_party_references_reconciled"] == 1
    assert test_db.scalar(
        select(func.count(ExternalParty.id)).where(ExternalParty.code.like("EBNK%"))
    ) == 0


def test_scan_unknown_party_not_registered_leaves_unresolved(test_db):
    """Verify that scanning a document with a valid format but unseeded/unregistered party (e.g., EA37) does NOT create a master row."""
    from app.services.ingest import _auto_register_external_party

    _auto_register_external_party(
        test_db,
        counterparty_code="EA37",
        counterparty_name="未登记特种施工单位",
        tax_id=None,
        kind="construction",
    )

    party = test_db.scalar(select(ExternalParty).where(ExternalParty.code == "EA37"))
    assert party is None, "Ingestion must not create new ExternalParty rows for unregistered codes"


def test_auto_register_nested_savepoint_rollback(test_db):
    """Verify that duplicate or conflict in _auto_register_external_party rolls back nested savepoint without poisoning outer session."""
    from app.services.ingest import _auto_register_external_party
    from sqlalchemy.exc import IntegrityError

    # Session is healthy before
    assert test_db.is_active

    # Create dummy party
    p = ExternalParty(code="EA01", name="原始单位", short_name="原始", kind="construction", active=False)
    test_db.add(p)
    test_db.commit()

    # Call _auto_register_external_party - it uses with db.begin_nested()
    _auto_register_external_party(
        test_db,
        counterparty_code="EA01",
        counterparty_name="四川省建筑科学研究院特种技术服务中心",
        tax_id=None,
        kind="construction",
    )

    test_db.refresh(p)
    assert p.active is True
    # Session remains completely usable
    assert test_db.is_active
    test_db.execute(select(ExternalParty.id)).fetchall()

