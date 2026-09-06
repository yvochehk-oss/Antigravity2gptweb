"""V1.0: 模型端点适配（mock / openai_compatible / openrouter）。"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Literal

import httpx

from ..constants import RISK_ORDER
from ..models import AIModelEndpoint
from ..security import resolve_secret_env_name, validate_ai_endpoint_url
from ..services.secret_store import SecretStoreError, default_secret_store
from .mock import mock_review

# Mock is a test double, never a production fallback.  Requiring both guards
# is deliberate: a test process must opt in explicitly, and an accidentally
# inherited opt-in cannot enable mock execution in development/production.
MOCK_OPT_IN_ENV = "AI_ALLOW_MOCK_ENDPOINTS"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class AIEndpointUnavailable(RuntimeError):
    """A model endpoint cannot produce a trustworthy AI result."""

    status = "UNAVAILABLE"
    requires_manual_review = True

    def __init__(
        self,
        message: str,
        *,
        status: str = "UNAVAILABLE",
        attempts: list[dict[str, Any]] | None = None,
        endpoint: AIModelEndpoint | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.data_gaps = [message]
        # These fields are deliberately limited to safe routing metadata.  A
        # caller may expose them in a degraded envelope, but never a secret,
        # Authorization header, or complete provider response.
        self.attempts = list(attempts or [])
        self.endpoint_id = getattr(endpoint, "id", None) if endpoint else None
        self.endpoint_name = getattr(endpoint, "name", "") if endpoint else ""
        self.model_name = getattr(endpoint, "model", "") if endpoint else ""


class AIEndpointTimeout(RuntimeError):
    """A model endpoint exceeded the caller's total request budget."""

    status = "DEGRADED"
    requires_manual_review = True


class AIResponseContractError(RuntimeError):
    """The provider responded, but not with the contract Tax can trust."""

    status = "DEGRADED"
    requires_manual_review = True


def mock_endpoint_allowed() -> bool:
    """Return whether the process explicitly opted into test-only mock AI."""
    environment = os.getenv("APP_ENV", "development").strip().lower()
    opt_in = os.getenv(MOCK_OPT_IN_ENV, "").strip().lower()
    return environment == "test" and opt_in in _TRUE_VALUES


def is_mock_endpoint(endpoint: AIModelEndpoint | None) -> bool:
    """Recognize mock adapters without allowing casing/whitespace bypasses."""
    return endpoint is not None and str(endpoint.adapter or "").strip().lower() == "mock"


def endpoint_is_allowed(endpoint: AIModelEndpoint | None) -> bool:
    """Return whether an endpoint may be shown or invoked in this process."""
    if endpoint is None or endpoint.enabled is False:
        return False
    return not is_mock_endpoint(endpoint) or mock_endpoint_allowed()


def ensure_endpoint_allowed(endpoint: AIModelEndpoint | None) -> None:
    """Enforce the same endpoint policy for every AI consumer."""
    if endpoint is None:
        raise AIEndpointUnavailable("未配置可用的 AI 端点")
    if endpoint.enabled is False:
        raise AIEndpointUnavailable("AI 端点已禁用")
    if is_mock_endpoint(endpoint) and not mock_endpoint_allowed():
        raise AIEndpointUnavailable(
            "mock AI 端点仅允许在 APP_ENV=test 且 "
            f"{MOCK_OPT_IN_ENV}=1 时使用"
        )


# Severity levels allowed for findings.  HARD-CODED FALLBACK VALUES ARE FORBIDDEN.
# When the AI model fails to return structured JSON, the system must NOT
# silently default to a risk level - it must mark the severity as UNKNOWN
# and flag the finding as requiring manual review.
UNKNOWN_RISK_LEVEL = "UNKNOWN"
ALLOWED_RISK_LEVELS = frozenset(RISK_ORDER)
ALLOWED_SEVERITIES = ALLOWED_RISK_LEVELS - {UNKNOWN_RISK_LEVEL}
PARSE_ERROR_SENTINEL_SEVERITY = UNKNOWN_RISK_LEVEL


