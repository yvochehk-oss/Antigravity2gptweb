"""LLM answer service with OpenAI-compatible API."""
from dataclasses import dataclass
import re
import httpx
from ..config import LLM_BASE_URL, LLM_MODEL, LLM_API_KEY
from ..logging_config import get_logger

logger = get_logger(__name__)


class LLMError(Exception):
    """Raised when LLM call fails."""
    pass


def answer_with_llm(query: str, evidence: list[dict]) -> str:
    """Generate answer from LLM using retrieved evidence.

    Args:
        query: User query
        evidence: List of retrieved document chunks

    Returns:
        Generated answer text

    Raises:
        LLMError: If LLM call fails
    """
    if not LLM_BASE_URL or not LLM_MODEL:
        return "未配置RAG回答模型；请使用 /api/v1/retrieve 获取证据，或配置 RAG_LLM_BASE_URL / RAG_LLM_MODEL。"

    # Build evidence text
    evidence_text = "\n\n".join([
        f"[{i+1}] {x['filename']} P{x.get('page_start') or '?'} {x.get('heading_path') or ''}\n{x['text'][:3000]}"
        for i, x in enumerate(evidence)
    ])

    prompt = f"""请仅依据提供的项目资料证据回答问题。没有证据时明确说资料不足。引用格式使用[1][2]。

问题：{query}

证据：
{evidence_text}"""

    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    try:
        with httpx.Client(timeout=90) as client:
            response = client.post(
                f"{LLM_BASE_URL}/v1/chat/completions",
                headers=headers,
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1
                }
            )
            response.raise_for_status()
            data = response.json()

        answer = data["choices"][0]["message"]["content"]
        logger.info(f"Generated answer ({len(answer)} chars) for query: {query[:50]}...")

        return answer

    except httpx.TimeoutException:
        logger.error("LLM request timed out")
        raise LLMError("LLM request timed out (90s)")

    except httpx.HTTPStatusError as e:
        logger.error(f"LLM HTTP error: {e.response.status_code} - {e.response.text[:500]}")
        raise LLMError(f"LLM returned error: {e.response.status_code}")

    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        raise LLMError(f"LLM call failed: {e}")


def llm_available() -> bool:
    """Check if LLM is configured and available.

    Returns:
        True if LLM is configured
    """
    return bool(LLM_BASE_URL and LLM_MODEL)


def test_llm_connection() -> dict:
    """Test LLM connection with a simple request.

    Returns:
        Dict with test result
    """
    if not llm_available():
        return {"ok": False, "error": "LLM not configured"}

    try:
        headers = {"Content-Type": "application/json"}
        if LLM_API_KEY:
            headers["Authorization"] = f"Bearer {LLM_API_KEY}"

        with httpx.Client(timeout=10) as client:
            response = client.post(
                f"{LLM_BASE_URL}/v1/models",
                headers=headers
            )
            response.raise_for_status()

        return {"ok": True, "models": response.json()}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Citation Grounding Answer (V0.3)
# ---------------------------------------------------------------------------

@dataclass
class AnswerResult:
    """Result of a grounded, citation-aware answer.

    Attributes:
        answer: Generated answer text.
        citations_used: List of evidence indices (1-based) cited in the answer.
        faithful: True when the answer contains at least one citation marker.
        no_answer_confidence: Confidence that the answer expresses "no answer found".
    """
    answer: str
    citations_used: list[int]
    faithful: bool
    no_answer_confidence: float


def _estimate_tokens(text: str) -> int:
    """Estimate token count for a text.

    CJK characters are counted 1:1; all other characters are counted 4:1.
    This mirrors the heuristic used in :mod:`chunker`.

    Args:
        text: Input text.

    Returns:
        Estimated token count.
    """
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return cjk + max(0, len(text) - cjk) // 4


def build_evidence_text(
    evidence: list[dict],
    max_tokens: int = 8192,
) -> tuple[str, int]:
    """Format a list of evidence dicts into a single prompt string.

    Each evidence entry is rendered as::

        [1] {filename} · P{page_start}-{page_end} · {title_chain or heading_path}
            {text}

    Evidence is appended in order until the cumulative token estimate exceeds
    ``max_tokens``, at which point no more entries are added.

    Args:
        evidence: List of evidence dicts. Expected keys are:
            ``filename``, ``page_start``, ``page_end``, ``heading_path``,
            ``title_chain``, ``text``, ``metadata``.
        max_tokens: Maximum cumulative tokens to include.

    Returns:
        A 2-tuple ``(text, used_count)`` where ``used_count`` is the number
        of evidence entries that were actually appended.
    """
    parts: list[str] = []
    used = 0
    current_tokens = 0

    for i, ev in enumerate(evidence):
        filename = ev.get("filename") or ""
        page_start = ev.get("page_start")
        page_end = ev.get("page_end")
        heading_path = ev.get("heading_path") or ""
        title_chain = ev.get("title_chain") or ""
        body_text = ev.get("text") or ""

        page_label = ""
        if page_start is not None and page_end is not None:
            page_label = f"P{page_start}-{page_end}"
        elif page_start is not None:
            page_label = f"P{page_start}"

        title = title_chain or heading_path

        chunk_lines = [
            f"[{i+1}] {filename} · {page_label} · {title}",
            f"    {body_text}",
            "",
        ]
        chunk_str = "\n".join(chunk_lines)
        chunk_tokens = _estimate_tokens(chunk_str)

        if current_tokens + chunk_tokens > max_tokens:
            break

        parts.append(chunk_str)
        current_tokens += chunk_tokens
        used += 1

    return "\n".join(parts), used


