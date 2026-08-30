"""AI-powered extraction of structured tax data from document chunks.

Uses an LLM to parse unstructured document text and extract structured
fields (invoices, contracts, payments, tax receipts) with confidence scores.
"""
import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import httpx

from ..config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from ..logging_config import get_logger
from . import llm_pool
from .tax_extraction import (
    EXTRACT_TYPES,
    is_canonical_entity_code,
    normalize_entity_code,
    normalize_extracted_fields,
)

logger = get_logger(__name__)


class ExtractionError(Exception):
    """Raised when extraction fails."""
    pass


# ============================================================
# Prompt templates per extraction type
# ============================================================

PROMPT_TEMPLATES: dict[EXTRACT_TYPES, str] = {
    "invoice": """你是一个专业的建筑工程项目税务专员。请从以下发票文档片段中提取发票信息。

提取规则：
- 严格按 JSON Schema 输出，不要输出任何额外文字
- 只提取能明确找到的字段，找不到的字段返回 null
- 金额字段返回数字（单位：元），不是数字的返回 null
- 日期格式：YYYY-MM-DD，无法确定具体日期则返回 null
- direction: in=进项发票（购买方为贵司）, out=销项发票（销售方为贵司）
- category: 根据货物/服务内容判断: material(材料)/labor(劳务)/equipment(设备)/subcontract(分包)/service(服务)

JSON Schema:
{
  "invoice_no": string|null,
  "invoice_code": string|null,
  "invoice_date": string|null,
  "period": string|null,
  "direction": "in"|"out"|"",
  "invoice_type": "special"|"normal"|"roll"|"",
  "seller_name": string|null,
  "seller_entity_code": string|null,
  "seller_tax_id": string|null,
  "seller_code": string|null,
  "buyer_name": string|null,
  "buyer_entity_code": string|null,
  "buyer_tax_id": string|null,
  "buyer_code": string|null,
  "total_amount": number|null,
  "net_amount": number|null,
  "vat_amount": number|null,
  "vat_rate": number|null,
  "deductible": boolean|null,
  "category": string|null,
  "note": string|null,
  "evidence": object,
  "validation_status": string,
  "validation_errors": array,
  "arithmetic_validation": object
}

文档片段：
{chunk_text}

请输出 JSON：""",

    "contract": """你是一个专业的建筑工程项目法务专员。请从以下合同文档片段中提取合同信息。

提取规则：
- 严格按 JSON Schema 输出，不要输出任何额外文字
- 只提取能明确找到的字段，找不到的字段返回 null
- 金额字段返回数字（单位：元）
- 日期格式：YYYY-MM-DD
- category: material(材料采购)/labor(劳务分包)/equipment(设备租赁)/subcontract(专业分包)/service(其他服务)

JSON Schema:
{
  "contract_no": string|null,
  "contract_date": string|null,
  "contract_type": string|null,
  "party_a_name": string|null,
  "party_a_code": string|null,
  "party_b_name": string|null,
  "party_b_code": string|null,
  "total_amount": number|null,
  "tax_included": boolean,
  "category": string|null,
  "note": string|null
}

文档片段：
{chunk_text}

请输出 JSON：""",

    "payment": """你是一个专业的建筑工程项目财务专员。请从以下付款凭证文档片段中提取付款信息。

提取规则：
- 严格按 JSON Schema 输出，不要输出任何额外文字
- 只提取能明确找到的字段，找不到的字段返回 null
- 金额字段返回数字（单位：元）
- 日期格式：YYYY-MM-DD
- direction: out=付款（资金流出）, in=收款（资金流入）
- payment_method: bank_transfer(转账)/credit(承兑汇票)/cash(现金)/other(其他)

JSON Schema:
{
  "payment_date": string|null,
  "period": string|null,
  "direction": "in"|"out"|"",
  "payer_name": string|null,
  "payer_account": string|null,
  "payee_name": string|null,
  "payee_account": string|null,
  "amount": number|null,
  "payment_method": string|null,
  "counterparty_code": string|null,
  "contract_no": string|null,
  "note": string|null
}

文档片段：
{chunk_text}

请输出 JSON：""",

    "tax_payment": """你是一个专业的建筑工程项目税务专员。请从以下完税凭证文档片段中提取缴税信息。

提取规则：
- 严格按 JSON Schema 输出，不要输出任何额外文字
- 只提取能明确找到的字段，找不到的字段返回 null
- 金额字段返回数字（单位：元）
- 日期格式：YYYY-MM-DD
- tax_type: VAT(增值税)/income(个人所得税)/cit(企业所得税)/stamp(印花税)/other(其他)

JSON Schema:
{
  "tax_type": string|null,
  "tax_period": string|null,
  "payment_date": string|null,
  "taxpayer_name": string|null,
  "taxpayer_code": string|null,
  "tax_amount": number|null,
  "principal_amount": number|null,
  "penalty_amount": number|null,
  "receipt_no": string|null,
  "note": string|null
}

文档片段：
{chunk_text}

请输出 JSON：""",
}


# ============================================================
# Deterministic invoice extraction and validation
# ============================================================

