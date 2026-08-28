"""Tests for regulation batch directory import and Pkulaw sync service."""
import os
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Regulation, RegulationChunk
from app.services.regulations_batch_importer import batch_import_regulations_from_dir
from app.services.pkulaw_sync_service import sync_pkulaw_regulations

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_test_db():
    Regulation.__table__.create(bind=test_engine, checkfirst=True)
    RegulationChunk.__table__.create(bind=test_engine, checkfirst=True)
    yield
    RegulationChunk.__table__.drop(bind=test_engine, checkfirst=True)
    Regulation.__table__.drop(bind=test_engine, checkfirst=True)


def test_batch_import_regulations_from_dir(tmp_path):
    # Create test markdown regulation files
    reg1 = tmp_path / "test_reg1.md"
    reg1.write_text(
        "---\n"
        "title: \"四川省建筑工程增值税核算指引\"\n"
        "document_no: \"川税发〔2026〕12号\"\n"
        "issuer: \"四川省税务局\"\n"
        "legal_level: \"地方规范性文件\"\n"
        "jurisdiction: \"四川省\"\n"
        "tax_type: \"增值税\"\n"
        "industry: \"建筑业\"\n"
        "business_role: \"construction\"\n"
        "---\n\n"
        "# 四川省建筑工程增值税核算指引\n\n"
        "第一条 本指引适用于四川省境内建筑工程总承包业务。\n\n"
        "第二条 施工总承包方应按规定在建筑服务发生地预缴增值税。",
        encoding="utf-8",
    )

    reg2 = tmp_path / "test_reg2.md"
    reg2.write_text(
        "---\n"
        "title: \"成都市建筑劳务用工税收管理规定\"\n"
        "document_no: \"成税公告2026年第3号\"\n"
        "issuer: \"国家税务总局成都市税务局\"\n"
        "legal_level: \"地方规范性文件\"\n"
        "jurisdiction: \"成都市\"\n"
        "tax_type: \"个人所得税\"\n"
        "industry: \"建筑劳务\"\n"
        "business_role: \"labor\"\n"
        "---\n\n"
        "# 成都市建筑劳务用工税收管理规定\n\n"
        "第一条 规范劳务分包企业实名制工资代发与全员全额个税申报。",
        encoding="utf-8",
    )

    db = TestingSessionLocal()
    try:
        res = batch_import_regulations_from_dir(db, str(tmp_path), recursive=True)
        assert res["success"] is True
        assert res["total_scanned"] == 2
        assert res["created_count"] == 2
        assert res["failed_count"] == 0

        # Verify regulations table
        regs = db.query(Regulation).all()
        assert len(regs) == 2
        titles = {r.title for r in regs}
        assert "四川省建筑工程增值税核算指引" in titles
        assert "成都市建筑劳务用工税收管理规定" in titles

        # Verify chunks table
        chunks = db.query(RegulationChunk).all()
        assert len(chunks) >= 2

        # Re-import test (idempotence)
        res_repeat = batch_import_regulations_from_dir(db, str(tmp_path), recursive=True)
        assert res_repeat["created_count"] == 0
        assert res_repeat["updated_count"] == 2
    finally:
        db.close()


def test_pkulaw_sync_regulations(tmp_path):
    db = TestingSessionLocal()
    try:
        res = sync_pkulaw_regulations(
            db=db,
            api_key="",
            jurisdiction="四川省",
            categories=["建筑", "财务", "材料"],
            keywords="预缴税款",
            timeliness="现行有效",
            limit=2,
            local_dir=str(tmp_path / "pkulaw_sync_test"),
        )
        assert res["success"] is True
        assert res["total_synced"] >= 1
        assert res["created_count"] >= 1

        # Check local markdown files written
        saved_files = list((tmp_path / "pkulaw_sync_test").glob("*.md"))
        assert len(saved_files) >= 1
        content = saved_files[0].read_text(encoding="utf-8")
        assert "---" in content
        assert "title:" in content
        assert "document_no:" in content

        # Check database records
        regs = db.query(Regulation).all()
        assert len(regs) >= 1
    finally:
        db.close()
