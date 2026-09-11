"""
ledger_importer.py — 台账（Ledger）批量导入服务
===============================================
支持从 Excel (.xlsx/.xls)、Word (.docx)、PDF 表格中解析结构化数据，
按数据库已有表单格式批量写入 RAG 系统的以下表：

    projects        项目台账
    documents       发票/合同台账（带税务字段）
    external_parties 供应商/往来单位台账
    entities        内部单位台账（复用 entity_importer.py）
    entities_or_external 自动路由：系统内 → entities，系统外 → external_parties

每个表的列映射通过 HEADER_ALIASES 定义，支持中文表头模糊匹配，
允许用户在 Excel 中使用自己的列名（如"项目代码"/"项目编号"/"编号"都可映射到 project_code）。

台账单位自动路由规则（entities_or_external）：
  1. entity_code 匹配 A01-A11 / B01-B10 / C01-C02 / D01-D03  → entities（内部单位）
  2. entity_code 匹配 E01-E03 / EA01-EA02 / EB01-EB03 / EC01 / ED01 → external_parties（已知外部单位）
  3. 名称/简称匹配已知外部单位预设别名                         → external_parties
  4. 新外部单位（无代码、无法定代表为个人）                    → external_parties
  5. 新单位（有名称、税号，无法官代码）                       → entities + 自动分配下一可用代码
     新外部单位（有名称）                                      → external_parties + 自动分配 E 系列代码
     外部代码格式：E1–E999999（无零填充，每系列 999,999 家，8 系列合计近 800 万家）

使用方式（CLI）：
    python -m scripts.ledger_import --table projects --path ./data/项目台账.xlsx
    python -m scripts.ledger_import --table documents --path ./data/发票台账.xlsx --project-id 3
    python -m scripts.ledger_import --path ./data/单位台账.xlsx --auto-route   # 自动路由

使用方式（API / 内部调用）：
    from app.services.ledger_importer import import_ledger
    result = import_ledger("entities_or_external", "/path/to/file.xlsx", db=session)
"""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import openpyxl

logger = logging.getLogger("ledger_importer")

# ---------------------------------------------------------------------------
# 表 → 列映射别名（中文/英文 → 数据库字段）
# ---------------------------------------------------------------------------

# projects 表映射
PROJECTS_ALIASES: dict[str, list[str]] = {
    "project_code": [
        "项目代码", "项目编号", "项目ID", "编号", "code", "project_code",
    ],
    "name": ["项目名称", "名称", "name", "project_name"],
    "location": ["项目地址", "地址", "地点", "location", "项目地点"],
    "contract_amount": [
        "合同金额", "签约金额", "金额", "contract_amount", "项目金额",
    ],
    "start_date": ["开工日期", "开始日期", "start_date", "开工时间"],
    "expected_end_date": ["竣工日期", "结束日期", "expected_end_date", "计划竣工", "竣工时间"],
    "status": ["项目状态", "status", "状态"],
    "project_type": ["项目类型", "类型", "project_type", "工程类型"],
    "entity_code": ["主管单位", "entity_code", "单位代码", "主体代码"],
    "note": ["备注", "说明", "note"],
}