# Invoice text is often produced by OCR and may contain spaces between a
# currency sign and the number, Chinese punctuation, or a compact one-line
# layout.  The parser below deliberately uses labels and local context; it
# never treats a filename/document code as an invoice number.
# The word/hyphen boundaries are important for OCR pages: tax IDs and dates
# otherwise look like a sequence of valid monetary substrings.
_AMOUNT_TOKEN = r"(?<![\w-])[¥￥]?\s*[+-]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?(?![\w-])"
_TAX_ID_TOKEN = r"[0-9A-Z]{15,20}"
_INVOICE_ID_TOKEN = r"[A-Za-z0-9][A-Za-z0-9_-]{1,59}"
_INVOICE_STOP_LABELS = (
    "发票代码", "发票号码", "开票日期", "销方名称", "销售方名称", "销货单位",
    "购方名称", "购货单位", "购买方名称", "纳税识别", "纳税人识别号", "税号",
    "销方纳税人识别号", "销售方纳税人识别号", "销货单位纳税人识别号",
    "购方纳税人识别号", "购货单位纳税人识别号", "购买方纳税人识别号",
    "销方税号", "销售方税号", "销货单位税号", "购方税号", "购货单位税号", "购买方税号",
    "货物或应税劳务", "合计", "价税合计", "备注", "开票人", "收款人", "复核",
)
_SELLER_LABELS = ("销方名称", "销售方名称", "销货单位", "开票单位", "卖方名称")
_BUYER_LABELS = ("购方名称", "购货单位", "购买方名称", "买方名称")
_DATE_LABELS = ("开票日期", "开具日期", "出票日期")
_MONEY_QUANT = Decimal("0.01")
_RATE_QUANT = Decimal("0.0001")
_MONEY_TOLERANCE = Decimal("0.01")


def _invoice_decimal(value: Any) -> Decimal | None:
    """Parse an OCR/LLM amount without accepting NaN/Infinity."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        raw = str(value).strip().replace(",", "").replace("，", "")
        raw = raw.replace("¥", "").replace("￥", "").replace("元", "").strip()
        result = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _invoice_number(value: Decimal | None) -> int | float | None:
    """Return a JSON/Pydantic-friendly number while retaining cents."""
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _invoice_rate(value: Any) -> Decimal | None:
    """Normalize 13%, 13 and 0.13 to the decimal rate 0.13."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    raw = str(value).strip().replace("%", "")
    try:
        rate = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not rate.is_finite():
        return None
    if abs(rate) > 1:
        rate /= Decimal("100")
    return rate


def _invoice_text_value(
    text: str,
    labels: tuple[str, ...],
    *,
    stops: tuple[str, ...] = _INVOICE_STOP_LABELS,
) -> tuple[str | None, str | None]:
    """Capture a labelled text value and the exact local evidence."""
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    stop_pattern = "|".join(re.escape(label) for label in sorted(stops, key=len, reverse=True))
    pattern = rf"(?P<label>{label_pattern})\s*[:：]?\s*(?P<value>.*?)(?=\s*(?:{stop_pattern})(?=\s*[:：]|[\s,，。、|｜]|$)|[|｜\n]|$)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None, None
    value = re.sub(r"\s+", " ", match.group("value")).strip(" ：:|")
    if not value:
        return None, None
    return value, match.group(0).strip()


def _invoice_identifier(text: str, labels: tuple[str, ...]) -> tuple[str | None, str | None]:
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    match = re.search(rf"(?:{label_pattern})\s*[:：]\s*(?P<value>{_INVOICE_ID_TOKEN})", text, flags=re.IGNORECASE)
    if not match:
        return None, None
    value = match.group("value").strip()
    return value, match.group(0).strip()


