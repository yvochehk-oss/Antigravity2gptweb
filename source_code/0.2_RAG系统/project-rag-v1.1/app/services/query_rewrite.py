def _build_chat_url(base_url: str) -> str:
    base = (base_url or '').rstrip('/')
    if base.endswith('/v1'):
        return f'{base}/chat/completions'
    return f'{base}/v1/chat/completions'

"""Query rewriting and HyDE (Hypothetical Document Embeddings) service.

Provides query optimization via LLM and adaptive HyDE for low-quality retrieval results.
"""
from dataclasses import dataclass
import json
import httpx

from ..config import (
    ENABLE_QUERY_REWRITE,
    ENABLE_HYDE,
    REWRITE_LLM_BASE_URL,
    REWRITE_LLM_MODEL,
    REWRITE_LLM_API_KEY,
    REWRITE_MAX_TOKENS,
    REWRITE_TEMPERATURE,
    HYDE_LLM_BASE_URL,
    HYDE_LLM_MODEL,
    HYDE_LLM_API_KEY,
    HYDE_MAX_TOKENS,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_API_KEY,
)
from ..logging_config import get_logger
from ..models import is_canonical_entity_code, normalize_entity_code
from ..security import validate_llm_outbound_url

logger = get_logger(__name__)


@dataclass
class RewriteResult:
    """Result of query rewriting step.

    Attributes:
        intent: Classified query intent (合同条款/金额/资料检索/主体信息/其他)
        rewritten_query: Improved query text suitable for vector retrieval
        filters: Extracted metadata filters (document_type, business_category, etc.)
        keywords: Key search terms extracted from the query
        confidence: Confidence score [0, 1] of the rewrite quality
    """
    intent: str
    rewritten_query: str
    filters: dict
    keywords: list[str]
    confidence: float
    # A rewrite must never look successful when the optional LLM helper was
    # unavailable or returned an unsafe payload.  ``status`` and ``error``
    # are deliberately part of the result so the retrieval pipeline can
    # expose a degraded step instead of silently pretending that the original
    # query was rewritten.
    status: str = "OK"
    error: str = ""


@dataclass
class HyDEResult:
    """Result of HyDE hypothetical document generation.

    Attributes:
        hypothetical_text: Generated hypothetical document text
        helper_used: Whether the HyDE helper was actually invoked
    """
    hypothetical_text: str
    helper_used: bool
    status: str = "OK"
    error: str = ""


_REWRITE_SYSTEM_PROMPT = """你是一个建筑工程项目知识库查询优化助手。请分析用户问题，输出 JSON：
{
  "intent": "查询意图分类（合同条款/金额/资料检索/主体信息/其他）",
  "rewritten_query": "改写后的查询（补全实体/期间/类别等关键信息，更适合向量检索）",
  "filters": {"document_type": "...", "business_category": "...", "entity_code": "...", "period": "..."},
  "keywords": ["关键词1", "关键词2", "..."]
}
仅输出 JSON，不要 markdown 包裹，不要其他解释。"""

_HYDE_SYSTEM_PROMPT = """你是一个建筑工程项目资料检索助手。用户提问后，先没有检索到充分资料，请你根据提问和（可选）少量已有片段，**生成一段假设性的工程资料片段**（约 150-300 字），用专业工程语言描述。如果提问太模糊，生成最可能的场景描述。
输出仅这段文本，不要其他。"""