# documents 表映射（发票/合同台账）
DOCUMENTS_ALIASES: dict[str, list[str]] = {
    "document_code": [
        "单据编号", "单据号", "编号", "document_code", "发票号码", "invoice_no",
        "合同编号", "contract_no",
    ],
    "document_type": [
        "单据类型", "类型", "单据类型", "document_type", "发票类型",
        "业务类型", "type",
    ],
    "filename": ["文件名", "文件名称", "filename", "文件名（含后缀）"],
    "file_type": ["文件格式", "file_type", "文件类型"],
    "file_hash": ["文件哈希", "hash", "file_hash"],
    "size_bytes": ["文件大小", "size_bytes"],
    "original_path": ["原始路径", "original_path"],
    "parse_status": ["解析状态", "parse_status", "状态"],
    # 税务字段
    "invoice_no": ["发票号码", "invoice_no"],
    "invoice_code": ["发票代码", "invoice_code"],
    "invoice_type": ["发票类型", "invoice_type"],
    "invoice_date": ["开票日期", "发票日期", "invoice_date"],
    "contract_no": ["合同编号", "contract_no"],
    "period": ["期间", "period", "所属期"],
    "document_date": ["单据日期", "document_date", "日期"],
    "tax_vat_rate": ["增值税率", "税率", "tax_vat_rate", "vat_rate", "增值税率(%)"],
    "tax_vat_input": ["进项税额", "tax_vat_input", "可抵扣税额"],
    "tax_vat_output": ["销项税额", "tax_vat_output"],
    "tax_vat_paid": ["已交增值税", "tax_vat_paid"],
    "tax_income_rate": ["所得税率", "tax_income_rate", "所得税税率(%)"],
    "tax_income_amount": ["应纳所得税", "tax_income_amount"],
    "tax_income_paid": ["已预缴所得税", "tax_income_paid"],
    "tax_individual_rate": ["个税税率", "tax_individual_rate"],
    "tax_individual_amount": ["代扣代缴个税", "tax_individual_amount"],
    "tax_surtax_urban": ["城建税", "tax_surtax_urban"],
    "tax_surtax_edu": ["教育费附加", "tax_surtax_edu"],
    "tax_surtax_local_edu": ["地方教育附加", "tax_surtax_local_edu"],
    "tax_stamp_duty": ["印花税", "tax_stamp_duty"],
    "tax_total": ["税金合计", "tax_total", "总税额"],
    "confidentiality": ["密级", "confidentiality", "保密级别"],
    "version_label": ["版本", "version_label"],
    "metadata_source": ["元数据来源", "metadata_source"],
    "entity_code": ["单位代码", "entity_code", "主体代码", "供应方代码"],
    "counterparty_code": ["对方单位代码", "counterparty_code", "客户代码"],
    "business_category": ["业务类别", "business_category"],
    "tax_category": ["税务分类", "tax_category"],
}

# external_parties 表映射
EXTERNAL_PARTIES_ALIASES: dict[str, list[str]] = {
    "code": ["供应商代码", "code", "单位代码", "编号"],
    "name": ["供应商名称", "单位名称", "名称", "name", "企业名称", "公司名称"],
    "short_name": ["简称", "short_name", "企业简称"],
    "kind": ["类型", "kind", "企业类型", "单位类型"],
    "tax_id": [
        "税号", "纳税人识别号", "统一社会信用代码", "信用代码",
        "tax_id", "unified_social_credit_code",
    ],
    "active": ["是否有效", "active", "状态"],
}

# entities 表映射（与 entity_importer.py 保持一致）
ENTITIES_ALIASES: dict[str, list[str]] = {
    "entity_code": ["主体代码", "entity_code", "单位代码", "代码", "编号"],
    "name": ["单位全称", "企业全称", "公司全称", "名称", "name"],
    "short_name": ["简称", "short_name", "企业简称"],
    "entity_type": ["单位类型", "entity_type", "企业类型", "类型"],
    "industry": ["行业", "industry", "业务类型"],
    "business_role": ["业务角色", "business_role", "角色", "A/B/C/D"],
    "legal_representative": ["法定代表人", "legal_representative", "法人"],
    "registered_capital": ["注册资本", "registered_capital"],
    "establishment_date": ["成立日期", "establishment_date", "注册日期"],
    "tax_id": ["税号", "纳税人识别号", "统一社会信用代码", "tax_id"],
    "business_scope": ["经营范围", "business_scope"],
    "note": ["备注", "note"],
    "status": ["状态", "status"],
}

# 允许的表名（含虚拟路由表）
VALID_TABLES = frozenset({"projects", "documents", "external_parties", "entities", "entities_or_external"})

