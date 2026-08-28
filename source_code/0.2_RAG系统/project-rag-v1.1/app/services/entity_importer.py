"""
entity_importer.py
------------------
Intelligent parser and importer for system-internal entities from Excel (.xlsx, .xls),
Word (.docx), and PDF (.pdf) files.

Extracts:
- 单位全称 (Company Name)
- 统一社会信用代码 / 税号 (Tax ID / Unified Social Credit Code)
- 单位代码 (Entity Code: A01-A11, B01-B10, C01-C02, D01-D03)
- 业务角色 (Business Role: A施工 / B商贸 / C劳务 / D租赁)
- 法定代表人 (Legal Representative)
- 注册资本 (Registered Capital)
- 成立日期 (Establishment Date)
- 经营范围 / 备注 (Business Scope / Note)
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..domain.entities import (
    CANONICAL_ENTITY_CODES,
    is_canonical_entity_code,
    normalize_entity_code,
)
from ..logging_config import get_logger
from ..models import Entity

logger = get_logger(__name__)

# Tax ID regex: 18-digit Unified Social Credit Code or 15-20 digit tax code
TAX_ID_RE = re.compile(r"([0-9A-HJ-NP-RT-UW-Y]{18}|\d{15,20})", re.IGNORECASE)

# Company name regex
COMPANY_NAME_RE = re.compile(
    r"([\u4e00-\u9fa5（）()]{4,35}(?:有限公司|股份有限公司|有限责任公司|集团有限公司|工程有限公司|商贸有限公司|贸易有限公司|劳务有限公司|租赁有限公司|分公司))"
)


def _deduce_role_from_name(name: str) -> str:
    """Deduce business role A/B/C/D from company name keywords."""
    if any(k in name for k in ("劳务", "劳动力", "施工队")):
        return "C"
    if any(k in name for k in ("租赁", "机械", "设备", "农机")):
        return "D"
    if any(k in name for k in ("商贸", "贸易", "物资", "广告", "建材", "采云")):
        return "B"
    return "A"  # Default to A (Construction / Engineering)


def _find_available_canonical_code(db: Session, role: str) -> str | None:
    """Find the next available canonical entity code for the given role."""
    prefix_ranges = {
        "A": [f"A{i:02d}" for i in range(1, 12)],
        "B": [f"B{i:02d}" for i in range(1, 11)],
        "C": [f"C{i:02d}" for i in range(1, 3)],
        "D": [f"D{i:02d}" for i in range(1, 4)],
    }
    candidates = prefix_ranges.get(role, prefix_ranges["A"])
    existing_codes = set(
        db.scalars(select(Entity.entity_code).where(Entity.entity_code.is_not(None))).all()
    )
    for code in candidates:
        if code not in existing_codes:
            return code
    return None


def parse_entities_from_excel(file_bytes: bytes) -> list[dict[str, Any]]:
    """Parse entities from Excel (.xlsx) file bytes."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    results = []

    for sheet in wb.worksheets:
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            continue

        # Locate header row
        header_idx = -1
        col_map = {}
        for r_idx, row in enumerate(rows[:10]):
            row_str = [str(c or "").strip() for c in row]
            for c_idx, cell in enumerate(row_str):
                if any(k in cell for k in ("代码", "主体代码", "单位代码", "编号")):
                    col_map["code"] = c_idx
                elif any(k in cell for k in ("全称", "单位名称", "企业名称", "公司名称", "名称")):
                    col_map["name"] = c_idx
                elif any(k in cell for k in ("简称", "企业简称", "主体简称")):
                    col_map["short_name"] = c_idx
                elif any(k in cell for k in ("税号", "统一社会信用代码", "纳税人识别号", "信用代码")):
                    col_map["tax_id"] = c_idx
                elif any(k in cell for k in ("角色", "业务角色", "业务类型", "类别", "分类")):
                    col_map["role"] = c_idx
                elif any(k in cell for k in ("法人", "法定代表人", "负责人")):
                    col_map["legal_rep"] = c_idx
                elif any(k in cell for k in ("注册资本", "资本", "注册资金")):
                    col_map["capital"] = c_idx
                elif any(k in cell for k in ("成立日期", "成立时间", "注册日期")):
                    col_map["date"] = c_idx
                elif any(k in cell for k in ("经营范围", "业务范围", "范围")):
                    col_map["scope"] = c_idx
                elif any(k in cell for k in ("备注", "说明")):
                    col_map["note"] = c_idx

            if "name" in col_map or "tax_id" in col_map:
                header_idx = r_idx
                break

        start_row = header_idx + 1 if header_idx >= 0 else 0
        for row in rows[start_row:]:
            if not row or all(c is None for c in row):
                continue
            
            def get_val(key):
                if key in col_map and col_map[key] < len(row):
                    v = row[col_map[key]]
                    return str(v).strip() if v is not None else ""
                return ""

            name = get_val("name")
            tax_id = get_val("tax_id")
            code = get_val("code")

            # Fallback scan if columns weren't explicitly mapped
            if not name or not tax_id:
                for cell in row:
                    c_str = str(cell or "").strip()
                    if not tax_id and TAX_ID_RE.fullmatch(c_str):
                        tax_id = c_str
                    elif not name and COMPANY_NAME_RE.search(c_str):
                        name = c_str
                    elif not code and is_canonical_entity_code(c_str.upper()):
                        code = c_str.upper()

            if name or tax_id:
                results.append({
                    "entity_code": code,
                    "name": name or code or tax_id,
                    "short_name": get_val("short_name") or name,
                    "tax_id": tax_id,
                    "business_role": get_val("role"),
                    "legal_representative": get_val("legal_rep"),
                    "registered_capital": get_val("capital"),
                    "establishment_date": get_val("date"),
                    "business_scope": get_val("scope"),
                    "note": get_val("note"),
                })

    return results


