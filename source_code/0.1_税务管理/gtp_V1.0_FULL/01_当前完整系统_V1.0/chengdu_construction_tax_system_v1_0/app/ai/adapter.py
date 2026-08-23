"""V1.0: 模型端点适配（mock / openai_compatible / openrouter）。"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

from ..constants import RISK_ORDER
from ..models import AIModelEndpoint
from ..security import resolve_secret_env_name, validate_ai_endpoint_url
from .mock import mock_review

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
) -> tuple[dict, str, bool]:
    """返回 (parsed_result, raw_text, parse_failed)。"""
    if endpoint.adapter == "mock":
        result = mock_review(ctx, endpoint.name)
        normalized, contract_failed = _normalize_result(result)
        return normalized, json.dumps(result, ensure_ascii=False), contract_failed

    if endpoint.adapter not in ("openai_compatible", "openrouter"):
        raise ValueError(f"暂不支持适配器: {endpoint.adapter}")

    # 获取 API 密钥。  ``api_key_env`` is an identifier, never a secret
    # value; arbitrary environment-variable lookup would allow a malicious
    # endpoint row to exfiltrate DATABASE_URL or other credentials.
    key = ""
    if endpoint.api_key_env:
        env_name = resolve_secret_env_name(endpoint.api_key_env)
        key = os.getenv(env_name, "") if env_name else ""
    if not key:
        key = os.getenv("OPENROUTER_API_KEY", "") or os.getenv("OPENAI_API_KEY", "") or os.getenv("RAG_LLM_API_KEY", "")

    if not key and endpoint.api_key_env:
        raise RuntimeError(f"环境变量 {endpoint.api_key_env} 未设置且无默认 API Key")

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
    body = {"model": endpoint.model, "messages": messages, "temperature": 0.1}

    max_retries = 3
    last_err = None
    data = None

    with httpx.Client(timeout=endpoint.timeout_seconds, follow_redirects=False) as client:
        for attempt in range(max_retries):
            resp = client.post(url, headers=headers, json=body)
            if resp.status_code == 429 and attempt < max_retries - 1:
                # 429 速率限制退避重试
                time.sleep(3 * (attempt + 1))
                continue
            try:
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                last_err = e
                if attempt < max_retries - 1 and resp.status_code in (500, 502, 503, 504):
                    time.sleep(2)
                    continue
                raise last_err from None

    if not data or "choices" not in data or not data["choices"]:
        raise RuntimeError(f"模型端点返回格式异常: {data}")

    text = data["choices"][0]["message"]["content"]

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