# ---------------------------------------------------------------------------
# 实体自动路由（entities_or_external）
# ---------------------------------------------------------------------------

# 内部单位代码前缀
_INTERNAL_CODE_PREFIXES = ("A", "B", "C", "D")

# 已知外部单位预设代码（来自 domain/entities.py EXTERNAL_ENTITY_PRESETS，预设格式为 EA01 等，历史数据兼容）
# 自动分配格式：E1 / E100 / E12222（无零填充）
_KNOWN_EXTERNAL_CODES = frozenset((
    "E01", "E02", "E03",
    "EA01", "EA02",
    "EB01", "EB02", "EB03",
    "EC01",
    "ED01",
))

# 外部单位代码前缀层级（用于自动分配，可扩展至 EA/EB/EC/ED/F...）
_EXTERNAL_CODE_SERIES = ["E", "EA", "EB", "EC", "ED", "F", "FA", "FB"]
_EXTERNAL_CODE_POOL_SIZE = 999_999  # 每系列最多 999,999 家


def _is_canonical_internal_code(code: str | None) -> bool:
    """判断是否为系统内部单位代码 A01-D03。"""
    if not code:
        return False
    c = code.strip().upper()
    if len(c) != 3:
        return False
    prefix, num = c[0], c[1:]
    if prefix not in _INTERNAL_CODE_PREFIXES:
        return False
    try:
        n = int(num)
    except ValueError:
        return False
    ranges = {"A": (1, 11), "B": (1, 10), "C": (1, 2), "D": (1, 3)}
    lo, hi = ranges.get(prefix, (0, 0))
    return lo <= n <= hi


def _is_known_external_code(code: str | None) -> bool:
    """判断是否为已知外部单位代码 E01-E03 / EA01-EA02 / EB01-EB03 / EC01 / ED01。"""
    if not code:
        return False
    return code.strip().upper() in _KNOWN_EXTERNAL_CODES


def _match_external_preset(name: str | None, short_name: str | None = None) -> bool:
    """判断名称/简称是否匹配已知外部单位预设别名。"""
    if not name:
        return False
    # 懒加载避免循环导入
    from ..domain.entities import EXTERNAL_ALIAS_TO_CODE
    candidates = [name.strip().upper()]
    if short_name:
        candidates.append(short_name.strip().upper())
    for c in candidates:
        if c in EXTERNAL_ALIAS_TO_CODE:
            return True
    return False


def _route_entity_record(record: dict[str, Any]) -> str:
    """根据记录内容自动路由到 entities 或 external_parties 表。

    路由优先级：
      1. 有 canonical 内部代码  → entities
      2. 有已知外部单位代码      → external_parties
      3. 名称匹配已知外部单位别名 → external_parties
      4. 无代码、有法人代表且为个人姓名 → external_parties（推断为自然人挂靠）
      5. 其他所有情况            → entities（待人工补编代码）
    """
    code = record.get("entity_code") or record.get("code") or ""
    code = str(code).strip().upper()
    name = record.get("name", "")
    short_name = record.get("short_name", "")

    if _is_canonical_internal_code(code):
        return "entities"
    if _is_known_external_code(code):
        return "external_parties"
    if _match_external_preset(name, short_name):
        return "external_parties"
    # 无代码：根据法人代表判断（自然人挂靠 → 外部）
    legal_rep = record.get("legal_representative", "") or ""
    if legal_rep and len(legal_rep) <= 4 and not any(k in legal_rep for k in ("公司", "集团", "有限", "企业", "事业单位")):
        return "external_parties"
    return "entities"


