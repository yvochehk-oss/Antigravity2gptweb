"""Tests for idempotent scanning, duplicate child purging, and strong evidence classification."""

from decimal import Decimal
from pathlib import Path
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import Base, Document, Project, Chunk, IngestJob
from app.services.documents import (
    purge_redundant_duplicate_documents,
    repair_filename_classifications,
    scan_folder,
)
from app.services.metadata import infer_from_filename
import app.services.storage.write as storage_write


@pytest.fixture
def test_db():
    """Provide a fresh SQLite in-memory test database with document core tables."""
    engine = create_engine("sqlite:///:memory:")
    Project.__table__.create(engine)
    Document.__table__.create(engine)
    Chunk.__table__.create(engine)
    IngestJob.__table__.create(engine)
    SessionMaker = sessionmaker(bind=engine)
    session = SessionMaker()
    yield session
    session.close()


def test_strong_evidence_classification_priority():
    """Verify strong evidence/prefix rules beat contract keywords."""
    f1 = "TAX_CERT_TF-A08_202403_机械租赁与劳务分包合同印花税完税证明.pdf"
    r1 = infer_from_filename(f1)
    assert r1["document_type"] == "tax_payment_record"
    assert r1["tax_category"] == "stamp_duty"
    assert r1["classification_source"] == "prefix:TAX_CERT_"

    f2 = "TAX_CERT_A11_2025Q2_超高层钢结构专业分包增值税及附加完税证明.pdf"
    r2 = infer_from_filename(f2)
    assert r2["document_type"] == "tax_payment_record"
    assert r2["tax_category"] == "vat"

    f3 = "INVOICE_TF-A08_202403_塔吊设备租赁增值税专用发票.pdf"
    r3 = infer_from_filename(f3)
    assert r3["document_type"] == "tax_invoice"
    assert r3["tax_category"] == "vat"

    f4 = "01_建筑设备租赁合同_TF-A08_202403.pdf"
    r4 = infer_from_filename(f4)
    assert r4["document_type"] == "equipment_contract"
    assert r4["business_category"] == "equipment"


def test_idempotent_register_and_scan(test_db, tmp_path, monkeypatch):
    """Verify registering or scanning the same file multiple times is strictly idempotent."""
    monkeypatch.setattr(storage_write, "SAFE_ORIGIN_DIRS", [*storage_write.SAFE_ORIGIN_DIRS, tmp_path.resolve()])
    project = Project(id=1, project_code="CD-TF-001", name="天府国际金融中心二期", contract_amount=Decimal("0.00"), location="")
    test_db.add(project)
    test_db.commit()

    scan_dir = tmp_path / "scan_source"
    scan_dir.mkdir()

    pdf1 = scan_dir / "TAX_CERT_TF-A08_202403_机械租赁与劳务分包合同印花税完税证明.pdf"
    pdf1.write_bytes(b"%PDF-1.4 test document content A")

    pdf2 = scan_dir / "01_建筑设备租赁合同_TF-A08_202403.pdf"
    pdf2.write_bytes(b"%PDF-1.4 test document content B")

    # First scan
    results1 = scan_folder(test_db, project, scan_dir, recursive=False, auto_parse=False)
    assert len(results1) == 2
    assert all(not r.get("skipped_duplicate") for r in results1)
    assert len(test_db.scalars(select(Document).where(Document.project_id == 1)).all()) == 2

    # Second scan with identical files
    results2 = scan_folder(test_db, project, scan_dir, recursive=False, auto_parse=False)
    assert len(results2) == 2
    assert all(r.get("skipped_duplicate") for r in results2)
    # Total documents count should still be exactly 2 (no duplicate row explosion!)
    assert len(test_db.scalars(select(Document).where(Document.project_id == 1)).all()) == 2


def test_purge_redundant_duplicate_documents(test_db):
    """Verify historical DUPLICATE rows are purged without touching canonical documents."""
    project = Project(id=1, project_code="CD-TF-001", name="天府国际金融中心二期", contract_amount=Decimal("0.00"), location="")
    test_db.add(project)
    test_db.commit()

    doc_canonical = Document(
        id=101,
        project_id=1,
        document_code="DOC-CANONICAL",
        filename="合同.pdf",
        file_type="pdf",
        file_hash="hash_abc",
        size_bytes=100,
        original_path="/dummy/canonical.pdf",
        parse_status="INDEXED",
        duplicate_of_id=None,
    )
    doc_duplicate1 = Document(
        id=102,
        project_id=1,
        document_code="DOC-DUP-1",
        filename="合同.pdf",
        file_type="pdf",
        file_hash="hash_abc",
        size_bytes=100,
        original_path="/dummy/dup1.pdf",
        parse_status="DUPLICATE",
        duplicate_of_id=101,
    )
    doc_duplicate2 = Document(
        id=103,
        project_id=1,
        document_code="DOC-DUP-2",
        filename="合同.pdf",
        file_type="pdf",
        file_hash="hash_abc",
        size_bytes=100,
        original_path="/dummy/dup2.pdf",
        parse_status="DUPLICATE",
        duplicate_of_id=101,
    )
    test_db.add_all([doc_canonical, doc_duplicate1, doc_duplicate2])
    test_db.commit()

    res = purge_redundant_duplicate_documents(test_db, 1)
    assert res["removed"] == 2

    remaining = test_db.scalars(select(Document).where(Document.project_id == 1)).all()
    assert len(remaining) == 1
    assert remaining[0].id == 101


def test_repair_filename_classifications(test_db):
    """Verify repair_filename_classifications updates misclassified documents."""
    project = Project(id=1, project_code="CD-TF-001", name="天府国际金融中心二期", contract_amount=Decimal("0.00"), location="")
    test_db.add(project)
    test_db.commit()

    misclassified = Document(
        id=201,
        project_id=1,
        document_code="DOC-MISCLASSIFIED",
        filename="TAX_CERT_TF-A08_202403_机械租赁与劳务分包合同印花税完税证明.pdf",
        file_type="pdf",
        file_hash="hash_tax",
        size_bytes=100,
        original_path="/dummy/tax1.pdf",
        parse_status="INDEXED",
        duplicate_of_id=None,
        metadata_source="filename",
        document_type="equipment_contract",  # Erroneous
        business_category="equipment",      # Erroneous
        tax_category="",
    )
    user_customized = Document(
        id=202,
        project_id=1,
        document_code="DOC-USER-CUSTOM",
        filename="TAX_CERT_TF-A08_202403_机械租赁与劳务分包合同印花税完税证明.pdf",
        file_type="pdf",
        file_hash="hash_tax_custom",
        size_bytes=100,
        original_path="/dummy/tax2.pdf",
        parse_status="INDEXED",
        duplicate_of_id=None,
        metadata_source="user",  # User manually edited
        document_type="custom_type",
        business_category="custom_cat",
        tax_category="",
    )
    test_db.add_all([misclassified, user_customized])
    test_db.commit()

    rep_res = repair_filename_classifications(test_db, 1)
    assert rep_res["reclassified"] == 1

    test_db.refresh(misclassified)
    test_db.refresh(user_customized)

    assert misclassified.document_type == "tax_payment_record"
    assert misclassified.tax_category == "stamp_duty"
    assert user_customized.document_type == "custom_type"  # Preserved!
