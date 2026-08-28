"""Tests for entity importer parsing and database ingestion logic."""
import io
import openpyxl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Entity
from app.services.entity_importer import (
    parse_entities_from_excel,
    _deduce_role_from_name,
    import_entities_from_file_bytes,
)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_test_db():
    Entity.__table__.create(bind=test_engine, checkfirst=True)
    yield
    Entity.__table__.drop(bind=test_engine, checkfirst=True)


def test_parse_entities_from_excel():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["单位代码", "单位全称", "统一社会信用代码", "法定代表人", "注册资本"])
    ws.append(["A01", "成都建工集团有限公司", "91510100201980000X", "张三", "10000万元"])
    ws.append(["B01", "成都采云商贸有限公司", "91510100789000000Y", "李四", "500万元"])
    
    buf = io.BytesIO()
    wb.save(buf)
    
    results = parse_entities_from_excel(buf.getvalue())
    assert len(results) == 2
    assert results[0]["entity_code"] == "A01"
    assert results[0]["name"] == "成都建工集团有限公司"
    assert results[0]["tax_id"] == "91510100201980000X"
    assert results[0]["legal_representative"] == "张三"
    
    assert results[1]["entity_code"] == "B01"
    assert results[1]["name"] == "成都采云商贸有限公司"


def test_import_entities_from_file_bytes_upsert():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["单位代码", "单位全称", "统一社会信用代码", "法定代表人", "注册资本", "业务角色"])
    ws.append(["A01", "成都建工集团有限公司", "91510100201980000X", "张三", "10000万元", "A"])
    ws.append(["B01", "成都采云商贸有限公司", "91510100789000000Y", "李四", "500万元", "B"])
    
    buf = io.BytesIO()
    wb.save(buf)

    db = TestingSessionLocal()
    try:
        res = import_entities_from_file_bytes(db, buf.getvalue(), "test_companies.xlsx")
        assert res["success"] is True
        assert res["created_count"] == 2
        
        # Verify entities in db
        ent_a01 = db.query(Entity).filter(Entity.entity_code == "A01").first()
        assert ent_a01 is not None
        assert ent_a01.name == "成都建工集团有限公司"
        assert ent_a01.tax_id == "91510100201980000X"
        assert ent_a01.legal_representative == "张三"

        # Re-importing same file updates rather than duplicates
        res2 = import_entities_from_file_bytes(db, buf.getvalue(), "test_companies.xlsx")
        assert res2["updated_count"] == 2
        assert res2["created_count"] == 0
    finally:
        db.close()


def test_deduce_role_from_name():
    assert _deduce_role_from_name("成都劳务发展有限公司") == "C"
    assert _deduce_role_from_name("四川机械租赁有限公司") == "D"
    assert _deduce_role_from_name("成都采云商贸有限公司") == "B"
    assert _deduce_role_from_name("成都建工第一建筑工程有限公司") == "A"