def parse_entities_from_docx(file_bytes: bytes) -> list[dict[str, Any]]:
    """Parse entities from Word (.docx) file bytes."""
    import docx

    doc = docx.Document(io.BytesIO(file_bytes))
    results = []

    # 1. Parse from Word tables
    for table in doc.tables:
        if not table.rows:
            continue
        headers = [c.text.strip() for c in table.rows[0].cells]
        col_map = {}
        for c_idx, cell in enumerate(headers):
            if any(k in cell for k in ("代码", "编号")):
                col_map["code"] = c_idx
            elif any(k in cell for k in ("全称", "单位名称", "企业名称", "公司名称", "名称")):
                col_map["name"] = c_idx
            elif any(k in cell for k in ("简称", "企业简称")):
                col_map["short_name"] = c_idx
            elif any(k in cell for k in ("税号", "统一社会信用代码", "纳税人识别号")):
                col_map["tax_id"] = c_idx
            elif any(k in cell for k in ("角色", "业务类型", "类别")):
                col_map["role"] = c_idx
            elif any(k in cell for k in ("法人", "法定代表人")):
                col_map["legal_rep"] = c_idx
            elif any(k in cell for k in ("注册资本", "资本")):
                col_map["capital"] = c_idx

        for row in table.rows[1:]:
            cells = [c.text.strip() for c in row.cells]
            name = cells[col_map["name"]] if "name" in col_map and col_map["name"] < len(cells) else ""
            tax_id = cells[col_map["tax_id"]] if "tax_id" in col_map and col_map["tax_id"] < len(cells) else ""
            code = cells[col_map["code"]] if "code" in col_map and col_map["code"] < len(cells) else ""

            if not name or not tax_id:
                for c in cells:
                    if not tax_id and TAX_ID_RE.fullmatch(c):
                        tax_id = c
                    elif not name and COMPANY_NAME_RE.search(c):
                        name = c

            if name or tax_id:
                results.append({
                    "entity_code": code,
                    "name": name,
                    "short_name": cells[col_map["short_name"]] if "short_name" in col_map and col_map["short_name"] < len(cells) else name,
                    "tax_id": tax_id,
                    "business_role": cells[col_map["role"]] if "role" in col_map and col_map["role"] < len(cells) else "",
                    "legal_representative": cells[col_map["legal_rep"]] if "legal_rep" in col_map and col_map["legal_rep"] < len(cells) else "",
                    "registered_capital": cells[col_map["capital"]] if "capital" in col_map and col_map["capital"] < len(cells) else "",
                })

    # 2. Parse unstructured text paragraphs if no tables found
    if not results:
        full_text = "\n".join(p.text for p in doc.paragraphs)
        extracted = _extract_entities_from_text(full_text)
        results.extend(extracted)

    return results


def parse_entities_from_pdf(file_bytes: bytes) -> list[dict[str, Any]]:
    """Parse entities from PDF file bytes."""
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    full_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return _extract_entities_from_text(full_text)


