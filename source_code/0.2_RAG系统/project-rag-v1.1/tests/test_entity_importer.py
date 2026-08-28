"""Tests for entity importer parsing logic."""
import io
import openpyxl
import pytest
from app.services.entity_importer import (
    parse_entities_from_excel,
    _deduce_role_from_name,
)


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


def test_deduce_role_from_name():
    assert _deduce_role_from_name("成都劳务发展有限公司") == "C"
    assert _deduce_role_from_name("四川机械租赁有限公司") == "D"
    assert _deduce_role_from_name("成都采云商贸有限公司") == "B"
    assert _deduce_role_from_name("成都建工第一建筑工程有限公司") == "A"