def _auto_assign_entity_code(db, role: str = "A") -> str | None:
    """为新内部单位自动分配下一个可用 A/B/C/D canonical 代码。"""
    from sqlalchemy import select
    from ..models import Entity
    prefix_ranges = {
        "A": [f"A{i:02d}" for i in range(1, 12)],
        "B": [f"B{i:02d}" for i in range(1, 11)],
        "C": [f"C{i:02d}" for i in range(1, 3)],
        "D": [f"D{i:02d}" for i in range(1, 4)],
    }
    candidates = prefix_ranges.get(role, prefix_ranges["A"])
    existing = set(
        db.scalars(select(Entity.entity_code).where(Entity.entity_code.is_not(None))).all()
    )
    for code in candidates:
        if code not in existing:
            return code
    return None


def _auto_assign_external_code(db) -> str | None:
    """为新外部单位自动分配下一个可用 E 系列代码（支持近 800 万家）。

    分配顺序：E1 → E999999 → EA1 → EA999999 → EB1 → ...
    每系列 999,999 个，全部用完后自动切换到下一个字母前缀。
    格式：E1 / E100 / E12222（无零填充，数字直接拼接前缀）。
    """
    from sqlalchemy import select
    from ..models import ExternalParty

    existing_codes: set[str] = set(
        db.scalars(select(ExternalParty.code).where(ExternalParty.code.is_not(None))).all()
    )

    for series_prefix in _EXTERNAL_CODE_SERIES:
        # 收集该系列已有的编号
        used_nums: set[int] = set()
        for code in existing_codes:
            if code and code.upper().startswith(series_prefix):
                try:
                    num_str = code[len(series_prefix):]
                    n = int(num_str)
                    used_nums.add(n)
                except ValueError:
                    pass

        # 分配下一个可用编号
        for n in range(1, _EXTERNAL_CODE_POOL_SIZE + 1):
            if n not in used_nums:
                return f"{series_prefix}{n}"  # E1, EA1, EB1, ...

    # 全系列用尽，抛出异常
    raise RuntimeError(
        f"外部单位代码库已满（{len(_EXTERNAL_CODE_SERIES)} 系列 × {_EXTERNAL_CODE_POOL_SIZE:,} 家上限 = "
        f"{len(_EXTERNAL_CODE_SERIES) * _EXTERNAL_CODE_POOL_SIZE:,} 家），请联系管理员扩展前缀。"
    )


def _find_existing_record(db, record: dict[str, Any], table: str) -> int | None:
    """按 tax_id 或 name 查找数据库中是否已有记录，返回其 ID。"""
    from sqlalchemy import or_, select
    if table == "entities":
        from ..models import Entity
        tax_id = record.get("tax_id")
        name = record.get("name", "").strip()
        q = select(Entity.id)
        conditions = []
        if tax_id:
            conditions.append(Entity.tax_id == tax_id)
        if name:
            conditions.append(Entity.name == name)
        if not conditions:
            return None
        row = db.scalars(q.where(or_(*conditions)).limit(1)).first()
        return int(row) if row else None
    elif table == "external_parties":
        from ..models import ExternalParty
        tax_id = record.get("tax_id")
        name = record.get("name", "").strip()
        q = select(ExternalParty.id)
        conditions = []
        if tax_id:
            conditions.append(ExternalParty.tax_id == tax_id)
        if name:
            conditions.append(ExternalParty.name == name)
        if not conditions:
            return None
        row = db.scalars(q.where(or_(*conditions)).limit(1)).first()
        return int(row) if row else None
    return None


# ---------------------------------------------------------------------------
# 通用列名标准化
# ---------------------------------------------------------------------------

def _normalize_header(header: str) -> str:
    """将表头文本标准化为小写英文，移除括号和空格。"""
    return re.sub(r"[\s（）()（）]", "", header.lower().strip())


# ---------------------------------------------------------------------------
# 列名 → 字段别名 → 目标字段的反向索引
# ---------------------------------------------------------------------------

def _build_alias_map(aliases: dict[str, list[str]]) -> dict[str, str]:
    """从 {field: [aliases]} 构建 {alias_normalized: field} 索引。"""
    index: dict[str, str] = {}
    for field, alias_list in aliases.items():
        for alias in alias_list:
            index[_normalize_header(alias)] = field
    return index