def _call_llm(
    base_url: str,
    model: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
) -> str | None:
    """Call an OpenAI-compatible LLM endpoint.

    Args:
        base_url: LLM base URL (e.g. "https://api.openai.com/v1")
        model: Model name
        api_key: API key for authentication
        system_prompt: System prompt content
        user_prompt: User prompt content
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature

    Returns:
        Response text content, or None on any error (timeout, HTTP error, etc.)
    """
    if not base_url or not model:
        logger.warning("LLM call skipped: base_url or model not configured")
        return None

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        # Validate the final chat-completions URL before opening or using the
        # HTTP client.  Both Rewrite and HyDE (including their dedicated URL
        # overrides and the shared LLM fallback) reach this single boundary.
        endpoint = validate_llm_outbound_url(_build_chat_url(base_url))
        with httpx.Client(timeout=30) as client:
            response = client.post(
                endpoint,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        return content.strip()

    except httpx.TimeoutException:
        logger.warning("LLM call timed out after 30s")
        return None
    except httpx.HTTPStatusError as e:
        logger.warning(f"LLM HTTP error {e.response.status_code}: {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.warning(f"LLM call failed: {e}")
        return None


def rewrite_query(
    query: str,
    filters: dict,
    project_meta: dict = None,
) -> RewriteResult:
    """Rewrite a user query for improved retrieval.

    Uses an LLM to classify intent, expand entities/periods, and extract
    structured filters and keywords.

    Args:
        query: Original user query
        filters: Current active filters (document_type, business_category, etc.)
        project_meta: Optional project metadata dict for context injection

    Returns:
        RewriteResult with intent, rewritten_query, filters, keywords, confidence.
        Falls back to original query if LLM is unavailable or parsing fails.
    """
    if not ENABLE_QUERY_REWRITE:
        return _fallback_result(query, status="DISABLED")

    # Build user prompt with context
    filters_str = json.dumps(filters, ensure_ascii=False)
    meta_str = ""
    if project_meta:
        meta_parts = [
            f"{k}: {v}" for k, v in project_meta.items() if v
        ]
        if meta_parts:
            meta_str = "\n项目背景：" + "\n".join(meta_parts)

    user_prompt = f"""当前查询：{query}
当前过滤条件：{filters_str}{meta_str}
请优化上述查询。"""

    text = _call_llm(
        base_url=REWRITE_LLM_BASE_URL or LLM_BASE_URL,
        model=REWRITE_LLM_MODEL or LLM_MODEL,
        api_key=REWRITE_LLM_API_KEY or LLM_API_KEY,
        system_prompt=_REWRITE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=REWRITE_MAX_TOKENS,
        temperature=REWRITE_TEMPERATURE,
    )

    if text is None:
        return _fallback_result(
            query,
            status="UNAVAILABLE",
            error="rewrite LLM returned no usable response",
        )

    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise TypeError("rewrite response must be a JSON object")

        # The active request filters are authoritative.  The model may add
        # useful filters, but it must not replace an explicit caller filter.
        # In particular, a hallucinated single-letter A/B/C/D value must not
        # become an entity_code filter.
        parsed_filters = _merge_filters(filters, parsed.get("filters", {}))
        unsafe_filter = _find_unsafe_entity_filter(parsed.get("filters", {}))
        if unsafe_filter:
            return RewriteResult(
                intent=str(parsed.get("intent", "其他")),
                rewritten_query=str(parsed.get("rewritten_query", query)) or query,
                filters=parsed_filters,
                keywords=_normalise_keywords(parsed.get("keywords", [])),
                confidence=0.0,
                status="INVALID_FILTER",
                error=f"rewrite returned non-canonical entity_code {unsafe_filter!r}",
            )

        rewritten_query = str(parsed.get("rewritten_query", query)).strip() or query
        return RewriteResult(
            intent=str(parsed.get("intent", "其他")),
            rewritten_query=rewritten_query,
            filters=parsed_filters,
            keywords=_normalise_keywords(parsed.get("keywords", [])),
            confidence=0.8,
            status="OK",
        )
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning(f"Failed to parse rewrite JSON: {e} | raw: {text[:200]}")
        return _fallback_result(
            query,
            status="INVALID_RESPONSE",
            error=f"invalid rewrite response: {e}",
        )


def _normalise_keywords(raw: object) -> list[str]:
    """Return a bounded, string-only keyword list from an LLM payload."""
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()][:32]


def _find_unsafe_entity_filter(raw_filters: object) -> str | None:
    """Find an unsafe entity filter value emitted by the rewrite model."""
    if not isinstance(raw_filters, dict) or "entity_code" not in raw_filters:
        return None
    raw = raw_filters.get("entity_code")
    values = raw if isinstance(raw, list) else [raw]
    for value in values:
        code = normalize_entity_code(value)
        if code is None:
            continue
        if not is_canonical_entity_code(code):
            return str(value)
    return None


def _merge_filters(active: dict | None, proposed: object) -> dict:
    """Merge model filters without weakening explicit caller constraints."""
    merged = dict(active or {})
    if not isinstance(proposed, dict):
        return merged
    for key, value in proposed.items():
        if value is None or value == "" or value == []:
            continue
        if key == "entity_code":
            values = value if isinstance(value, list) else [value]
            # Keep unsafe model output out of the actual retrieval filters;
            # the caller still receives INVALID_FILTER via the result status.
            safe_values = [
                normalize_entity_code(item)
                for item in values
                if normalize_entity_code(item)
                and is_canonical_entity_code(normalize_entity_code(item))
            ]
            if not safe_values:
                continue
            value = safe_values if isinstance(value, list) else safe_values[0]
        if key not in merged or merged[key] in (None, "", []):
            merged[key] = value
    return merged


def _fallback_result(
    query: str,
    *,
    status: str = "UNAVAILABLE",
    error: str = "",
) -> RewriteResult:
    """Create an explicit, non-rewritten result using the original query."""
    return RewriteResult(
        intent="其他",
        rewritten_query=query,
        filters={},
        keywords=[],
        confidence=0.0,
        status=status,
        error=error,
    )


def hyde_generate(
    query: str,
    context_chunks: list[dict] = None,
    max_hints: int = 3,
) -> HyDEResult:
    """Generate a hypothetical document for HyDE retrieval.

    When retrieval quality is low, generates a plausible document snippet
    using the query and optional hints from existing (partial) chunks.

    Args:
        query: Original user query
        context_chunks: Optional list of partial chunk dicts with
            heading_path/filename to use as hints
        max_hints: Maximum number of hints to include (default 3)

    Returns:
        HyDEResult with hypothetical_text and helper_used flag.
        Falls back to returning original query if LLM is unavailable.
    """
    if not ENABLE_HYDE:
        return HyDEResult(
            hypothetical_text=query,
            helper_used=False,
            status="DISABLED",
        )

    chunks = context_chunks or []
    hint_lines = []
    for chunk in chunks[:max_hints]:
        heading = chunk.get("heading_path", "") or chunk.get("filename", "")
        if heading:
            hint_lines.append(f"- {heading}")

    user_prompt = f"用户提问：{query}"
    if hint_lines:
        user_prompt += (
            "\n\n已有部分资料标题（作为参考）：\n" + "\n".join(hint_lines)
            + "\n\n请根据上述提问和标题参考，生成一段假设性工程资料片段："
        )
    else:
        user_prompt += "\n\n请生成一段假设性工程资料片段："

    text = _call_llm(
        base_url=HYDE_LLM_BASE_URL or LLM_BASE_URL,
        model=HYDE_LLM_MODEL or LLM_MODEL,
        api_key=HYDE_LLM_API_KEY or LLM_API_KEY,
        system_prompt=_HYDE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=HYDE_MAX_TOKENS,
        temperature=0.7,
    )

    if text is None:
        return HyDEResult(
            hypothetical_text=query,
            helper_used=False,
            status="UNAVAILABLE",
            error="HyDE LLM returned no usable response",
        )

    return HyDEResult(hypothetical_text=text, helper_used=True, status="OK")
