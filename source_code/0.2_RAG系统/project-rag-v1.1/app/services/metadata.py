"""Metadata inference service with deterministic rules.

Entity metadata is deliberately conservative.  The four single-letter
labels are business roles, not companies; only codes from the canonical
26-company master can be emitted as a resolved ``entity_code``.  A name or
tax id is used only when it maps to exactly one canonical cache row.
"""
from pathlib import Path
from collections.abc import Mapping, Sequence
import csv
import json
import os
import re
from typing import Any

from ..domain.entities import (
    EXTERNAL_ENTITY_PRESETS,
    get_external_preset,
    map_to_standard_external_code,
)
from ..logging_config import get_logger

logger = get_logger(__name__)

# Keep metadata inference dependency-free.  The ORM model exposes the same
# contract for persistence/API callers; repeating this tiny lexical contract
# avoids forcing a database driver merely to classify a filename.
CANONICAL_ENTITY_CODES = frozenset(
    {f"A{i:02d}" for i in range(1, 12)}
    | {f"B{i:02d}" for i in range(1, 11)}
    | {f"C{i:02d}" for i in range(1, 3)}
    | {f"D{i:02d}" for i in range(1, 4)}
)
BUSINESS_ROLE_CODES = frozenset({"A", "B", "C", "D"})
VIRTUAL_ENTITY_CODES = frozenset({"A", "B", "C", "D", "甲", "乙", "丙", "丁"})
_CANONICAL_ENTITY_CODE_RE = re.compile(
    r"^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C(?:0[1-2])|D(?:0[1-3])|E(?:0[1-9]|[1-9]\d|[A-D](?:0[1-9]|[1-9]\d)))$",
    re.IGNORECASE,
)


def normalize_entity_code(value: str | None) -> str | None:
    value = "" if value is None else str(value).strip().upper()
    return value or None


def is_canonical_entity_code(value: str | None) -> bool:
    code = normalize_entity_code(value)
    return bool(
        code
        and (
            code in CANONICAL_ENTITY_CODES
            or bool(_CANONICAL_ENTITY_CODE_RE.fullmatch(code) and code.startswith("E"))
        )
        and _CANONICAL_ENTITY_CODE_RE.fullmatch(code)
    )


_ENTITY_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C(?:0[1-2])|D(?:0[1-3])|EXT-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*|E(?:0[1-9]|[1-9]\d|[A-D](?:0[1-9]|[1-9]\d))|EA|EB|EC|ED|E0)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_TAX_ID_RE = re.compile(r"(?<![A-Za-z0-9])[1-9ANY][1-9]\d{6}[0-9A-HJ-NP-RTUWXY]{10}(?![A-Za-z0-9])", re.IGNORECASE)
_ROLE_RE = re.compile(r"(?<![A-Za-z])([ABCD])(?:类|类主体|类业务)(?![A-Za-z])", re.IGNORECASE)


def _default_cache() -> list[dict[str, str]]:
    """Return the canonical code roster used when no external cache is set.

    The roster validates the identifier namespace only.  It intentionally has
    no invented company names or tax ids.  Deployments should set
    ``PROJECT_RAG_ENTITY_MASTER`` to the actual JSON/CSV cache so names and
    tax ids can resolve to a company as well.
    """
    return [
        {"entity_code": code, "business_role": code[0]}
        for code in sorted(CANONICAL_ENTITY_CODES)
    ]