_ALIAS_MAPS = {
    "projects": _build_alias_map(PROJECTS_ALIASES),
    "documents": _build_alias_map(DOCUMENTS_ALIASES),
    "external_parties": _build_alias_map(EXTERNAL_PARTIES_ALIASES),
    "entities": _build_alias_map(ENTITIES_ALIASES),
}


def _map_headers(headers: list[str], table: str) -> list[tuple[int, str] | None]:
    """将 Excel 表头列表映射到目标表字段，返回 [(col_idx, field_name) or None]。

    无法映射的列返回 None。
    """
    alias_map = _ALIAS_MAPS.get(table, {})
    result: list[tuple[int, str] | None] = []
    for h in headers:
        norm = _normalize_header(h)
        field = alias_map.get(norm)
        result.append((len(result), field) if field else None)
    return result


# ---------------------------------------------------------------------------
# 文件解析
# ---------------------------------------------------------------------------

def parse_excel(file_bytes: bytes) -> list[dict[str, Any]]:
    """解析 Excel 文件，返回每行对应字段名→值的字典列表。"""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    # 取第一行作为表头
    headers = [str(c or "").strip() for c in rows[0]]
    return rows[1:]


def parse_excel_sheet(
    file_bytes: bytes,
    sheet_index: int = 0,
) -> list[list[Any]]:
    """返回指定 sheet 的所有行（不含表头），用于直接处理。"""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheets = wb.sheetnames
    if sheet_index >= len(sheets):
        raise ValueError(f"Sheet index {sheet_index} out of range, available: {sheets}")
    ws = wb[sheets[sheet_index]]
    rows = list(ws.iter_rows(values_only=True))
    return rows[1:] if rows else []  # skip header


def _detect_table_from_headers(headers: list[str]) -> str | None:
    """根据表头内容猜测台账类型。"""
    h_str = " ".join(_normalize_header(h) for h in headers)

    if any(k in h_str for k in ["项目代码", "项目名称", "项目编号", "开工日期", "竣工日期", "项目地址"]):
        return "projects"
    if any(k in h_str for k in ["发票号码", "发票代码", "增值税率", "进项税额", "销项税额", "税金合计", "开票日期"]):
        return "documents"
    if any(k in h_str for k in ["合同编号", "合同金额", "签约金额"]):
        return "documents"
    if any(k in h_str for k in ["供应商名称", "供应商代码", "统一社会信用代码", "税号"]):
        return "external_parties"
    if any(k in h_str for k in ["单位代码", "单位全称", "主体代码", "统一社会信用代码"]):
        return "entities"
    return None


def _parse_value(raw: Any, field: str) -> Any:
    """将原始单元格值转换为目标字段的 Python 类型。"""
    if raw is None or raw == "":
        return None

    s = str(raw).strip()

    if field in {"contract_amount", "tax_vat_input", "tax_vat_output",
                  "tax_vat_paid", "tax_income_amount", "tax_income_paid",
                  "tax_individual_amount", "tax_surtax_urban", "tax_surtax_edu",
                  "tax_surtax_local_edu", "tax_stamp_duty", "tax_total",
                  "size_bytes"}:
        # 去除人民币符号和逗号
        cleaned = re.sub(r"[¥$,，\s]", "", s)
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    if field in {"tax_vat_rate", "tax_income_rate", "tax_individual_rate",
                  "metadata_confidence"}:
        # 税率可能是 "13%" 或 "0.13" 或 "13"
        cleaned = re.sub(r"[%\s]", "", s)
        try:
            v = float(cleaned)
            return v / 100 if v > 1 else v
        except (ValueError, TypeError):
            return None

    if field in {"active"}:
        return s.lower() in {"是", "有效", "yes", "y", "true", "1", "active"}

    if field in {"invoice_deductible"}:
        return s.lower() in {"是", "已勾选", "yes", "y", "true", "1"}

    if field in {"legal_entity", "is_encrypted"}:
        return s.lower() in {"是", "yes", "true", "1"}

    if field in {"invoice_date", "document_date", "start_date",
                  "expected_end_date", "establishment_date", "date",
                  "acquisition_date", "data_as_of"}:
        # 尝试解析常见日期格式
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        return s  # 返回原字符串

    if field in {"size_bytes"}:
        try:
            return int(s)
        except (ValueError, TypeError):
            return None

    # 默认返回字符串
    return s