def _normalize_severity(value: str | None, default_on_error: bool = False) -> str:
    """Normalize a severity string to an allowed value.

    Args:
        value: The severity string from AI response
        default_on_error: If True, return UNKNOWN; if False (strict mode), raise

    Returns:
        An allowed severity value

    Raises:
        ValueError: If value is not in ALLOWED_SEVERITIES and default_on_error is False
    """
    normalized = (value or "").strip().upper()
    if normalized in ALLOWED_SEVERITIES:
        return normalized
    if default_on_error:
        return PARSE_ERROR_SENTINEL_SEVERITY
    raise ValueError(
        f"Invalid severity '{value}'; must be one of {sorted(ALLOWED_SEVERITIES)}. "
        "Unrecognized severity in production indicates model output mismatch."
    )


def _normalize_risk_level(value: Any) -> str:
    """Return only a canonical risk level from an AI response.

    ``risk_level`` is model output, not a deterministic tax fact.  Missing,
    malformed, or unsupported values therefore cannot be guessed from a
    default severity; they are explicitly downgraded to ``UNKNOWN`` so the
    caller can keep the result visible for manual review.
    """
    normalized = value.strip().upper() if isinstance(value, str) else ""
    return normalized if normalized in ALLOWED_RISK_LEVELS else UNKNOWN_RISK_LEVEL


def _normalize_result(parsed: Any) -> tuple[dict[str, Any], bool]:
    """Normalize an adapter result and report semantic contract failures.

    The boolean is intentionally included in the existing ``parse_failed``
    return slot.  A JSON object with an invalid risk level is parseable text,
    but it still violates the response contract and must be surfaced to the
    review job rather than silently persisted as a made-up severity.
    """
    if not isinstance(parsed, dict):
        return _parse_json(""), True

    result = dict(parsed)
    raw_risk_level = result.get("risk_level")
    normalized_risk_level = _normalize_risk_level(raw_risk_level)
    raw_normalized = (
        raw_risk_level.strip().upper()
        if isinstance(raw_risk_level, str)
        else ""
    )
    contract_failed = raw_normalized not in ALLOWED_RISK_LEVELS
    result["risk_level"] = normalized_risk_level

    # 分数范围校验：截断到 0-100
    raw_score = result.get("score")
    if raw_score is not None:
        try:
            score_val = float(raw_score)
            if score_val < 0:
                result["score"] = 0
                _append_data_gap(result, "分数 < 0，已截断为 0")
            elif score_val > 100:
                result["score"] = 100
                _append_data_gap(result, "分数 > 100，已截断为 100")
        except (TypeError, ValueError):
            result["score"] = 0
            _append_data_gap(result, "分数非数字，已重置为 0")

    # UNKNOWN is an explicit review boundary.  Keep the original response,
    # but expose why it cannot be treated as an approved AI conclusion.
    if normalized_risk_level == UNKNOWN_RISK_LEVEL:
        gaps = result.get("data_gaps")
        gaps = list(gaps) if isinstance(gaps, list) else []
        reason = "模型未返回可用风险等级，需人工复核"
        if reason not in gaps:
            gaps.append(reason)
        result["data_gaps"] = gaps
        result["needs_review"] = True

    return result, contract_failed


def _append_data_gap(result: dict[str, Any], message: str) -> None:
    """向 data_gaps 添加一条记录。"""
    gaps = result.get("data_gaps")
    gaps = list(gaps) if isinstance(gaps, list) else []
    if message not in gaps:
        gaps.append(message)
    result["data_gaps"] = gaps


