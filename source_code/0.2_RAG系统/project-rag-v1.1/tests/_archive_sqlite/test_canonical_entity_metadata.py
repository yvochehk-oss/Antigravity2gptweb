"""Regression tests for the real-company RAG entity contract."""

import pytest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import Entity, Project
from app.schemas import EntityCreate, ProjectSync
from app.services.documents import register_bytes
from app.services.metadata import infer_from_filename, resolve_entity_reference
from app.services.metadata_confidence import score_metadata_confidence
from app.services.tax_extraction import (
    ExtractedFieldsInvoice,
    ExtractedFieldsPayment,
    normalize_extracted_fields,
)
from scripts.import_entities import validate_master_entities


@pytest.fixture
def runtime_entity_db(tmp_path, monkeypatch):
    """Give ``register_bytes`` a real, isolated runtime Entity database.

    The production lookup opens its own ``app.db.SessionLocal`` session.  A
    temporary file-backed engine makes that path real (including SQLite pool
    checkout/return behavior) while keeping the operator database entirely
    out of scope.  ``unified_social_credit_code`` is deliberately duplicated
    for two rows with a null canonical ``tax_id``: this models legacy runtime
    data that resolves to the same effective tax identifier without disabling
    the schema's canonical ``tax_id`` uniqueness constraint.
    """
    import app.db as db_module
    from app.services.storage import write as storage_write

    database = tmp_path / "runtime-entities.db"
    engine = create_engine(
        f"sqlite:///{database}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)

    opened: list[Session] = []
    closed: list[Session] = []

    class TrackingSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            opened.append(self)

        def close(self):
            if self not in closed:
                closed.append(self)
            return super().close()

    RuntimeSession = sessionmaker(
        bind=engine,
        class_=TrackingSession,
        autoflush=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(db_module, "SessionLocal", RuntimeSession)

    storage_root = tmp_path / "originals"
    storage_root.mkdir()
    monkeypatch.setattr(storage_write, "ORIGINAL_DIR", storage_root)
    monkeypatch.setattr(storage_write, "SAFE_ORIGIN_DIRS", [storage_root.resolve()])

    db = RuntimeSession()
    db.add(
        Project(
            project_code="RUNTIME-ENTITY-TEST",
            name="runtime entity resolution test",
            external_system="test",
            external_project_id="RUNTIME-ENTITY-TEST",
            entity_code=None,
            status="ACTIVE",
        )
    )
    db.add_all(
        [
            Entity(
                entity_code="A01",
                name="四川测试建设有限公司",
                tax_id="91510100A01TEST001",
                status="active",
                business_role="A",
            ),
            Entity(
                entity_code="A02",
                name="成都测试贸易有限公司",
                tax_id="91510100A02TEST002",
                status="active",
                business_role="B",
            ),
            Entity(
                entity_code="A03",
                name="重复名称企业",
                tax_id="91510100A03TEST003",
                status="active",
                business_role="A",
            ),
            Entity(
                entity_code="A05",
                name="重复名称企业",
                tax_id="91510100A05TEST005",
                status="active",
                business_role="A",
            ),
            Entity(
                entity_code="A06",
                name="重复税号企业一",
                tax_id=None,
                unified_social_credit_code="9151010DUPTAX00111",
                status="active",
                business_role="A",
            ),
            Entity(
                entity_code="A07",
                name="重复税号企业二",
                tax_id=None,
                unified_social_credit_code="9151010DUPTAX00111",
                status="active",
                business_role="A",
            ),
            Entity(
                entity_code="A08",
                name="停用测试公司",
                tax_id="91510100INACTV001",
                status="inactive",
                business_role="A",
            ),
            Entity(
                entity_code=None,
                name="外部测试供应商",
                tax_id="91510100EXTTEST001",
                status="active",
                business_role="",
            ),
        ]
    )
    db.commit()
    project = db.scalar(
        select(Project).where(Project.project_code == "RUNTIME-ENTITY-TEST")
    )
    assert project is not None

    try:
        yield {
            "db": db,
            "project": project,
            "engine": engine,
            "opened": opened,
            "closed": closed,
        }
    finally:
        db.close()
        engine.dispose()


def test_virtual_labels_are_not_entity_codes():
    for token in ("A", "B", "C", "D", "甲", "乙", "丙", "丁"):
        result = infer_from_filename(f"{token}_合同.pdf", canonical_cache=[])
        assert result["entity_code"] == ""
        assert result["entity_resolution_status"] in {"UNRESOLVED", "REJECTED"}


def test_real_codes_resolve_and_unavailable_cache_stays_unresolved():
    cache = [{"entity_code": "A01"}, {"entity_code": "D01"}]
    assert infer_from_filename("A01_施工合同.pdf", canonical_cache=cache)["entity_code"] == "A01"
    assert infer_from_filename("D01_设备合同.pdf", canonical_cache=cache)["entity_code"] == "D01"
    unresolved = infer_from_filename("A01_施工合同.pdf", canonical_cache=[])
    assert unresolved["entity_code"] == ""
    assert unresolved["entity_code_candidate"] == "A01"
    assert unresolved["entity_resolution_status"] == "UNRESOLVED"


def test_a04_branch_parent_and_project_single_entity_constraint():
    entity = EntityCreate(
        name="四川屹明汇建设工程有限公司重庆分公司",
        entity_code="A04",
        tax_id="91500230MAD7T9Y43P",
        legal_entity=False,
        parent_entity_code="A03",
    )
    assert entity.entity_kind == "branch"
    assert entity.parent_entity_code == "A03"
    with pytest.raises(ValueError):
        EntityCreate(name="bad", entity_code="A04", legal_entity=True, parent_entity_code="A03")
    with pytest.raises(ValueError):
        ProjectSync(project_code="P", name="multi", entity_code="A01,B01")


def test_duplicate_tax_id_is_a_hard_import_failure():
    rows = [
        {"entity_code": "A01", "name": "公司一", "business_role": "A", "tax_id": "T1"},
        {"entity_code": "A02", "name": "公司二", "business_role": "A", "tax_id": "T1"},
    ]
    with pytest.raises(ValueError, match="duplicate tax_id"):
        validate_master_entities(rows, require_all_codes=False)


def test_name_lookup_requires_unique_match():
    cache = [
        {"entity_code": "A01", "name": "同名公司", "tax_id": "T1"},
        {"entity_code": "A02", "name": "同名公司", "tax_id": "T2"},
    ]
    result = resolve_entity_reference("同名公司", cache)
    assert result["status"] == "CONFLICT"
    assert result["entity_code"] == ""


def test_register_bytes_resolves_runtime_code_name_and_tax_id(runtime_entity_db):
    runtime = runtime_entity_db
    db = runtime["db"]
    project = runtime["project"]

    code_doc, code_job = register_bytes(
        db,
        project,
        "A01_施工合同.md",
        b"runtime-code-resolution",
        auto_parse=False,
    )
    name_doc, name_job = register_bytes(
        db,
        project,
        "成都测试贸易有限公司_采购合同.md",
        b"runtime-name-resolution",
        auto_parse=False,
    )
    tax_doc, tax_job = register_bytes(
        db,
        project,
        "91510100A01TEST001_完税证明.md",
        b"runtime-tax-resolution",
        auto_parse=False,
    )

    assert code_job is None and code_doc.entity_code == "A01"
    assert name_job is None and name_doc.entity_code == "A02"
    assert tax_job is None and tax_doc.entity_code == "A01"
    assert code_doc.parse_status == name_doc.parse_status == tax_doc.parse_status == "UPLOADED"


def test_register_bytes_runtime_resolution_rejects_inactive_and_external_rows(
    runtime_entity_db,
):
    runtime = runtime_entity_db
    db = runtime["db"]
    project = runtime["project"]

    inactive, _ = register_bytes(
        db,
        project,
        "停用测试公司_历史合同.md",
        b"runtime-inactive-resolution",
        auto_parse=False,
    )
    external, _ = register_bytes(
        db,
        project,
        "外部测试供应商_对账单.md",
        b"runtime-external-resolution",
        auto_parse=False,
    )

    assert inactive.entity_code == ""
    assert external.entity_code == ""
    assert inactive.counterparty_code == ""
    assert external.counterparty_code == ""
    assert infer_from_filename("停用测试公司_历史合同.md")["entity_resolution_status"] == "UNRESOLVED"
    assert infer_from_filename("外部测试供应商_对账单.md")["entity_resolution_status"] == "UNRESOLVED"


def test_register_bytes_runtime_duplicate_name_and_tax_id_stays_unresolved(
    runtime_entity_db,
):
    runtime = runtime_entity_db
    db = runtime["db"]
    project = runtime["project"]

    duplicate_name, _ = register_bytes(
        db,
        project,
        "重复名称企业_合同.md",
        b"runtime-duplicate-name",
        auto_parse=False,
    )
    duplicate_tax, _ = register_bytes(
        db,
        project,
        "9151010DUPTAX00111_合同.md",
        b"runtime-duplicate-tax",
        auto_parse=False,
    )

    assert duplicate_name.entity_code == ""
    assert duplicate_tax.entity_code == ""
    assert infer_from_filename("重复名称企业_合同.md")["entity_resolution_status"] == "CONFLICT"
    assert infer_from_filename("9151010DUPTAX00111_合同.md")["entity_resolution_status"] == "CONFLICT"


def test_runtime_entity_lookup_closes_sessions_and_returns_connections(runtime_entity_db):
    runtime = runtime_entity_db
    db = runtime["db"]
    project = runtime["project"]
    engine = runtime["engine"]

    document, _ = register_bytes(
        db,
        project,
        "A01_连接归还验证.md",
        b"runtime-connection-return",
        auto_parse=False,
    )
    db.commit()
    db.close()

    assert document.entity_code == "A01"
    assert runtime["opened"]
    assert all(session in runtime["closed"] for session in runtime["opened"])
    assert engine.pool.checkedout() == 0


def test_single_letter_confidence_is_zero_and_party_fields_are_separate():
    assert score_metadata_confidence({"entity_code": "A"})["entity_code"].confidence == 0.0
    invoice = ExtractedFieldsInvoice(
        buyer_entity_code="A01",
        buyer_tax_id="TAX-BUYER",
        buyer_account="BANK-ACCOUNT",
        net_amount=100.0,
    )
    assert invoice.buyer_entity_code == "A01"
    assert invoice.buyer_tax_id == "TAX-BUYER"
    assert invoice.buyer_account == "BANK-ACCOUNT"
    payment = ExtractedFieldsPayment(
        payer_entity_code="D01",
        payer_tax_id="TAX-PAYER",
        payer_account="BANK-ACCOUNT",
        payer_bank_reference="REF-1",
        net_amount=90.0,
    )
    assert payment.payer_tax_id != payment.payer_account
    assert payment.payer_bank_reference == "REF-1"
    with pytest.raises(ValueError):
        ExtractedFieldsPayment(payer_entity_code="D")
    normalized = normalize_extracted_fields(
        "invoice",
        {"seller_code": "A", "seller_account": "A-ACCOUNT", "buyer_code": "TAX-BUYER"},
    )
    assert normalized["seller"]["entity_code"] is None
    assert normalized["seller"]["tax_id"] is None
    assert normalized["seller"]["account"] == "A-ACCOUNT"
    assert normalized["buyer"]["tax_id"] == "TAX-BUYER"