# ---------------------------------------------------------------------------
# 核心导入逻辑
# ---------------------------------------------------------------------------

def import_excel_ledger(
    file_path: str,
    table: str,
    project_id: int | None = None,
    header_row: int = 0,
    sheet_index: int = 0,
    skip_validation: bool = False,
) -> dict[str, Any]:
    """解析 Excel 台账文件并返回预览/验证结果（不写入数据库）。

    用于 UI 预览：传入 --dry-run 或在写入前先调用此函数。
    返回 {"rows": [...], "errors": [...], "total": N}
    """
    if table not in VALID_TABLES:
        raise ValueError(f"无效表名: {table}，允许: {VALID_TABLES}")

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheets = wb.sheetnames
    ws = wb[sheets[sheet_index]]
    rows = list(ws.iter_rows(values_only=True))

    if header_row >= len(rows):
        raise ValueError(f"表头行 {header_row} 超出文件行数 {len(rows)}")

    raw_headers = [str(c or "").strip() for c in rows[header_row]]
    col_mapping = _map_headers(raw_headers, table)

    # 记录哪些字段被映射了
    mapped_fields = {field for _, field in col_mapping if field is not None}

    data_rows = []
    errors = []
    for row_idx, row in enumerate(rows[header_row + 1:], start=header_row + 2):
        if not row or all(c is None or str(c).strip() == "" for c in row):
            continue

        record: dict[str, Any] = {"_row": row_idx}
        if project_id is not None:
            record["project_id"] = project_id

        row_errors: list[str] = []
        for col_idx, field in col_mapping:
            if field is None:
                continue
            if col_idx >= len(row):
                continue
            raw = row[col_idx]
            parsed = _parse_value(raw, field)
            if parsed is not None:
                record[field] = parsed
            elif raw is not None and str(raw).strip() != "":
                # 有原始值但解析失败
                row_errors.append(f"列「{raw_headers[col_idx]}」值「{raw}」无法转换为 {field} 类型")

        data_rows.append(record)
        if row_errors:
            errors.append({"row": row_idx, "errors": row_errors})

    return {
        "table": table,
        "file": file_path,
        "sheet": sheets[sheet_index] if sheet_index < len(sheets) else None,
        "total": len(data_rows),
        "mapped_fields": sorted(mapped_fields),
        "rows": data_rows,
        "errors": errors,
        "raw_headers": raw_headers,
    }