def _parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    # Parsing failure must never manufacture a severity.
    # The parse_error_sentinel severity marks this finding as requiring human review.
    return {
        "risk_level": UNKNOWN_RISK_LEVEL,
        "score": 0,
        "summary": "模型返回内容不是有效JSON，已保留原始响应供人工查看。",
        "findings": [{
            # Use the explicit sentinel severity for manual review.
            "severity": PARSE_ERROR_SENTINEL_SEVERITY,
            "area": "AI响应",
            "issue": "非结构化返回",
            "evidence": "模型未按约定返回JSON",
            "impact": "自动解析失败，需人工复核",
            "requires_manual_review": True,
        }],
        "recommendations": [{
            "priority": "P1",
            "action": "检查模型提示词配置或更换兼容模型",
            "reason": "需要结构化结果，当前输出无法自动解析",
            "owner": "系统管理员",
        }],
        "data_gaps": [],
    }


def call_endpoint(
    endpoint: AIModelEndpoint,
    messages: list[dict],
    ctx: dict,
    *,
    deadline: float | None = None,
    _response_mode: Literal["structured", "text"] = "structured",
) -> tuple[dict, str, bool]:
    """返回 (parsed_result, raw_text, parse_failed)。

    ``deadline`` is an absolute ``time.monotonic`` deadline.  It is used by
    bounded background health checks so retries and backoff cannot extend a
    model call indefinitely.  Ordinary AI calls preserve the endpoint's
    configured timeout when no deadline is supplied.
    """
    if _response_mode not in ("structured", "text"):
        raise ValueError(f"不支持的 AI 响应模式: {_response_mode}")
    ensure_endpoint_allowed(endpoint)
    if is_mock_endpoint(endpoint):
        result = mock_review(ctx, endpoint.name)
        normalized, contract_failed = _normalize_result(result)
        return normalized, json.dumps(normalized, ensure_ascii=False), contract_failed

    if endpoint.adapter not in ("openai_compatible", "openrouter"):
        raise ValueError(f"暂不支持适配器: {endpoint.adapter}")

    key = _resolve_endpoint_key(endpoint)

    if not endpoint.base_url:
        raise RuntimeError("未配置 base_url")

    try:
        base_url = validate_ai_endpoint_url(endpoint.base_url, endpoint.chat_path)
    except ValueError as exc:
        raise RuntimeError(f"AI endpoint rejected by SSRF policy: {exc}") from exc
    url = base_url + "/" + endpoint.chat_path.lstrip("/")
    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/projectrag",
        "X-Title": "Chengdu Construction Tax System",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {
        "model": endpoint.model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 8192,
    }

    max_retries = 3
    last_err = None
    data = None

    def _remaining() -> float | None:
        if deadline is None:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AIEndpointTimeout("AI 端点超过本次体检总 deadline")
        return remaining

    configured_timeout = max(float(endpoint.timeout_seconds or 1), 0.1)
    initial_remaining = _remaining()
    request_timeout = min(configured_timeout, initial_remaining) if initial_remaining is not None else configured_timeout

    with httpx.Client(timeout=request_timeout, follow_redirects=False) as client:
        for attempt in range(max_retries):
            remaining = _remaining()
            if remaining is not None:
                # A client may take longer than the remaining budget while a
                # retry loop is running; refresh the per-request timeout each
                # time rather than reusing the original endpoint timeout.
                request_timeout = min(configured_timeout, remaining)
            try:
                resp = client.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=request_timeout,
                )
            except httpx.TimeoutException as exc:
                last_err = exc
                if attempt >= max_retries - 1:
                    if deadline is not None:
                        raise AIEndpointTimeout(
                            "AI 端点请求超出总 deadline",
                        ) from exc
                    raise
                remaining = _remaining()
                if remaining is not None:
                    delay = min(2 * (attempt + 1), remaining)
                    if delay <= 0:
                        raise AIEndpointTimeout(
                            "AI 端点重试前已超出总 deadline",
                        ) from exc
                    time.sleep(delay)
                else:
                    time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code == 429 and attempt < max_retries - 1:
                # 429 速率限制退避重试
                remaining = _remaining()
                delay = 3 * (attempt + 1)
                if remaining is not None:
                    delay = min(delay, remaining)
                if delay <= 0:
                    raise AIEndpointTimeout("AI 端点重试前已超出总 deadline")
                time.sleep(delay)
                continue
            try:
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                last_err = e
                if attempt < max_retries - 1 and resp.status_code in (500, 502, 503, 504):
                    remaining = _remaining()
                    delay = 2.0
                    if remaining is not None:
                        delay = min(delay, remaining)
                    if delay <= 0:
                        raise AIEndpointTimeout("AI 端点重试前已超出总 deadline") from e
                    time.sleep(delay)
                    continue
                raise last_err from None

    if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or not data["choices"]:
        raise AIResponseContractError("模型端点未返回有效 choices")

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIResponseContractError("模型端点消息结构无效") from exc
    if not isinstance(text, str):
        raise AIResponseContractError("模型端点 content 不是文本")

    # The manager's natural-language path intentionally bypasses the Review
    # JSON parser.  Keep this private switch behind the dedicated
    # ``call_text_endpoint`` wrapper below; all existing callers retain the
    # strict structured contract by default.
    if _response_mode == "text":
        return {}, text, False

    # 解析 JSON
    try:
        parsed = json.loads(text)
        normalized, contract_failed = _normalize_result(parsed)
        return normalized, text, contract_failed
    except Exception:
        m = re.search(r"\{.*\}", text or "", re.S)
        parse_failed = True
        if m:
            try:
                parsed = json.loads(m.group(0))
                normalized, contract_failed = _normalize_result(parsed)
                return normalized, text, contract_failed
            except Exception:
                pass
        parsed = _parse_json(text)
        normalized, contract_failed = _normalize_result(parsed)
        return normalized, text, parse_failed or contract_failed