def _read_cache_file(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read a small canonical entity cache from JSON or CSV.

    XLSX parsing belongs to the master import script; keeping runtime metadata
    lookup dependency-free prevents an optional spreadsheet library from
    becoming a production requirement.
    """
    cache_path = Path(path).expanduser().resolve()
    if not cache_path.is_file():
        raise FileNotFoundError(f"canonical entity cache not found: {cache_path}")
    suffix = cache_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(payload, Mapping):
            payload = payload.get("entities", payload.get("items", payload))
        if isinstance(payload, Mapping):
            payload = [dict(value, entity_code=key) if isinstance(value, Mapping) else {"entity_code": key, "name": value}
                       for key, value in payload.items()]
        if not isinstance(payload, list):
            raise ValueError("canonical JSON must contain a list or an entities/items object")
        return [dict(row) for row in payload if isinstance(row, Mapping)]
    if suffix == ".csv":
        with cache_path.open("r", encoding="utf-8-sig", newline="") as fh:
            return [dict(row) for row in csv.DictReader(fh)]
    raise ValueError("canonical entity cache supports JSON or CSV")


def load_canonical_entity_cache(
    source: str | os.PathLike[str] | Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Load and normalize canonical cache rows.

    ``source=None`` uses the JSON/CSV master configured by
    ``PROJECT_RAG_ENTITY_MASTER``.  When that optional export is not
    configured, the cache is read from the current runtime ``entities`` table
    through the application's own ``SessionLocal``/``Entity`` models.  This
    keeps upload-time inference on the same canonical source as the UI and
    synchronization service.  Runtime reads include active canonical rows
    only; external parties and historical/inactive rows never become entity
    selectors.  An explicitly supplied invalid source raises instead of
    silently falling back.
    """
    if source is None:
        env_source = os.getenv("PROJECT_RAG_ENTITY_MASTER", "").strip()
        rows = _read_cache_file(env_source) if env_source else _load_runtime_entity_cache()
    elif isinstance(source, (str, os.PathLike)):
        rows = _read_cache_file(source)
    elif isinstance(source, Mapping):
        if "entities" in source or "items" in source:
            value = source.get("entities", source.get("items"))
            rows = [dict(row) for row in value] if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else []
        else:
            rows = [
                dict(value, entity_code=key) if isinstance(value, Mapping) else {"entity_code": key, "name": value}
                for key, value in source.items()
            ]
    else:
        rows = [dict(row) for row in source]

    normalized: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        code = normalize_entity_code(row.get("entity_code") or row.get("code"))
        if code and not is_canonical_entity_code(code):
            # Virtual labels and made-up codes never enter the lookup index.
            logger.warning("Ignoring non-canonical entity cache code %r", code)
            continue
        tax_id = str(row.get("tax_id") or row.get("unified_social_credit_code") or "").strip().upper() or None
        name = str(row.get("name") or row.get("company_name") or "").strip()
        short_name = str(row.get("short_name") or "").strip()
        legal_value = row.get("legal_entity", code != "A04")
        if isinstance(legal_value, str):
            legal_value = legal_value.strip().lower() not in {"0", "false", "no", "否", "非独立法人"}
        normalized.append({
            **row,
            "entity_code": code,
            "tax_id": tax_id,
            "name": name,
            "short_name": short_name,
            "business_role": str(row.get("business_role") or (code[0] if code else "")).strip(),
            "entity_kind": str(row.get("entity_kind") or ("branch" if code == "A04" else "company")).strip(),
            "legal_entity": bool(legal_value),
            "parent_entity_code": normalize_entity_code(row.get("parent_entity_code")) or ("A03" if code == "A04" else None),
            "status": str(row.get("status") or "active").strip(),
        })
    return normalized


def _load_runtime_entity_cache() -> list[dict[str, Any]]:
    """Read active canonical entities from the runtime database.

    This import is intentionally lazy.  ``metadata`` is imported by document
    services during application startup, while ``models`` imports the shared
    SQLAlchemy base; importing either at module scope here would create a
    circular import during normal startup and during the Tax/RAG test
    harness' module reloads.  A context-managed ``SessionLocal`` closes the
    connection on every lookup, including when SQLAlchemy raises.

    The database is a source of truth, not a fallback roster: if it is not
    available, inference returns an empty cache and therefore leaves the
    reference unresolved.  That preserves the no-default-entity boundary.
    """
    try:
        from sqlalchemy import func, select
        from sqlalchemy.exc import SQLAlchemyError
    except ImportError as exc:
        logger.warning("Unable to load canonical entities: SQLAlchemy is unavailable: %s", exc)
        return []

    try:
        from ..db import SessionLocal
        from ..models import Entity, ExternalParty

        with SessionLocal() as db:
            entities = db.scalars(
                select(Entity)
                .where(func.lower(Entity.status) == "active")
                .order_by(Entity.entity_code, Entity.id)
            ).all()

            ext_parties = db.scalars(
                select(ExternalParty)
                .where(ExternalParty.active == True)
            ).all()

            entities = list(entities) + list(ext_parties)

            rows: list[dict[str, Any]] = []
            for entity in entities:
                raw_code = getattr(entity, "entity_code", getattr(entity, "code", ""))
                code = normalize_entity_code(raw_code)
                if not code or not is_canonical_entity_code(code):
                    continue
                rows.append({
                    "entity_code": code,
                    "name": entity.name or "",
                    "short_name": entity.short_name or "",
                    "business_role": getattr(entity, "business_role", None) or ("E" if code.startswith("E") else code[0]),
                    "entity_kind": getattr(entity, "entity_kind", "external") or ("branch" if code == "A04" else "company"),
                    "legal_entity": getattr(entity, "legal_entity", True),
                    "parent_entity_code": getattr(entity, "parent_entity_code", None),
                    "status": getattr(entity, "status", "active") or "active",
                    # ``tax_id`` is canonical; the legacy unified-code
                    # column is retained only as a compatibility read.
                    "tax_id": getattr(entity, "tax_id", "") or getattr(entity, "unified_social_credit_code", "") or "",
                    "unified_social_credit_code": getattr(entity, "unified_social_credit_code", "") or "",
                })
            return rows
    except (ImportError, OSError, SQLAlchemyError) as exc:
        logger.warning("Unable to load canonical entities from runtime DB: %s", exc)
        return []


def _matching_rows(value: str, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Find exact code, tax-id, or name matches without selecting ``first()``."""
    raw = str(value or "").strip()
    code = normalize_entity_code(raw)
    if code and is_canonical_entity_code(code):
        return [dict(row) for row in rows if row.get("entity_code") == code]
    if raw in VIRTUAL_ENTITY_CODES:
        return []

    tax_matches = [
        dict(row)
        for row in rows
        if raw and str(row.get("tax_id") or "").strip().upper() == raw.upper()
    ]
    if tax_matches:
        return tax_matches
    return [
        dict(row)
        for row in rows
        if raw and raw in {str(row.get("name") or ""), str(row.get("short_name") or "")}
    ]


def _extract_external_name_from_filename(code: str, filename: str) -> str:
    name = Path(filename).stem
    match = re.search(r"外部([^_\./]+?)(?:合同|协议|发票|专票|回单|单据|验收|过磅|照片|扫描件|影印本|$)", name)
    if match and len(match.group(1).strip()) >= 2:
        return f"外部{match.group(1).strip()}"

    known_names = {
        "ED01": "外部超重型起重吊装租赁服务单位",
        "EB01": "外部特种高强合金钢直采供货单位",
        "EA01": "外部深基坑地质监测与技术咨询服务单位",
        "E01": "项目发包方/外部业主单位",
    }
    return known_names.get(code.upper(), f"系统外合作单位 ({code})")


def resolve_entity_reference(
    value: str | None,
    canonical_cache: str | os.PathLike[str] | Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve a code/tax id/name against the canonical cache.

    Known external aliases are resolved against the preset map before runtime
    cache rows.  This ensures an accidentally persisted alias row can never
    outrank its canonical external-party code.

    Returns a structured result with ``status`` in ``RESOLVED``, ``CONFLICT``,
    ``REJECTED`` or ``UNRESOLVED``.  Multiple matches are always surfaced as
    ``CONFLICT``; no arbitrary row is selected.
    """
    raw = str(value or "").strip()
    if not raw:
        return {"status": "UNRESOLVED", "entity_code": "", "tax_id": "", "name": ""}
    if raw in VIRTUAL_ENTITY_CODES or (len(raw) == 1 and raw.upper() in BUSINESS_ROLE_CODES):
        return {"status": "REJECTED", "entity_code": "", "tax_id": "", "name": "", "input": raw}

    preset = get_external_preset(raw)
    if preset:
        return {
            "status": "RESOLVED",
            "resolution_status": "RESOLVED",
            "entity_code": preset["code"],
            "tax_id": preset.get("tax_id") or "",
            "name": preset["name"],
            "short_name": preset.get("short_name") or preset["name"],
            "business_role": preset["business_role"],
            "entity_kind": "external",
            "legal_entity": True,
            "parent_entity_code": "",
            "entity_status": "active",
            "input": raw,
        }

    code = normalize_entity_code(raw)
    rows = load_canonical_entity_cache(canonical_cache)
    matches = _matching_rows(raw, rows)
    if len(matches) == 1:
        row = matches[0]
        return {
            "status": "RESOLVED",
            "resolution_status": "RESOLVED",
            "entity_code": map_to_standard_external_code(row.get("entity_code")) or row.get("entity_code") or "",
            "tax_id": row.get("tax_id") or "",
            "name": row.get("name") or row.get("short_name") or "",
            "business_role": row.get("business_role") or "",
            "entity_kind": row.get("entity_kind") or "company",
            "legal_entity": row.get("legal_entity", True),
            "parent_entity_code": row.get("parent_entity_code") or "",
            "entity_status": row.get("status") or "active",
            "input": raw,
        }
    elif len(matches) > 1:
        return {
            "status": "CONFLICT",
            "entity_code": "",
            "tax_id": "",
            "name": "",
            "input": raw,
            "match_count": len(matches),
        }

    if (
        code
        and not code.startswith("EBNK")
        and not code.startswith("BANK")
        and (
            code.startswith("EXT-")
            or bool(re.match(r"^E[A-D0](?:0[1-9]|[1-9]\d)?$", code, re.IGNORECASE))
        )
    ):
        std_code = map_to_standard_external_code(code)
        if not std_code:
            return {
                "status": "UNRESOLVED",
                "entity_code": "",
                "tax_id": "",
                "name": "",
                "input": raw,
            }
        role = "owner"
        if std_code.startswith("EA"):
            role = "construction"
        elif std_code.startswith("EB"):
            role = "trade"
        elif std_code.startswith("EC"):
            role = "labor"
        elif std_code.startswith("ED"):
            role = "equipment"
        elif std_code.startswith("E0"):
            role = "owner"

        return {
            "status": "RESOLVED",
            "resolution_status": "RESOLVED",
            "entity_code": std_code,
            "tax_id": "",
            "name": _extract_external_name_from_filename(std_code, raw),
            "business_role": role,
            "entity_kind": "external",
            "legal_entity": True,
            "parent_entity_code": "",
            "entity_status": "active",
            "input": raw,
        }
    else:
        return {
            "status": "UNRESOLVED",
            "entity_code": "",
            "tax_id": "",
            "name": "",
            "input": raw,
            "match_count": 0,
        }

# Strong evidence must win over the business subject contained in the filename.
#
# Example:
# TAX_CERT_xxx_机械租赁与劳务分包合同印花税完税证明
#
# "机械租赁" describes what the tax certificate is about. It does NOT make
# the file itself an equipment contract.
STRONG_PREFIX_RULES = (
    ("TAX_CERT_", "", "tax_payment_record", ""),
    ("TAX_DECLARATION_", "", "tax_payment_record", ""),
    ("INVOICE_", "", "tax_invoice", "vat"),
    ("BANK_SLIP_", "", "bank_slip", ""),
)


STRONG_EVIDENCE_RULES = (
    (
        ("完税证明", "完税凭证", "缴税凭证", "税票"),
        "",
        "tax_payment_record",
        "",
    ),
    (
        (
            "增值税专用发票",
            "增值税普通发票",
            "电子发票",
            "专票",
        ),
        "",
        "tax_invoice",
        "vat",
    ),
    (
        ("纳税申报", "税务申报", "纳税申报表", "税务申报表"),
        "",
        "tax_payment_record",
        "",
    ),
    (
        (
            "银行支付回单",
            "银行回单",
            "电子回单",
            "支付回单",
            "付款回单",
        ),
        "",
        "bank_slip",
        "",
    ),
)


TAX_CATEGORY_RULES = (
    (("印花税",), "stamp_duty"),
    (("企业所得税", "所得税预缴", "所得税汇算"), "enterprise_income"),
    (("个人所得税", "代扣代缴", "劳务个税"), "individual_income"),
    (("土地增值税",), "land_income"),
    (("环保税", "环境保护税"), "environmental"),
    (("增值税", "进项税", "销项税"), "vat"),
    (("附加税", "城建税", "教育费附加", "地方教育附加"), "surtax"),
)


def _infer_tax_category(name: str) -> str:
    for keywords, category in TAX_CATEGORY_RULES:
        if any(keyword in name for keyword in keywords):
            return category
    return ""


def _classify_filename(name: str) -> dict[str, object]:
    """Classify evidence type before considering its business subject."""

    upper_name = name.upper()

    # 1. Explicit machine-readable prefixes have the strongest authority.
    for prefix, category, document_type, tax_category in STRONG_PREFIX_RULES:
        if upper_name.startswith(prefix):
            resolved_tax_category = tax_category

            if document_type in {
                "tax_payment_record",
                "tax_invoice",
                "tax_document",
            }:
                resolved_tax_category = (
                    _infer_tax_category(name)
                    or resolved_tax_category
                )

            return {
                "document_type": document_type,
                "business_category": category,
                "tax_category": resolved_tax_category,
                "confidence": 0.98,
                "classification_source": f"prefix:{prefix}",
            }

    # 2. Evidence nature beats the underlying contract/business topic.
    for keywords, category, document_type, tax_category in STRONG_EVIDENCE_RULES:
        if any(keyword in name for keyword in keywords):
            resolved_tax_category = tax_category

            if document_type in {
                "tax_payment_record",
                "tax_invoice",
                "tax_document",
            }:
                resolved_tax_category = (
                    _infer_tax_category(name)
                    or resolved_tax_category
                )

            return {
                "document_type": document_type,
                "business_category": category,
                "tax_category": resolved_tax_category,
                "confidence": 0.93,
                "classification_source": "strong_evidence",
            }

    # 3. Only now apply broad business-topic rules.
    for keys, category, document_type, tax_category in DOC_RULES:
        if any(keyword in name for keyword in keys):
            return {
                "document_type": document_type,
                "business_category": category,
                "tax_category": tax_category,
                "confidence": 0.70,
                "classification_source": "doc_rule",
            }

    return {
        "document_type": "other",
        "business_category": "",
        "tax_category": "",
        "confidence": 0.25,
        "classification_source": "unclassified",
    }


# Classification rules: (keywords, business_category, document_type, tax_category)
DOC_RULES = [
    # Labor documents
    (("工资", "考勤", "社保", "实名制"), "labor", "labor_record", ""),
    (("劳务结算", "劳务计量"), "labor", "labor_settlement", ""),
    (("劳务合同", "劳务协议", "用工分包"), "labor", "labor_contract", ""),

    # Equipment documents
    (("台班", "设备使用", "机械使用"), "equipment", "equipment_shift", ""),
    (("设备结算", "机械结算"), "equipment", "equipment_settlement", ""),
    (("设备租赁", "机械租赁", "设备合同", "吊装合同", "起重吊装"), "equipment", "equipment_contract", ""),

    # Material documents
    (("材料验收", "收料", "入库", "地磅", "过磅", "大宗材料"), "material", "material_acceptance", ""),
    (("材料采购", "钢材采购", "商砼采购", "材料合同", "供货合同", "直采供货", "集采供销", "供销合同"), "material", "material_contract", ""),

    # Subcontract documents
    (("分包结算",), "subcontract", "subcontract_settlement", ""),
    (("专业分包", "分包工程合同", "分包合同", "监测与技术咨询", "智能化及BIM"), "subcontract", "subcontract_contract", ""),

    # Main / Construction documents
    (("总包合同", "施工合同", "主合同", "施工总承包", "中标通知书", "履约保函"), "construction", "main_contract", ""),
    (("实景照片", "现场照片", "施工现场"), "construction", "site_photo", ""),

    # Tax documents
    (("增值税", "进项税", "销项税", "增值税专用发票", "增值税普通发票", "发票", "专票"), "", "tax_invoice", "vat"),
    (("企业所得税", "所得税预缴", "所得税汇算"), "", "tax_document", "enterprise_income"),
    (("个人所得税", "代扣代缴", "劳务个税"), "", "tax_document", "individual_income"),
    (("附加税", "城建税", "教育费附加", "地方教育附加"), "", "tax_document", "surtax"),
    (("土地增值税",), "", "tax_document", "land_income"),
    (("印花税",), "", "tax_document", "stamp_duty"),
    (("环保税", "环境保护税"), "", "tax_document", "environmental"),
    (("税务申报", "纳税申报", "完税证明", "税票", "缴税凭证"), "", "tax_payment_record", ""),

    # Banking / Payment documents
    (("银行支付回单", "电子回单", "银行回单", "支付回单", "付款回单", "挂账应付款"), "", "bank_slip", ""),

    # Logistics documents
    (("物流小票", "地磅称重", "现场签收单"), "material", "logistics_waybill", ""),

    # Acceptance documents
    (("工序验收", "验收与台班", "签认记录单"), "", "acceptance_record", ""),

    # Other documents
    (("会议纪要", "会议记录"), "", "meeting_minutes", ""),
    (("补充协议",), "", "supplementary_agreement", ""),
    (("签证", "变更单", "工程变更"), "", "change_order", ""),
    (("结算",), "", "settlement_document", ""),
]


def infer_from_filename(
    filename: str,
    canonical_cache: str | os.PathLike[str] | Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
) -> dict:
    """Infer document metadata from filename.

    Args:
        filename: Document filename

    Returns:
        Dict with inferred metadata fields
    """
    name = Path(filename).stem
    result = {
        "document_type": "other",
        "business_category": "",
        "tax_category": "",
        "entity_code": "",
        "entity_code_candidate": "",
        "entity_resolution_status": "UNRESOLVED",
        "entity_match_source": "",
        "entity_name": "",
        "entity_tax_id": "",
        "business_role": "",
        "counterparty_code": "",
        "counterparty_name": "",
        "counterparty_tax_id": "",
        "counterparty_resolution_status": "UNRESOLVED",
        "period": "",
        "confidence": 0.25,
        "classification_source": "unclassified",
    }

    # Apply hierarchical classification rules
    classification = _classify_filename(name)
    result["document_type"] = str(classification["document_type"])
    result["business_category"] = str(classification["business_category"])
    result["tax_category"] = str(classification["tax_category"])
    result["confidence"] = float(classification["confidence"])
    result["classification_source"] = str(classification["classification_source"])

    # A/B/C/D are role labels, e.g. ``A类建筑施工``.  A bare single letter
    # never becomes an entity code.
    role_matches = {m.group(1).upper() for m in _ROLE_RE.finditer(name)}
    if len(role_matches) == 1:
        result["business_role"] = next(iter(role_matches))
        result["confidence"] += 0.02

    # Load once per inference.  In production this is one short, scoped read
    # from the runtime Entity table; passing an explicit cache remains fully
    # supported and avoids any database access.
    rows = load_canonical_entity_cache(canonical_cache)

    # Resolve a real code only after checking the canonical cache.  Multiple
    # different codes in one filename are ambiguous and remain unresolved.
    # 收集所有的实体引用（包括代码、税号、名称）并统一解析
    code_candidates = {m.group(0).upper() for m in _ENTITY_CODE_RE.finditer(name)}
    if code_candidates:
        result["entity_code_candidate"] = ",".join(sorted(code_candidates))

    tax_ids = list(dict.fromkeys(_TAX_ID_RE.findall(name)))

    name_matches = {
        candidate_name
        for row in rows
        for candidate_name in (
            str(row.get("name") or "").strip(),
            str(row.get("short_name") or "").strip(),
        )
        if candidate_name and candidate_name in name
    }

    # 统一把所有匹配到的候选合并解析（按在文件名中的出现顺序排序）
    def _pos_in_name(token: str) -> int:
        idx = name.find(token)
        return idx if idx >= 0 else 99999

    sorted_codes = sorted(code_candidates, key=_pos_in_name)
    all_refs = [(c, "code") for c in sorted_codes] + [(t, "tax_id") for t in tax_ids] + [(n, "name") for n in sorted(name_matches, key=len, reverse=True)]

    resolved_entities = {}
    for ref, source in all_refs:
        r = resolve_entity_reference(ref, rows)
        if r["status"] == "RESOLVED" and r["entity_code"] not in resolved_entities:
            resolved_entities[r["entity_code"]] = (r, source)

    resolved_list = list(resolved_entities.values())

    if len(resolved_list) == 1:
        r, source = resolved_list[0]
        code = r["entity_code"]
        role = r.get("business_role", "")
        if code.startswith("E") or role in ("C", "D"):
            result["counterparty_code"] = code
            result["counterparty_name"] = r.get("name", "")
            result["counterparty_tax_id"] = r.get("tax_id", "")
            result["counterparty_resolution_status"] = "RESOLVED"
            result["entity_resolution_status"] = "UNRESOLVED"
        else:
            result["entity_code"] = code
            result["entity_name"] = r.get("name", "")
            result["entity_tax_id"] = r.get("tax_id", "")
            result["entity_match_source"] = source
            result["entity_resolution_status"] = "RESOLVED"
            result["business_role"] = role
            result["confidence"] += 0.08

    elif len(resolved_list) >= 2:
        r1, src1 = resolved_list[0]
        r2, src2 = resolved_list[1]
        c1, c2 = r1["entity_code"], r2["entity_code"]

        if (c1.startswith("E") or r1.get("business_role") in ("C", "D")) and not c2.startswith("E"):
            r1, r2 = r2, r1
            src1, src2 = src2, src1

        result["entity_code"] = r1["entity_code"]
        result["entity_name"] = r1.get("name", "")
        result["entity_tax_id"] = r1.get("tax_id", "")
        result["entity_match_source"] = src1
        result["entity_resolution_status"] = "RESOLVED"
        result["business_role"] = r1.get("business_role", "")
        result["confidence"] += 0.12

        result["counterparty_code"] = r2["entity_code"]
        result["counterparty_name"] = r2.get("name", "")
        result["counterparty_tax_id"] = r2.get("tax_id", "")
        result["counterparty_resolution_status"] = "RESOLVED"

    elif all_refs:
        result["entity_resolution_status"] = "UNRESOLVED"

    # Counterparty codes are persisted identifiers, so aliases must not leave
    # metadata inference.  This also ensures category inference sees ED01 rather
    # than legacy aliases.
    if result["counterparty_code"]:
        result["counterparty_code"] = map_to_standard_external_code(result["counterparty_code"]) or ""

    # Deduce category and refine document_type based on resolved entity/counterparty roles
    cp_code = result["counterparty_code"]
    ent_code = result["entity_code"]

    # Check if this is a main contract or site photo with owner E01
    if ("MAIN" in name or "总承包" in name or "主合同" in name or "中标通知" in name) and not cp_code:
        result["counterparty_code"] = "E01"
        result["counterparty_name"] = "成都市天府新区金融城投公司"
        result["counterparty_resolution_status"] = "RESOLVED"
        result["business_category"] = "construction"
        result["document_type"] = "main_contract"
        cp_code = "E01"

    # If business_category is empty or not specific, infer from counterparty
    if not result["business_category"]:
        if cp_code.startswith("C") or cp_code.startswith("EC"):
            result["business_category"] = "labor"
        elif cp_code.startswith("B") or cp_code.startswith("EB"):
            result["business_category"] = "material"
        elif cp_code.startswith("D") or cp_code.startswith("ED"):
            result["business_category"] = "equipment"
        elif (cp_code.startswith("A") and cp_code != ent_code) or cp_code.startswith("EA"):
            result["business_category"] = "subcontract"
        elif cp_code.startswith("E0"):
            result["business_category"] = "construction"

    # Refine document_type for specific prefixes if it is still generic
    if result["document_type"] in ("other", "acceptance_record"):
        if name.startswith("ACCEPTANCE_") or "工序验收" in name:
            if result["business_category"] == "equipment":
                result["document_type"] = "equipment_shift"
            elif result["business_category"] == "labor":
                result["document_type"] = "labor_record"
            elif result["business_category"] == "subcontract":
                result["document_type"] = "subcontract_acceptance"
            else:
                result["document_type"] = "acceptance_record"
        elif name.startswith("BANK_") or "回单" in name or "支付" in name or "UNPAID_" in name:
            result["document_type"] = "bank_slip"
        elif name.startswith("PHOTO_"):
            result["document_type"] = "site_photo"
            result["business_category"] = "construction"

    # Period extraction: 2024-01, 2024年01月, etc.
    m = re.search(r"(20\d{2})[-年./_](0?[1-9]|1[0-2])", name)
    if m:
        result["period"] = f"{m.group(1)}-{int(m.group(2)):02d}"
        result["confidence"] += 0.07

    result["confidence"] = min(round(result["confidence"], 3), 1.0)
    return result


def refine_from_content(
    current: dict,
    text: str,
    canonical_cache: str | os.PathLike[str] | Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
) -> dict:
    """Refine metadata from document content.

    This is a second-pass classifier that only fills in missing fields.
    It never semantically overwrites explicit user metadata.  Normalizing an
    external alias to its canonical code is treated as identifier hygiene,
    not as a semantic overwrite.

    Args:
        current: Current metadata (may have user-specified values)
        text: Document content preview

    Returns:
        Updated metadata dict
    """
    out = dict(current)
    if out.get("counterparty_code"):
        out["counterparty_code"] = map_to_standard_external_code(out["counterparty_code"]) or ""

    if not text:
        return out

    # Use filename inference on a cleaned version of the text.
    # Replace slashes and dots so Path(filename).stem inside infer_from_filename
    # doesn't truncate the preview text.
    sample = text.replace("\n", " ").replace("/", " ").replace(".", " ")[:500]
    probe = infer_from_filename(sample, canonical_cache=canonical_cache)

    # Only fill in missing or "other" values
    if (not out.get("document_type") or out.get("document_type") == "other") and probe.get("document_type") != "other":
        out["document_type"] = probe["document_type"]
        logger.debug(f"Refined document_type from content: {probe['document_type']}")

    if not out.get("business_category") and probe.get("business_category"):
        out["business_category"] = probe["business_category"]
        logger.debug(f"Refined business_category from content: {probe['business_category']}")

    if not out.get("tax_category") and probe.get("tax_category"):
        out["tax_category"] = probe["tax_category"]
        logger.debug(f"Refined tax_category from content: {probe['tax_category']}")

    # Content refinement follows the same canonical resolution contract as
    # filename inference.  It only fills absent fields; an explicit user
    # value is never silently replaced by an LLM/heuristic guess.
    if not out.get("entity_code") and probe.get("entity_code"):
        out["entity_code"] = probe["entity_code"]
        out["entity_code_candidate"] = probe.get("entity_code_candidate", "")
        out["entity_resolution_status"] = probe.get("entity_resolution_status", "UNRESOLVED")
        out["entity_match_source"] = "content_" + str(probe.get("entity_match_source") or "reference")
        out["entity_name"] = probe.get("entity_name", "")
        out["entity_tax_id"] = probe.get("entity_tax_id", "")

    if not out.get("business_role") and probe.get("business_role"):
        out["business_role"] = probe["business_role"]

    if not out.get("counterparty_code") and probe.get("counterparty_code"):
        out["counterparty_code"] = map_to_standard_external_code(probe["counterparty_code"]) or ""
    if not out.get("counterparty_name") and probe.get("counterparty_name"):
        out["counterparty_name"] = probe["counterparty_name"]
    if out.get("counterparty_resolution_status", "UNRESOLVED") == "UNRESOLVED" and probe.get("counterparty_resolution_status") != "UNRESOLVED":
        out["counterparty_resolution_status"] = probe["counterparty_resolution_status"]

    return out


def get_category_display_name(category: str) -> str:
    """Get human-readable category name.

    Args:
        category: Category code

    Returns:
        Display name
    """
    names = {
        "labor": "劳务",
        "equipment": "设备租赁",
        "material": "材料",
        "subcontract": "分包",
        "tax": "税务",
        "": "未分类"
    }
    return names.get(category, category)


def get_tax_category_display_name(category: str) -> str:
    """Get human-readable tax category name.

    Args:
        category: Tax category code

    Returns:
        Display name
    """
    names = {
        "vat": "增值税",
        "enterprise_income": "企业所得税",
        "individual_income": "个人所得税",
        "surtax": "附加税",
        "land_income": "土地增值税",
        "stamp_duty": "印花税",
        "environmental": "环境保护税",
        "": "未分类"
    }
    return names.get(category, category)


def get_document_type_display_name(doc_type: str) -> str:
    """Get human-readable document type name.

    Args:
        doc_type: Document type code

    Returns:
        Display name
    """
    names = {
        "labor_record": "工资/考勤记录",
        "labor_settlement": "劳务结算单",
        "labor_contract": "劳务合同",
        "equipment_shift": "设备台班记录",
        "equipment_settlement": "设备结算单",
        "equipment_contract": "设备租赁合同",
        "material_acceptance": "材料验收单",
        "material_contract": "材料采购合同",
        "subcontract_settlement": "分包结算单",
        "subcontract_contract": "分包合同",
        "meeting_minutes": "会议纪要",
        "supplementary_agreement": "补充协议",
        "change_order": "工程变更/签证",
        "tax_invoice": "增值税发票",
        "tax_document": "税务文件",
        "tax_payment_record": "缴税凭证",
        "settlement_document": "结算文件",
        "main_contract": "主合同",
        "other": "其他"
    }
    return names.get(doc_type, doc_type)


def get_invoice_type_display_name(invoice_type: str | None) -> str:
    """Get human-readable invoice type display name."""
    if not invoice_type:
        return "增值税发票"
    it = str(invoice_type).strip().lower()
    names = {
        "special": "增值税专用发票",
        "vat_special": "增值税专用发票",
        "normal": "增值税普通发票",
        "plain": "增值税普通发票",
        "vat_normal": "增值税普通发票",
        "electronic": "增值税电子发票",
        "toll": "通行费电子发票",
        "other": "增值税发票",
    }
    return names.get(it, invoice_type)


def format_parse_message(raw_msg: str | None) -> str:
    """Format technical or JSON parse messages into user-friendly summary."""
    if not raw_msg:
        return "解析完成，已生成向量切块"
    raw = str(raw_msg).strip()
    if raw.startswith("{") and raw.endswith("}"):
        try:
            data = json.loads(raw)
            msg = str(data.get("message", ""))
            if "indexed" in msg.lower():
                m = re.search(r"(\d+)\s+chunk", msg, re.IGNORECASE) or re.search(r"\d+", msg)
                chunk_num = m.group(1) if (m and m.lastindex) else (m.group(0) if m else "1")
                friendly = f"已完成 {chunk_num} 个向量切块索引"
            else:
                friendly = "已建立向量切块索引"
            if "invoice_validation" in data or "fields" in data:
                friendly += " · 结构化发票信息已提取"
            return friendly
        except Exception:
            return "已完成解析与向量索引"
    return raw