def _extract_entities_from_text(text: str) -> list[dict[str, Any]]:
    """Regex-based heuristic entity extractor from free text / OCR output."""
    results = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    current_item: dict[str, Any] = {}
    for line in lines:
        # Match Tax ID
        tax_m = TAX_ID_RE.search(line)
        if tax_m and ("统一社会信用代码" in line or "税号" in line or len(tax_m.group(1)) == 18):
            current_item["tax_id"] = tax_m.group(1).upper()

        # Match Company Name
        name_m = COMPANY_NAME_RE.search(line)
        if name_m and ("名称" in line or "公司" in line or "单位" in line):
            current_item["name"] = name_m.group(1)

        # Match Legal Rep
        rep_m = re.search(r"(?:法定代表人|负责人|法人代表)[:：\s]*([\u4e00-\u9fa5]{2,6})", line)
        if rep_m:
            current_item["legal_representative"] = rep_m.group(1)

        # Match Capital
        cap_m = re.search(r"(?:注册资本|注册资金)[:：\s]*([0-9\.\,]+(?:万|亿)?(?:元|人民币|美元)?)", line)
        if cap_m:
            current_item["registered_capital"] = cap_m.group(1)

        # Match Date
        date_m = re.search(r"(?:成立日期|成立时间)[:：\s]*(\d{4}[年\-\/.]\d{1,2}[月\-\/.]\d{1,2}日?)", line)
        if date_m:
            current_item["establishment_date"] = date_m.group(1)

        # Match Code
        code_m = re.search(r"(?:代码|编号)[:：\s]*([A-D](?:0[1-9]|1[0-2]))", line, re.IGNORECASE)
        if code_m:
            current_item["entity_code"] = code_m.group(1).upper()

        # When we have both name and tax_id, save and reset for next
        if current_item.get("name") and current_item.get("tax_id"):
            results.append(dict(current_item))
            current_item = {}

    if current_item.get("name") or current_item.get("tax_id"):
        results.append(current_item)

    return results


def import_entities_from_file_bytes(
    db: Session,
    file_bytes: bytes,
    filename: str,
) -> dict[str, Any]:
    """Parse file bytes (Excel, Word, PDF) and upsert entities into Entity table."""
    suffix = Path(filename).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        raw_items = parse_entities_from_excel(file_bytes)
    elif suffix in (".docx", ".doc"):
        raw_items = parse_entities_from_docx(file_bytes)
    elif suffix == ".pdf":
        raw_items = parse_entities_from_pdf(file_bytes)
    else:
        raise ValueError(f"不支持的文件格式: {suffix}。支持 .xlsx, .docx, .pdf")

    if not raw_items:
        raise ValueError(f"未能从文件 '{filename}' 中识别出有效的单位名称或税号数据")

    imported_entities = []
    created_count = 0
    updated_count = 0

    for item in raw_items:
        name = (item.get("name") or "").strip()
        tax_id = (item.get("tax_id") or "").strip().upper() or None
        code = normalize_entity_code(item.get("entity_code"))

        if not name and not tax_id:
            continue

        # Look up existing entity by code, tax_id, or name
        existing = None
        if code and is_canonical_entity_code(code):
            existing = db.scalar(select(Entity).where(Entity.entity_code == code))
        if not existing and tax_id:
            existing = db.scalar(select(Entity).where(Entity.tax_id == tax_id))
        if not existing and name:
            existing = db.scalar(select(Entity).where(Entity.name == name))

        role = item.get("business_role") or (code[0] if code else _deduce_role_from_name(name))
        role = role.upper() if role else "A"
        if role not in ("A", "B", "C", "D"):
            role = "A"

        if existing:
            # Update fields
            if name:
                existing.name = name
                existing.short_name = item.get("short_name") or existing.short_name or name
            if tax_id:
                existing.tax_id = tax_id
                existing.unified_social_credit_code = tax_id
            if item.get("legal_representative"):
                existing.legal_representative = item["legal_representative"]
            if item.get("registered_capital"):
                existing.registered_capital = item["registered_capital"]
            if item.get("establishment_date"):
                existing.establishment_date = item["establishment_date"]
            if item.get("business_scope"):
                existing.business_scope = item["business_scope"]
            if item.get("note"):
                existing.note = item["note"]
            existing.business_role = role
            existing.status = "active"
            existing.active = True
            existing.internal = True
            updated_count += 1
            imported_entities.append(existing)
        else:
            # Allocate canonical code if not present
            if not code or not is_canonical_entity_code(code):
                allocated_code = _find_available_canonical_code(db, role)
                if not allocated_code:
                    logger.warning(f"No available canonical code for role {role}; skipping {name}")
                    continue
                code = allocated_code

            new_ent = Entity(
                code=code,
                entity_code=code,
                name=name or code,
                short_name=item.get("short_name") or name or code,
                business_role=role,
                kind=role,
                entity_kind="internal_company",
                tax_id=tax_id,
                unified_social_credit_code=tax_id or "",
                legal_representative=item.get("legal_representative") or "",
                registered_capital=item.get("registered_capital") or "",
                establishment_date=item.get("establishment_date") or "",
                business_scope=item.get("business_scope") or "",
                note=item.get("note") or "",
                legal_entity=True,
                status="active",
                active=True,
                internal=True,
            )
            db.add(new_ent)
            db.flush()
            created_count += 1
            imported_entities.append(new_ent)

    db.commit()

    return {
        "success": True,
        "total_parsed": len(raw_items),
        "created_count": created_count,
        "updated_count": updated_count,
        "items": [
            {
                "entity_code": e.entity_code,
                "name": e.name,
                "tax_id": e.tax_id,
                "business_role": e.business_role,
            }
            for e in imported_entities
        ],
    }