def _call_llm_chat(
    system: str,
    user: str,
    max_tokens: int = 1024,
) -> str | None:
    """Make a chat-completion call to the configured LLM endpoint.

    This is a standalone helper independent of the existing
    :func:`answer_with_llm`; it uses ``system`` + ``user`` message roles.

    Args:
        system: System-prompt text.
        user: User-prompt text.
        max_tokens: Maximum tokens to generate.

    Returns:
        Response text on success, or ``None`` on any error.
    """
    if not LLM_BASE_URL or not LLM_MODEL:
        logger.warning("LLM call skipped: base_url or model not configured")
        return None

    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }

    try:
        with httpx.Client(timeout=90) as client:
            response = client.post(
                f"{LLM_BASE_URL.rstrip('/')}/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        return content.strip()

    except httpx.TimeoutException:
        logger.warning("LLM chat call timed out after 90s")
        return None
    except httpx.HTTPStatusError as e:
        logger.warning(
            "LLM chat HTTP error %s: %s",
            e.response.status_code,
            e.response.text[:200],
        )
        return None
    except Exception as e:
        logger.warning("LLM chat call failed: %s", e)
        return None


def answer_with_evidence(
    query: str,
    evidence: list[dict],
    max_tokens: int = 8192,
) -> AnswerResult:
    """Generate a citation-grounded answer from retrieved evidence.

    The answer is produced by an LLM that is instructed to:
    - Cite every conclusion with an evidence number ``[1]``, ``[2]``, … or
      a ``【filename · P页 · 标题】`` marker.
    - Say "未在已上传资料中找到..." when the evidence does not support
      a direct answer.
    - Never fabricate dates, amounts, or clause numbers.

    Args:
        query: The user's question.
        evidence: List of retrieved evidence dicts (same format as
            ``build_evidence_text`` expects).
        max_tokens: Maximum tokens to pack into the evidence text.

    Returns:
        AnswerResult with the generated answer, citation list,
        faithfulness flag, and no-answer confidence score.
        On LLM failure returns a fallback result with empty citations.
    """
    evidence_text, used_count = build_evidence_text(evidence, max_tokens)

    if not evidence_text:
        return AnswerResult(
            answer="未在已上传资料中找到相关内容。",
            citations_used=[],
            faithful=False,
            no_answer_confidence=0.95,
        )

    system_prompt = (
        "你是一个严谨的建筑工程资料助手。请严格依据下方提供的证据回答问题。\n"
        "- 每个结论必须标注来源，格式：【文件名 · P页码 · 标题路径】或 [1][2] 这种引用编号\n"
        "- 如果证据不足以回答，请明确说明\"未在已上传资料中找到...\"，不要编造\n"
        "- 不要凭空捏造日期、金额、条款号\n"
        "- 引用的证据编号应在最终答复中显式出现（如 [1] 或 【文件名 P页】）"
    )

    user_prompt = (
        f"问题：{query}\n\n"
        f"证据：\n{evidence_text}"
    )

    raw = _call_llm_chat(system_prompt, user_prompt, max_tokens=1024)

    if raw is None:
        logger.warning("LLM answer_with_evidence call failed, returning fallback")
        return AnswerResult(
            answer="LLM 调用失败",
            citations_used=[],
            faithful=False,
            no_answer_confidence=0.0,
        )

    answer_text = raw

    # Detect citation markers: [1] [2] ... and 【…】 patterns
    numeric_refs = re.findall(r"\[(\d+)\]", answer_text)
    bracket_refs = re.findall(r"【([^】]+)】", answer_text)

    cited_indices: set[int] = set()
    for m in numeric_refs:
        try:
            cited_indices.add(int(m))
        except ValueError:
            pass

    # If any 【…】 marker appears, treat it as faithful (source is named)
    bracket_cited = bool(bracket_refs)
    faithful = bool(cited_indices) or bracket_cited

    # Restrict citations_used to indices that were actually packed
    citations_used = sorted(i for i in cited_indices if 1 <= i <= used_count)

    # no_answer_confidence: high when the answer explicitly says "not found"
    no_answer_keywords = re.compile(
        r"未在|资料不足|无法回答|未在|没有找到|暂无|证据不足",
        re.IGNORECASE,
    )
    if no_answer_keywords.search(answer_text):
        no_answer_confidence = 0.9
    else:
        no_answer_confidence = 0.05

    logger.info(
        "answer_with_evidence: used=%d citations=%s faithful=%s no_answer_conf=%.2f query=%s...",
        used_count,
        citations_used,
        faithful,
        no_answer_confidence,
        query[:40],
    )

    return AnswerResult(
        answer=answer_text,
        citations_used=citations_used,
        faithful=faithful,
        no_answer_confidence=no_answer_confidence,
    )