def _write_ledger_to_db(
    rows: list[dict[str, Any]],
    table: str,
    db,
    dry_run: bool = False,
) -> dict[str, Any]:
    """将已解析的行写入数据库（内部使用，由 scripts/ledger_import.py 调用）。

    当 table == "entities_or_external" 时，对每行自动路由到 entities 或 external_parties，
    并对去重后确认是新增的记录写入数据库。
    """
    from sqlalchemy import select
    from ..models import Document, Entity, ExternalParty, Project

    written = 0
    skipped = 0
    upserted = 0
    errors: list[dict[str, Any]] = []
    ids: list[int] = []
    routing_summary: dict[str, int] = {}

    # entities_or_external 时，先把每行路由目标记录下来
    if table == "entities_or_external":
        for record in rows:
            target = _route_entity_record(record)
            record["_target_table"] = target
            routing_summary[target] = routing_summary.get(target, 0) + 1

    for record in rows:
        row_num = record.pop("_row", "?")
        target_table = record.pop("_target_table", table)

        try:
            if dry_run:
                skipped += 1
                continue

            model_map = {
                "projects": Project,
                "documents": Document,
                "external_parties": ExternalParty,
                "entities": Entity,
            }
            Model = model_map.get(target_table)
            if Model is None:
                errors.append({"row": row_num, "error": f"未知目标表: {target_table}"})
                skipped += 1
                continue

            # 去重检查
            existing_id = _find_existing_record(db, record, target_table)
            if existing_id is not None:
                skipped += 1
                ids.append(existing_id)
                continue

            # entities 表：自动分配 canonical 代码（如果缺失）
            if target_table == "entities" and not record.get("entity_code"):
                # 根据名称推断角色
                role = "A"
                name = record.get("name", "")
                if any(k in name for k in ("劳务", "劳动力", "施工队")):
                    role = "C"
                elif any(k in name for k in ("租赁", "机械", "设备")):
                    role = "D"
                elif any(k in name for k in ("商贸", "贸易", "物资", "建材")):
                    role = "B"
                assigned = _auto_assign_entity_code(db, role)
                if assigned:
                    record["entity_code"] = assigned
                    record["note"] = (record.get("note") or "") + f" [自动分配代码:{assigned}]"
                else:
                    errors.append({
                        "row": row_num,
                        "error": f"无法为{name}自动分配代码，所有代码已用尽",
                        "skipped": True,
                    })
                    skipped += 1
                    continue

            # external_parties 表：自动分配 E 系列代码（支持 1000+ 家）
            if target_table == "external_parties" and not record.get("code"):
                short = record.get("short_name") or record.get("name", "")[:4]
                record["short_name"] = short
                record["code"] = _auto_assign_external_code(db)

            # 过滤掉非模型字段
            allowed = {k: v for k, v in record.items()
                       if k in Model.__table__.columns}

            obj = Model(**allowed)
            db.add(obj)
            db.flush()  # 获取主键
            ids.append(obj.id)
            written += 1
            upserted += 1
        except Exception as exc:
            errors.append({"row": row_num, "error": str(exc)})
            skipped += 1

    if not dry_run:
        db.commit()

    return {
        "written": written,
        "upserted": upserted,
        "skipped": skipped,
        "errors": errors,
        "ids": ids,
        "routing_summary": routing_summary if table == "entities_or_external" else None,
    }

    for record in rows:
        row_num = record.pop("_row", "?")
        try:
            if dry_run:
                skipped += 1
                continue

            # 过滤掉非模型字段
            allowed = {k: v for k, v in record.items()
                       if k in Model.__table__.columns}

            obj = Model(**allowed)
            db.add(obj)
            db.flush()  # 获取主键
            ids.append(obj.id)
            written += 1
        except Exception as exc:
            errors.append({"row": row_num, "error": str(exc)})
            skipped += 1

    if not dry_run:
        db.commit()

    return {
        "written": written,
        "skipped": skipped,
        "errors": errors,
        "ids": ids,
    }


# ---------------------------------------------------------------------------
# 批量导入（CLI 包装）
# ---------------------------------------------------------------------------

def import_ledger(
    table: str,
    path: str,
    project_id: int | None = None,
    dry_run: bool = True,
    db=None,
) -> dict[str, Any]:
    """主入口：解析并（可选）写入台账数据。

    Args:
        table:      目标表名（projects / documents / external_parties / entities）
        path:       Excel/Word/PDF 文件路径
        project_id: 对于 documents 表，指定所属项目 ID
        dry_run:    True=仅预览不写入（默认），False=实际写入
        db:         SQLAlchemy Session（dry_run=False 时必须提供）
    """
    if not dry_run and db is None:
        raise ValueError("写入数据库必须提供 db Session")

    result = import_excel_ledger(path, table, project_id=project_id)

    if not dry_run:
        db_result = _write_ledger_to_db(result["rows"], table, db, dry_run=False)
        result["db_write"] = db_result

    return result