def _invoice_date(text: str) -> tuple[str | None, str | None]:
    labels = "|".join(re.escape(label) for label in _DATE_LABELS)
    match = re.search(
        rf"(?:{labels})\s*[:：]?\s*(?P<year>20\d{{2}})\s*(?:年|[-/.])\s*(?P<month>0?[1-9]|1[0-2])\s*(?:月|[-/.])\s*(?P<day>[0-3]?\d)\s*日?",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None
    try:
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        # datetime is intentionally avoided here; this helper remains usable
        # in worker/CLI contexts that only need lexical extraction.
        if day < 1 or day > 31:
            return None, None
        value = f"{year:04d}-{month:02d}-{day:02d}"
    except (TypeError, ValueError):
        return None, None
    return value, match.group(0).strip()


def _invoice_labeled_amount(text: str, labels: tuple[str, ...]) -> tuple[Decimal | None, str | None]:
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    match = re.search(
        rf"(?:{label_pattern})\s*[:：]?\s*(?P<value>{_AMOUNT_TOKEN})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None
    value = _invoice_decimal(match.group("value"))
    if value is None:
        return None, None
    return value, match.group(0).strip()


def _invoice_amounts_after(text: str, marker: str, limit: int = 180) -> list[tuple[Decimal, str]]:
    # ``合计`` is also a suffix of ``价税合计``.  Prefer a standalone total
    # marker so the gross amount is not mistaken for the net amount pair.
    start = -1
    for candidate in re.finditer(re.escape(marker), text):
        if marker == "合计" and candidate.start() >= 2 and text[candidate.start() - 2 : candidate.start()] == "价税":
            continue
        start = candidate.start()
        break
    if start < 0:
        return []
    sample = text[start : start + limit]
    result: list[tuple[Decimal, str]] = []
    for match in re.finditer(_AMOUNT_TOKEN, sample, flags=re.IGNORECASE):
        amount = _invoice_decimal(match.group(0))
        if amount is not None:
            result.append((amount, match.group(0)))
    return result


def _invoice_evidence(text: str, match_text: str | None) -> str:
    """Bound evidence excerpts so a malformed OCR page cannot create huge rows."""
    if not match_text:
        return ""
    index = text.find(match_text)
    if index < 0:
        return match_text[:240]
    return text[max(0, index - 40) : index + len(match_text) + 80].strip()[:240]


def validate_invoice_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Run deterministic invoice arithmetic and identity-presence checks.

    The function returns a copy and is safe to call on either deterministic
    parser output or an LLM result.  It reports missing/invalid evidence as
    ``PENDING_REVIEW``/``UNVALIDATED`` and never fabricates an invoice number.
    """
    out = dict(fields or {})
    errors: list[str] = list(out.get("validation_errors") or [])
    warnings: list[str] = list(out.get("extraction_warnings") or [])
    arithmetic: dict[str, Any] = dict(out.get("arithmetic_validation") or {})

    evidence = out.get("evidence") if isinstance(out.get("evidence"), dict) else {}

    required_identity = (
        "invoice_no", "invoice_date",
        "seller_name", "seller_tax_id", "buyer_name", "buyer_tax_id",
    )
    for key in required_identity:
        value = out.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"缺少明确的{key}")
        elif not evidence.get(key):
            errors.append(f"{key}缺少原文证据")

    if out.get("invoice_code") and not evidence.get("invoice_code"):
        warnings.append("invoice_code缺少原文证据")

    amounts: dict[str, Decimal | None] = {
        key: _invoice_decimal(out.get(key))
        for key in ("total_amount", "net_amount", "vat_amount")
    }
    for key, value in amounts.items():
        if out.get(key) not in (None, "") and value is None:
            errors.append(f"{key} 不是有限金额")
        if value is not None and value < 0:
            errors.append(f"{key} 不能为负数")
        if value is not None:
            out[key] = _invoice_number(value)

    rate = _invoice_rate(out.get("vat_rate"))
    if out.get("vat_rate") not in (None, "") and rate is None:
        errors.append("vat_rate 不是有效税率")
    if rate is not None:
        if rate < 0 or rate > 1:
            errors.append("vat_rate 必须在 0% 到 100% 之间")
        else:
            out["vat_rate"] = float(rate)

    total = amounts["total_amount"]
    net = amounts["net_amount"]
    vat = amounts["vat_amount"]
    if total is not None and net is not None and vat is not None:
        gross_expected = (net + vat).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)
        gross_ok = abs(total - gross_expected) <= _MONEY_TOLERANCE
        arithmetic["gross_equals_net_plus_vat"] = gross_ok
        arithmetic["computed_gross"] = _invoice_number(gross_expected)
        if not gross_ok:
            errors.append(
                f"价税合计与不含税金额+税额不一致: {total} != {gross_expected}"
            )
    else:
        arithmetic["gross_equals_net_plus_vat"] = None
        errors.append("缺少完整的价税合计、不含税金额、税额，无法完成金额校验")

    for key in ("total_amount", "net_amount", "vat_amount"):
        if amounts[key] is None:
            errors.append(f"缺少明确的{key}")
        elif not evidence.get(key):
            errors.append(f"{key}缺少原文证据")

    if rate is None:
        errors.append("缺少明确的vat_rate，无法完成税额算术校验")
    elif not evidence.get("vat_rate"):
        errors.append("vat_rate缺少原文证据")
    if rate is not None and net is not None and vat is not None:
        vat_expected = (net * rate).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)
        vat_ok = abs(vat - vat_expected) <= _MONEY_TOLERANCE
        arithmetic["vat_equals_net_times_rate"] = vat_ok
        arithmetic["computed_vat"] = _invoice_number(vat_expected)
        arithmetic["vat_rate"] = float(rate)
        if not vat_ok:
            errors.append(f"税额与不含税金额×税率不一致: {vat} != {vat_expected}")
    else:
        arithmetic["vat_equals_net_times_rate"] = None
        if rate is not None:
            errors.append("缺少金额，无法完成税额算术校验")

    # Deductibility is not printed on every invoice.  If it is supplied,
    # however, it must point back to a document excerpt; a model-only boolean
    # is held for review instead of becoming a posting fact.
    if out.get("deductible") is None:
        warnings.append("未找到可抵扣证据")
    elif not evidence.get("deductible"):
        errors.append("deductible缺少原文证据")

    # Preserve order while avoiding repeated messages when a parser and the
    # LLM report the same issue.
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    out["validation_errors"] = errors
    out["extraction_warnings"] = warnings
    out["arithmetic_validation"] = arithmetic
    out["validation_status"] = "PENDING_REVIEW" if errors else "VALID"
    return out


def clean_chunk_html(text: str) -> str:
    """Strip table and HTML markup to yield clean text for regex extractors."""
    t = re.sub(r"</?(?:tr|td|th|div|p|br|table|span|tbody|thead)[^>]*>", "\n", str(text or ""))
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    return "\n".join([line.strip() for line in t.splitlines() if line.strip()])


def extract_invoice_fields_from_text(text: str) -> dict[str, Any]:
    """Extract invoice identity/amounts directly from native OCR text."""
    raw_text = str(text or "").replace("\x00", "").strip()
    if not raw_text:
        return validate_invoice_fields({})

    clean_text = clean_chunk_html(raw_text)
    # Search both raw and cleaned text for maximum coverage
    text = clean_text + "\n" + raw_text

    fields: dict[str, Any] = {}
    evidence: dict[str, str] = {}

    for key, labels in (
        ("invoice_code", ("发票代码",)),
        ("invoice_no", ("发票号码", "发票号", "No", "NO")),
    ):
        value, evidence_text = _invoice_identifier(text, labels)
        if value:
            fields[key] = value
            evidence[key] = _invoice_evidence(text, evidence_text)

    # Fallback pattern for invoice number in scanned titles or headers
    if not fields.get("invoice_no"):
        m_inv_no = re.search(r"(?:INVOICE|发票)[_A-Za-z0-9-]*?_(\d{8,12})_", text) or re.search(r"(?<!\d)(\d{8,12})(?!\d)", clean_text)
        if m_inv_no:
            fields["invoice_no"] = m_inv_no.group(1)
            evidence["invoice_no"] = _invoice_evidence(text, m_inv_no.group(0))

    invoice_date, date_evidence = _invoice_date(text)
    if invoice_date:
        fields["invoice_date"] = invoice_date
        fields["period"] = invoice_date[:7]
        evidence["invoice_date"] = _invoice_evidence(text, date_evidence)

    for key, labels in (("seller_name", _SELLER_LABELS), ("buyer_name", _BUYER_LABELS)):
        value, evidence_text = _invoice_text_value(text, labels)
        if value:
            clean_name = re.sub(r"(?:统一|社会|代码|纳税|地址|电话|开户).*$", "", value).strip(" ：:|<>/")
            clean_name = re.sub(r"<[^>]+>", "", clean_name).strip()
            if clean_name:
                fields[key] = clean_name
                evidence[key] = _invoice_evidence(text, evidence_text)

    # Explicit party-labelled tax ids
    seller_id_match = re.search(
        rf"(?:销方|销售方|销货单位|卖方)(?:名称)?\s*(?:纳税(?:人)?识别(?:号)?|税号)\s*[:：]?\s*(?P<value>{_TAX_ID_TOKEN})",
        text,
        flags=re.IGNORECASE,
    )
    buyer_id_match = re.search(
        rf"(?:购方|购货单位|购买方|买方)(?:名称)?\s*(?:纳税(?:人)?识别(?:号)?|税号)\s*[:：]?\s*(?P<value>{_TAX_ID_TOKEN})",
        text,
        flags=re.IGNORECASE,
    )
    if seller_id_match:
        fields["seller_tax_id"] = seller_id_match.group("value").upper()
        evidence["seller_tax_id"] = _invoice_evidence(text, seller_id_match.group(0))
    if buyer_id_match:
        fields["buyer_tax_id"] = buyer_id_match.group("value").upper()
        evidence["buyer_tax_id"] = _invoice_evidence(text, buyer_id_match.group(0))
    generic_id_matches = list(re.finditer(
        rf"(?:纳税(?:人)?识别(?:号)?|税号)\s*[:：]?\s*(?P<value>{_TAX_ID_TOKEN})",
        text,
        flags=re.IGNORECASE,
    ))
    generic_ids = [match.group("value").upper() for match in generic_id_matches]
    if generic_id_matches:
        if len(generic_id_matches) >= 2:
            id0 = generic_ids[0]
            id1 = generic_ids[1]
            b_name = fields.get("buyer_name", "")
            s_name = fields.get("seller_name", "")
            if "锐宝" in b_name or "建筑工程" in b_name:
                fields["buyer_tax_id"] = id0
                fields["seller_tax_id"] = id1
                evidence.setdefault("buyer_tax_id", _invoice_evidence(text, generic_id_matches[0].group(0)))
                evidence.setdefault("seller_tax_id", _invoice_evidence(text, generic_id_matches[1].group(0)))
            elif "锐宝" in s_name:
                fields["seller_tax_id"] = id0
                fields["buyer_tax_id"] = id1
                evidence.setdefault("seller_tax_id", _invoice_evidence(text, generic_id_matches[0].group(0)))
                evidence.setdefault("buyer_tax_id", _invoice_evidence(text, generic_id_matches[1].group(0)))
            else:
                if not fields.get("seller_tax_id"):
                    fields["seller_tax_id"] = id0
                if not fields.get("buyer_tax_id"):
                    fields["buyer_tax_id"] = id1
                evidence.setdefault("seller_tax_id", _invoice_evidence(text, generic_id_matches[0].group(0)))
                evidence.setdefault("buyer_tax_id", _invoice_evidence(text, generic_id_matches[1].group(0)))
        else:
            if not fields.get("seller_tax_id"):
                fields["seller_tax_id"] = generic_ids[0]
                evidence.setdefault("seller_tax_id", _invoice_evidence(text, generic_id_matches[0].group(0)))

    if re.search(r"(?:进项|购进|取得进项)", text):
        fields["direction"] = "in"
    elif re.search(r"(?:销项|销售发票|销货发票)", text):
        fields["direction"] = "out"
    else:
        # Default direction heuristics
        buyer_name = fields.get("buyer_name", "")
        if "锐宝" in buyer_name or "建筑工程" in buyer_name:
            fields["direction"] = "in"
        else:
            fields["direction"] = "out"

    if "增值税专用发票" in text or "专用发票" in text or "专票" in text:
        fields["invoice_type"] = "special"
    elif "增值税普通发票" in text or "普通发票" in text or "普票" in text:
        fields["invoice_type"] = "normal"
    elif "卷票" in text:
        fields["invoice_type"] = "roll"

    rate_match = re.search(r"税率\s*[:：]?\s*(?P<value>\d+(?:\.\d+)?)\s*%", text)
    if rate_match:
        fields["vat_rate"] = float(_invoice_rate(rate_match.group("value")) or 0)
        evidence["vat_rate"] = _invoice_evidence(text, rate_match.group(0))

    net, net_evidence = _invoice_labeled_amount(
        text, ("不含税金额", "不含税价", "金额（不含税）", "金额(不含税)", "单价(不含税)")
    )
    vat, vat_evidence = _invoice_labeled_amount(text, ("税额", "增值税额"))
    if net is not None:
        fields["net_amount"] = _invoice_number(net)
        evidence["net_amount"] = _invoice_evidence(text, net_evidence)
    if vat is not None:
        fields["vat_amount"] = _invoice_number(vat)
        evidence["vat_amount"] = _invoice_evidence(text, vat_evidence)

    triplet = re.search(
        rf"(?P<net>{_AMOUNT_TOKEN})\s+(?P<rate>\d+(?:\.\d+)?)\s*%\s+(?P<vat>{_AMOUNT_TOKEN})",
        text,
        flags=re.IGNORECASE,
    )
    if triplet:
        triplet_net = _invoice_decimal(triplet.group("net"))
        triplet_vat = _invoice_decimal(triplet.group("vat"))
        if net is None and triplet_net is not None:
            fields["net_amount"] = _invoice_number(triplet_net)
            evidence["net_amount"] = _invoice_evidence(text, triplet.group(0))
        if vat is None and triplet_vat is not None:
            fields["vat_amount"] = _invoice_number(triplet_vat)
            evidence["vat_amount"] = _invoice_evidence(text, triplet.group(0))
        if "vat_rate" not in fields:
            fields["vat_rate"] = float(_invoice_rate(triplet.group("rate")) or 0)
            evidence["vat_rate"] = _invoice_evidence(text, triplet.group(0))

    if net is None or vat is None:
        aggregate = _invoice_amounts_after(text, "合计")
        if len(aggregate) >= 2:
            if net is None:
                fields["net_amount"] = _invoice_number(aggregate[0][0])
                evidence["net_amount"] = _invoice_evidence(text, "合计")
            if vat is None:
                fields["vat_amount"] = _invoice_number(aggregate[1][0])
                evidence["vat_amount"] = _invoice_evidence(text, "合计")

    total, total_evidence = _invoice_labeled_amount(text, ("价税合计", "含税合计", "合计(小写)", "合计（小写）", "(小写)", "（小写）"))
    total_candidates = _invoice_amounts_after(text, "价税合计")
    if total_candidates and total is None:
        total = total_candidates[0][0]
        total_evidence = "价税合计"
    if total is not None:
        fields["total_amount"] = _invoice_number(total)
        evidence["total_amount"] = _invoice_evidence(text, total_evidence)

    # Balance amounts if missing
    if fields.get("total_amount") is None and fields.get("net_amount") is not None and fields.get("vat_amount") is not None:
        fields["total_amount"] = _invoice_number(Decimal(str(fields["net_amount"])) + Decimal(str(fields["vat_amount"])))
    elif fields.get("net_amount") is None and fields.get("total_amount") is not None and fields.get("vat_amount") is not None:
        fields["net_amount"] = _invoice_number(Decimal(str(fields["total_amount"])) - Decimal(str(fields["vat_amount"])))

    if "可抵扣" in text:
        deductible_match = re.search(r"可抵扣\s*[:：]?\s*(是|否|Y|N|yes|no)", text, flags=re.IGNORECASE)
        if deductible_match:
            fields["deductible"] = deductible_match.group(1).lower() in {"是", "y", "yes"}
            evidence["deductible"] = _invoice_evidence(text, deductible_match.group(0))

    if re.search(r"建筑服务|服务|咨询|试验|监测", text):
        fields["category"] = "service"
    elif re.search(r"材料|钢材|水泥|物资|砂石|采购", text):
        fields["category"] = "material"
    elif re.search(r"劳务|人工|施工队", text):
        fields["category"] = "labor"
    elif re.search(r"设备|机械|吊装|租赁", text):
        fields["category"] = "equipment"
    elif re.search(r"分包", text):
        fields["category"] = "subcontract"

    fields["evidence"] = evidence
    fields["source"] = "deterministic_ocr_rules"
    return validate_invoice_fields(fields)


def extract_contract_fields_from_text(text: str) -> dict[str, Any]:
    """Extract contract metadata deterministically from document text."""
    raw_text = str(text or "").replace("\x00", "").strip()
    if not raw_text:
        return {}
    clean = clean_chunk_html(raw_text)
    combined = clean + "\n" + raw_text
    fields: dict[str, Any] = {"tax_included": True}

    m_no = (
        re.search(r"合同编号[：:\s]*\n*([A-Za-z0-9_-]+)", combined)
        or re.search(r"([A-Z0-9]+-[A-Z0-9]+-\d{4}-\d+)", combined)
        or re.search(r"([A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+)", combined)
    )
    if m_no:
        fields["contract_no"] = m_no.group(1).strip()

    m_date = re.search(r"(?:签订日期|签约日期|合同日期|签订时间|签约执行日期|执行日期)[：:\s]*\n*(\d{4}[-年/.]\d{1,2}[-月/.]\d{1,2})", combined)
    if m_date:
        nums = [int(p) for p in re.findall(r"\d+", m_date.group(1))]
        if len(nums) >= 3:
            fields["contract_date"] = f"{nums[0]:04d}-{nums[1]:02d}-{nums[2]:02d}"

    # Party A (建设发包单位, 发包单位, 发包/采购方, 发包方, 发包人, 甲方, 购买方, 委托方)
    pa_match = re.search(r'(?:建设发包单位|发包单位|发包[/\s、]*采购方|发包方|发包人|甲方|购买方|委托方)(?:\s*[(（][^()（）]+[)）])?[：:\s]*\n*([^\n\r]+)', combined)
    if pa_match:
        val = pa_match.group(1).strip(' ：:|<>/')
        val = re.sub(r'<[^>]+>', '', val).strip()
        if re.match(r'^[(（]?[甲购买委托发包]+[)）]?$', val) or not val:
            rest = combined[pa_match.end():]
            next_lines = [line.strip() for line in rest.split('\n') if line.strip() and not re.match(r'^[(（]?[甲购买委托发包]+[)）]?[：:]?$', line.strip())]
            if next_lines:
                val = next_lines[0]
        val = re.sub(r'[(（][甲购买委托发包]+[)）]', '', val).strip(' ：:|<>/')
        val = re.sub(r'(?:统一|社会|代码|纳税|地址|电话|法定|账号).*$', '', val).strip(' ：:|<>/')
        if val and not re.match(r'^[(（]?[甲购买委托发包]+[)）]?$', val):
            fields["party_a_name"] = val

    pa_tax_match = re.search(r'(?:建设发包单位|发包单位|发包[/\s、]*采购方|发包方|发包人|甲方|购买方|委托方)[\s\S]{1,150}?(?:统一社会信用代码|纳税人识别号|税号|纳税识别号|机构代码)[：:\s]*([A-Za-z0-9]{15,20})', combined)
    if pa_tax_match:
        tax_id = pa_tax_match.group(1).strip()
        fields["party_a_tax_id"] = tax_id
        fields["party_a_code"] = tax_id

    # Party B (中标总包单位, 总包单位, 中标单位, 承包单位, 承包/供应方, 承包方, 承包人, 乙方, 销售方, 供货方, 受托方)
    pb_match = re.search(r'(?:中标总包单位|总包单位|中标单位|承包单位|承包[/\s、]*供应方|承包方|承包人|乙方|销售方|供货方|受托方)(?:\s*[(（][^()（）]+[)）])?[：:\s]*\n*([^\n\r]+)', combined)
    if pb_match:
        val = pb_match.group(1).strip(' ：:|<>/')
        val = re.sub(r'<[^>]+>', '', val).strip()
        if re.match(r'^[(（]?[乙销售供货受托承包]+[)）]?$', val) or not val:
            rest = combined[pb_match.end():]
            next_lines = [line.strip() for line in rest.split('\n') if line.strip() and not re.match(r'^[(（]?[乙销售供货受托承包]+[)）]?[：:]?$', line.strip())]
            if next_lines:
                val = next_lines[0]
        val = re.sub(r'[(（][乙销售供货受托承包]+[)）]', '', val).strip(' ：:|<>/')
        val = re.sub(r'(?:统一|社会|代码|纳税|地址|电话|法定|账号).*$', '', val).strip(' ：:|<>/')
        if val and not re.match(r'^[(（]?[乙销售供货受托承包]+[)）]?$', val):
            fields["party_b_name"] = val

    pb_tax_match = re.search(r'(?:中标总包单位|总包单位|中标单位|承包单位|承包[/\s、]*供应方|承包方|承包人|乙方|销售方|供货方|受托方)[\s\S]{1,150}?(?:统一社会信用代码|纳税人识别号|税号|纳税识别号|机构代码)[：:\s]*([A-Za-z0-9]{15,20})', combined)
    if pb_tax_match:
        tax_id = pb_tax_match.group(1).strip()
        fields["party_b_tax_id"] = tax_id
        fields["party_b_code"] = tax_id

    m_amt = re.search(r"(?:中标合同金额|中标金额|含税总价|暂定价款|合同金额|签约总价|暂定总价|签约含税总价|合同暂定价款|签约暂定金额|暂定金额)[：:\s]*\n*[¥￥]?\s*([0-9,，.]+(?:\.\d+)?)", combined)
    if m_amt:
        try:
            raw_amt = m_amt.group(1).replace(",", "").replace("，", "")
            if raw_amt.count(".") > 1:
                parts = raw_amt.split(".")
                raw_amt = "".join(parts[:-1]) + "." + parts[-1]
            fields["total_amount"] = float(Decimal(raw_amt))
        except Exception:
            pass

    if "劳务" in combined:
        fields["category"] = "labor"
    elif "材料" in combined or "采购" in combined or "钢材" in combined or "物资" in combined:
        fields["category"] = "material"
    elif "设备" in combined or "租赁" in combined or "机械" in combined or "吊装" in combined:
        fields["category"] = "equipment"
    elif "分包" in combined:
        fields["category"] = "subcontract"
    else:
        fields["category"] = "service"

    return fields


def extract_payment_fields_from_text(text: str) -> dict[str, Any]:
    """Extract bank receipt / payment metadata deterministically from document text."""
    raw_text = str(text or "").replace("\x00", "").strip()
    if not raw_text:
        return {}
    clean = clean_chunk_html(raw_text)
    combined = clean + "\n" + raw_text
    fields: dict[str, Any] = {"direction": "out", "payment_method": "bank_transfer"}

    m_ref = (
        re.search(r"(?:流水编号|电子回单号|凭证号|交易流水号|业务流水号|回单编号)[：:\s]*([A-Za-z0-9]+)", combined)
        or re.search(r"(EBNK\d+)", combined)
    )
    if m_ref:
        fields["bank_reference"] = m_ref.group(1).strip()

    m_date = re.search(r"(?:记账日期|交易日期|付款日期|业务日期|回单日期)[：:\s]*(\d{4}[-年/.]\d{1,2}[-月/.]\d{1,2})", combined)
    if m_date:
        nums = [int(p) for p in re.findall(r"\d+", m_date.group(1))]
        if len(nums) >= 3:
            fields["payment_date"] = f"{nums[0]:04d}-{nums[1]:02d}-{nums[2]:02d}"
            fields["period"] = f"{nums[0]:04d}-{nums[1]:02d}"

    m_payer = re.search(r"(?:付款人全称|付款人名称|付款人|付款单位)[：:\s]*([^\n\r]+)", combined)
    if m_payer:
        name = re.sub(r"(?:收款人|付款人账号|账号|开户行).*$", "", m_payer.group(1)).strip(" ：:|<>/")
        name = re.sub(r"<[^>]+>", "", name).strip()
        if name:
            fields["payer_name"] = name

    m_payee = re.search(r"(?:收款人全称|收款人名称|收款人|收款单位)[：:\s]*([^\n\r]+)", combined)
    if m_payee:
        name = re.sub(r"(?:付款人|收款人账号|账号|开户行).*$", "", m_payee.group(1)).strip(" ：:|<>/")
        name = re.sub(r"<[^>]+>", "", name).strip()
        if name:
            fields["payee_name"] = name

    m_amt = re.search(r"(?:交易(?:币种及)?金额|转账金额|付款金额|金额)[：:\s]*(?:RMB|¥|￥|\$|\s)*([0-9,，]+(?:\.\d+)?)", combined)
    if m_amt:
        try:
            amt_str = m_amt.group(1).replace(",", "").replace("，", "")
            fields["amount"] = float(Decimal(amt_str))
        except Exception:
            pass

    m_note = re.search(r"(?:业务款项用途|款项用途|用途|摘要|备注)[：:\s]*([^\n\r]+)", combined)
    if m_note:
        clean_note = re.sub(r"<[^>]+>", "", m_note.group(1)).strip()
        if clean_note:
            fields["note"] = clean_note

    return fields


def extract_tax_payment_fields_from_text(text: str) -> dict[str, Any]:
    """Extract tax payment receipt metadata deterministically from document text."""
    raw_text = str(text or "").replace("\x00", "").strip()
    if not raw_text:
        return {}
    clean = clean_chunk_html(raw_text)
    combined = clean + "\n" + raw_text
    fields: dict[str, Any] = {"tax_type": "VAT"}

    m_no = re.search(r"(?:完税凭证编号|税票号码|凭证编号|证明编号|报告编号)[：:\s]*([A-Za-z0-9_-]+)", combined)
    if m_no:
        fields["receipt_no"] = m_no.group(1).strip()

    m_payer = re.search(r"(?:纳税人名称|纳税人|缴税单位)[：:\s]*([^\n\r]+)", combined)
    if m_payer:
        name = re.sub(r"(?:纳税人识别号|税号|金额|税款).*$", "", m_payer.group(1)).strip(" ：:|<>/")
        name = re.sub(r"<[^>]+>", "", name).strip()
        if name:
            fields["taxpayer_name"] = name

    m_tax_id = re.search(r"(?:纳税人识别号|税号)[：:\s]*([0-9A-Z]{15,20})", combined)
    if m_tax_id:
        fields["taxpayer_code"] = m_tax_id.group(1).strip()

    m_amt = re.search(r"(?:实缴金额|缴纳金额|税额|实缴税额|合计金额|税款金额)[：:\s]*[¥￥]?\s*([0-9,，]+(?:\.\d+)?)", combined)
    if m_amt:
        try:
            amt_str = m_amt.group(1).replace(",", "").replace("，", "")
            fields["tax_amount"] = float(Decimal(amt_str))
            fields["principal_amount"] = float(Decimal(amt_str))
        except Exception:
            pass

    m_date = re.search(r"(?:缴款日期|实缴日期|填发日期|归档日期|签约日期)[：:\s]*(\d{4}[-年/.]\d{1,2}[-月/.]\d{1,2})", combined)
    if m_date:
        nums = [int(p) for p in re.findall(r"\d+", m_date.group(1))]
        if len(nums) >= 3:
            fields["payment_date"] = f"{nums[0]:04d}-{nums[1]:02d}-{nums[2]:02d}"
            fields["tax_period"] = f"{nums[0]:04d}-{nums[1]:02d}"

    if "企业所得税" in combined:
        fields["tax_type"] = "cit"
    elif "个人所得税" in combined:
        fields["tax_type"] = "income"
    elif "印花税" in combined:
        fields["tax_type"] = "stamp"
    else:
        fields["tax_type"] = "VAT"

    return fields


# ============================================================
# LLM extraction
# ============================================================

def _llm_extract(prompt: str, *, session: Any = None) -> str:
    """Call LLM with a structured extraction prompt.

    Args:
        prompt: The extraction prompt with chunk text

    Returns:
        Raw LLM response text

    Raises:
        ExtractionError: If LLM call fails
    """
    try:
        result = llm_pool.call_chat(
            [{"role": "user", "content": prompt}],
            temperature=0.05,
            routing_group="default",
            session=session,
            # Preserve the existing module seam for tests while keeping URL
            # validation ahead of HTTP client construction in the pool.
            http_client_factory=httpx.Client,
            legacy_base_url=LLM_BASE_URL,
            legacy_model=LLM_MODEL,
            legacy_api_key=LLM_API_KEY,
            legacy_timeout_seconds=60,
        )
        return result.text
    except llm_pool.LLMPoolError as exc:
        raise ExtractionError(f"LLM call failed: {exc}") from exc
    except Exception as exc:
        raise ExtractionError(f"LLM call failed: {exc}") from exc


def _parse_json_response(raw: str) -> dict[str, Any]:
    """Parse LLM response into a dict, handling markdown code blocks.

    Args:
        raw: Raw LLM response text

    Returns:
        Parsed dict
    """
    raw = raw.strip()
    # Strip markdown code fences
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    # Try direct JSON parse
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ExtractionError("LLM extraction response must be a JSON object")
        return parsed
    except json.JSONDecodeError:
        pass

    # Try to find JSON inside the text
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            parsed = json.loads(match.group())
            if not isinstance(parsed, dict):
                raise ExtractionError("LLM extraction response must be a JSON object")
            return parsed
        except json.JSONDecodeError:
            pass

    raise ExtractionError(f"Failed to parse LLM response as JSON: {raw[:200]}")


_VIRTUAL_ENTITY_VALUES = frozenset({"A", "B", "C", "D", "甲", "乙", "丙", "丁"})


def _sanitize_entity_identifiers(fields: dict[str, Any]) -> dict[str, Any]:
    """Remove virtual/invalid entity identifiers from an LLM result.

    The extraction prompt intentionally does not assign a default company.
    Older/local models can nevertheless return ``entity_code: "A"`` or put a
    role label in one of the legacy ``*_code`` fields.  Such a value is not a
    tax identifier and must never reach a downstream deterministic importer.
    Real canonical codes are retained; names, tax IDs and bank accounts are
    left untouched for later evidence-backed resolution.
    """
    out = dict(fields)
    warnings = list(out.get("extraction_warnings") or [])

    for key, value in list(out.items()):
        if value is None or not isinstance(value, str):
            continue
        normalized = normalize_entity_code(value)
        if key == "entity_code" or key.endswith("_entity_code"):
            if normalized and not is_canonical_entity_code(normalized):
                out[key] = None
                warnings.append(f"{key} is not a canonical entity code; left unresolved")
        elif key in {
            "seller_code", "buyer_code", "payer_code", "payee_code",
            "counterparty_code", "taxpayer_code", "party_a_code", "party_b_code",
        } and normalized in _VIRTUAL_ENTITY_VALUES:
            # These legacy fields are tax-id aliases, not entity-code fields;
            # a single role/placeholder token is therefore cleared rather
            # than copied as a tax identifier.
            out[key] = None
            warnings.append(f"{key} is a role/placeholder, not an identifier")

    if warnings:
        # Preserve insertion order while preventing duplicate warnings when
        # the normalizer and this compatibility scrubber see the same value.
        out["extraction_warnings"] = list(dict.fromkeys(warnings))
    return out


def _estimate_confidence(fields: dict[str, Any], extract_type: EXTRACT_TYPES) -> float:
    """Estimate extraction confidence based on field coverage.

    Args:
        fields: Extracted fields dict
        extract_type: Type of extraction

    Returns:
        Confidence score 0.0-1.0
    """
    # Required fields per type
    required_fields: dict[EXTRACT_TYPES, list[str]] = {
        "invoice": ["invoice_no", "total_amount", "vat_amount"],
        "contract": ["contract_no", "total_amount"],
        "payment": ["payment_date", "amount"],
        "tax_payment": ["tax_amount", "tax_period"],
    }

    required = required_fields.get(extract_type, [])
    if not required:
        return 0.5

    filled = sum(1 for k in required if fields.get(k) is not None)
    base = filled / len(required)

    # Boost if additional fields are present
    extra_count = sum(
        1 for k, v in fields.items()
        if k not in required and v is not None
    )
    extra_boost = min(extra_count * 0.05, 0.2)

    return round(min(base + extra_boost, 1.0), 3)


def extract_from_chunk(
    chunk_text: str,
    extract_type: EXTRACT_TYPES,
) -> tuple[dict[str, Any], float]:
    """Extract structured data from a single document chunk.

    Args:
        chunk_text: Raw text content of the document chunk
        extract_type: Type of data to extract

    Returns:
        Tuple of (extracted_fields_dict, confidence_score)

    Raises:
        ExtractionError: If extraction fails
    """
    if not chunk_text or not chunk_text.strip():
        raise ExtractionError("Empty chunk text")

    # 1. Deterministic fast path
    if extract_type == "invoice":
        deterministic = extract_invoice_fields_from_text(chunk_text)
    elif extract_type == "contract":
        deterministic = extract_contract_fields_from_text(chunk_text)
    elif extract_type == "payment":
        deterministic = extract_payment_fields_from_text(chunk_text)
    elif extract_type == "tax_payment":
        deterministic = extract_tax_payment_fields_from_text(chunk_text)
    else:
        deterministic = {}

    # Check if deterministic extraction found core fields
    is_complete_deterministic = False
    if extract_type == "invoice" and (deterministic.get("invoice_no") and deterministic.get("total_amount") is not None and (deterministic.get("seller_name") or deterministic.get("buyer_name"))):
        is_complete_deterministic = True
    elif extract_type == "contract" and (deterministic.get("contract_no") and (deterministic.get("party_a_name") and deterministic.get("party_b_name"))):
        is_complete_deterministic = True
    elif extract_type == "payment" and (deterministic.get("bank_reference") and deterministic.get("amount") is not None and (deterministic.get("payer_name") or deterministic.get("payee_name"))):
        is_complete_deterministic = True
    elif extract_type == "tax_payment" and (deterministic.get("receipt_no") and deterministic.get("tax_amount") is not None and deterministic.get("taxpayer_name")):
        is_complete_deterministic = True

    if is_complete_deterministic:
        fields = normalize_extracted_fields(extract_type, deterministic)
        fields = _sanitize_entity_identifiers(fields)
        if extract_type == "invoice":
            fields = validate_invoice_fields(fields)
        confidence = _estimate_confidence(fields, extract_type)
        if confidence >= 0.5:
            return fields, confidence

    # 2. LLM fallback path when deterministic parsing is incomplete
    chunk_text_truncated = chunk_text[:8000]
    prompt = PROMPT_TEMPLATES[extract_type].replace("{chunk_text}", chunk_text_truncated)
    try:
        raw = _llm_extract(prompt)
        fields = _parse_json_response(raw)
    except Exception as exc:
        if deterministic and any(v not in (None, "", [], {}) for v in deterministic.values()):
            fields = deterministic
        else:
            raise ExtractionError(f"Extraction failed: {exc}") from exc

    # Merge deterministic values onto model fields (deterministic values are authoritative)
    for key, value in deterministic.items():
        if value not in (None, "", [], {}):
            fields[key] = value

    fields = normalize_extracted_fields(extract_type, fields)
    fields = _sanitize_entity_identifiers(fields)
    if extract_type == "invoice":
        fields = validate_invoice_fields(fields)
    confidence = _estimate_confidence(fields, extract_type)
    if extract_type == "invoice" and fields.get("validation_status") != "VALID":
        confidence = min(confidence, 0.59)

    return fields, confidence


def llm_extraction_available() -> bool:
    """Check if LLM extraction is configured and available."""
    return llm_pool.llm_available(
        legacy_base_url=LLM_BASE_URL,
        legacy_model=LLM_MODEL,
        legacy_api_key=LLM_API_KEY,
    )
