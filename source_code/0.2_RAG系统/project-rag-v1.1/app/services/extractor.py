def _build_chat_url(base_url: str) -> str:
    base = (base_url or '').rstrip('/')
    if base.endswith('/v1'):
        return f'{base}/chat/completions'
    return f'{base}/v1/chat/completions'

"""AI-powered extraction of structured tax data from document chunks.

Uses an LLM to parse unstructured document text and extract structured
fields (invoices, contracts, payments, tax receipts) with confidence scores.
"""
import json
import re
import httpx
from typing import Any

from ..config import LLM_BASE_URL, LLM_MODEL, LLM_API_KEY
from ..logging_config import get_logger
from ..security import validate_llm_outbound_url
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
  "invoice_date": string|null,
  "period": string|null,
  "direction": "in"|"out"|"",
  "invoice_type": "special"|"normal"|"roll"|"",
  "seller_name": string|null,
  "seller_code": string|null,
  "buyer_name": string|null,
  "buyer_code": string|null,
  "total_amount": number|null,
  "net_amount": number|null,
  "vat_amount": number|null,
  "vat_rate": number|null,
  "deductible": boolean|null,
  "category": string|null,
  "note": string|null
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
# LLM extraction
# ============================================================

def _llm_extract(prompt: str) -> str:
    """Call LLM with a structured extraction prompt.

    Args:
        prompt: The extraction prompt with chunk text

    Returns:
        Raw LLM response text

    Raises:
        ExtractionError: If LLM call fails
    """
    if not LLM_BASE_URL or not LLM_MODEL:
        raise ExtractionError("LLM not configured (RAG_LLM_BASE_URL / RAG_LLM_MODEL)")

    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    try:
        # Validate the fully-resolved endpoint before constructing a client or
        # issuing any network request.  LLMs may intentionally run on
        # loopback/RFC1918 (Ollama, LM Studio, vLLM); the dedicated helper
        # keeps that exception separate from the generic SSRF boundary.
        endpoint = validate_llm_outbound_url(_build_chat_url(LLM_BASE_URL))
        with httpx.Client(timeout=60) as client:
            response = client.post(
                endpoint,
                headers=headers,
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.05,
                }
            )
            response.raise_for_status()
            data = response.json()

        return data["choices"][0]["message"]["content"].strip()

    except httpx.TimeoutException:
        raise ExtractionError("LLM request timed out (60s)")

    except httpx.HTTPStatusError as e:
        raise ExtractionError(f"LLM HTTP error: {e.response.status_code}")

    except Exception as e:
        raise ExtractionError(f"LLM call failed: {e}")


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
    REQUIRED: dict[EXTRACT_TYPES, list[str]] = {
        "invoice": ["invoice_no", "total_amount", "vat_amount"],
        "contract": ["contract_no", "total_amount"],
        "payment": ["payment_date", "amount"],
        "tax_payment": ["tax_amount", "tax_period"],
    }

    required = REQUIRED.get(extract_type, [])
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

    # Truncate very long chunks (LLM context limit)
    chunk_text = chunk_text[:8000]

    # The templates contain a literal JSON object/schema.  ``str.format``
    # would interpret those schema braces as replacement fields and fail
    # before the LLM is called; replace only the dedicated chunk marker.
    prompt = PROMPT_TEMPLATES[extract_type].replace("{chunk_text}", chunk_text)
    raw = _llm_extract(prompt)

    fields = _parse_json_response(raw)

    # Keep the LLM's legacy flat fields for API compatibility, while always
    # adding explicit party identity/tax-id/account evidence.  In particular,
    # unresolved parties stay empty; there is no fallback to company A (or to
    # any other role label).
    fields = normalize_extracted_fields(extract_type, fields)
    fields = _sanitize_entity_identifiers(fields)
    confidence = _estimate_confidence(fields, extract_type)

    logger.debug(
        f"Extracted {extract_type} with confidence={confidence} from chunk "
        f"({len(chunk_text)} chars)"
    )

    return fields, confidence


def llm_extraction_available() -> bool:
    """Check if LLM extraction is configured and available."""
    return bool(LLM_BASE_URL and LLM_MODEL)
