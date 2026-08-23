"""V0.2: 模型端点适配（mock / openai_compatible）。"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from ..models import AIModelEndpoint
from .mock import mock_review


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
    return {
        "risk_level": "UNKNOWN", "score": 0,
        "summary": "模型返回内容不是有效JSON，已保留原始响应供人工查看。",
        "findings": [{
            "severity": "MEDIUM", "area": "AI响应",
            "issue": "非结构化返回",
            "evidence": "模型未按约定返回JSON",
            "impact": "自动解析失败",
        }],
        "recommendations": [{
            "priority": "P2",
            "action": "调整模型提示或更换兼容模型后重新检查",
            "reason": "需要结构化结果",
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
        return result, json.dumps(result, ensure_ascii=False), False

    if endpoint.adapter != "openai_compatible":
        raise ValueError(f"暂不支持适配器: {endpoint.adapter}")

    key = os.getenv(endpoint.api_key_env, "") if endpoint.api_key_env else ""
    if endpoint.api_key_env and not key:
        raise RuntimeError(f"环境变量 {endpoint.api_key_env} 未设置")
    if not endpoint.base_url:
        raise RuntimeError("未配置 base_url")

    url = endpoint.base_url.rstrip("/") + "/" + endpoint.chat_path.lstrip("/")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"model": endpoint.model, "messages": messages, "temperature": 0.1}

    with httpx.Client(timeout=endpoint.timeout_seconds) as client:
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
    text = data["choices"][0]["message"]["content"]

    # V0.2: 解析失败单独标记
    try:
        parsed = json.loads(text)
        return parsed, text, False
    except Exception:
        m = re.search(r"\{.*\}", text or "", re.S)
        parse_failed = True
        if m:
            try:
                parsed = json.loads(m.group(0))
                return parsed, text, False
            except Exception:
                pass
        parsed = _parse_json(text)
        return parsed, text, parse_failed