def call_text_endpoint(
    endpoint: AIModelEndpoint,
    messages: list[dict],
    ctx: dict,
    *,
    deadline: float | None = None,
) -> str:
    """Call one real endpoint for a natural-language manager answer.

    This path validates the same OpenAI-compatible envelope, SSRF policy,
    endpoint credential and timeout as structured calls, but accepts only a
    non-empty assistant text.  It never parses or manufactures an AI Review
    JSON result, and mock endpoints are rejected explicitly.
    """
    ensure_endpoint_allowed(endpoint)
    if is_mock_endpoint(endpoint):
        raise AIEndpointUnavailable(
            "自然语言 AI 问答不允许使用 mock AI 端点",
            status="UNAVAILABLE",
            endpoint=endpoint,
        )
    _result, raw, _parse_failed = call_endpoint(
        endpoint,
        messages,
        ctx,
        deadline=deadline,
        _response_mode="text",
    )
    text = raw.strip()
    if not text:
        raise AIResponseContractError("模型端点未返回非空 assistant 文本")
    return text


def _resolve_endpoint_key(endpoint: AIModelEndpoint) -> str:
    """Resolve one endpoint's credential without cross-provider fallbacks.

    ``credential_ref`` is the preferred UI-managed path.  The legacy
    ``api_key_env`` path remains available only after its identifier passes
    the approved-name validator.  In particular, this function never tries a
    generic OPENAI/OPENROUTER/RAG key when the endpoint's own credential is
    absent: that behaviour can silently send a key for provider A to provider
    B.
    """
    credential_ref = str(getattr(endpoint, "credential_ref", "") or "").strip()
    if credential_ref:
        try:
            key = default_secret_store().resolve(credential_ref)
        except SecretStoreError as exc:
            raise RuntimeError("AI 端点凭证存储不可用") from exc
        if not key:
            raise RuntimeError("AI 端点凭证不可用或已撤销")
        return key

    api_key_env = str(getattr(endpoint, "api_key_env", "") or "").strip()
    if not api_key_env:
        return ""
    try:
        env_name = resolve_secret_env_name(api_key_env)
    except ValueError as exc:
        raise RuntimeError("AI 端点环境变量引用未获批准") from exc
    key = os.getenv(env_name, "") if env_name else ""
    if not key:
        raise RuntimeError("AI 端点配置的环境变量未设置")
    return key