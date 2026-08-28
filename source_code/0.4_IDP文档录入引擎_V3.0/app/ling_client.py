from __future__ import annotations

import json
import os
from typing import Any, Dict

import httpx


class LingClient:
    """Local Ling-3.0-tiny client using an OpenAI-compatible chat endpoint.

    Defaults are intentionally local and can be overridden with environment variables:
    - LING_BASE_URL (default: http://127.0.0.1:8000/v1)
    - LING_MODEL (default: Ling-3.0-tiny)
    - LING_API_KEY (default: local)
    - LING_TIMEOUT_SECONDS (default: 120)
    """

    def __init__(self) -> None:
        self.base_url = os.getenv("LING_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")
        self.model = os.getenv("LING_MODEL", "Ling-3.0-tiny")
        self.api_key = os.getenv("LING_API_KEY", "local")
        self.timeout = float(os.getenv("LING_TIMEOUT_SECONDS", "120"))

    def __call__(self, document_type: str, context: str, base: Dict[str, Any]) -> Dict[str, Any]:
        prompt = self._build_prompt(document_type, context, base)
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是成都建工企业文档结构化抽取程序。"
                        "只依据原文提取，不得猜测；不存在的字段返回 null。"
                        "只输出合法 JSON，不要输出解释、Markdown 或代码块。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"].strip()
        return self._parse_json(content)

    @staticmethod
    def _build_prompt(document_type: str, context: str, base: Dict[str, Any]) -> str:
        return (
            f"文档类型: {document_type}\n\n"
            "规则引擎已经抽取出的字段如下，请只补充缺失字段，不要覆盖已有确定字段：\n"
            f"{json.dumps(base, ensure_ascii=False)}\n\n"
            "候选原文：\n"
            f"{context}\n\n"
            "要求：金额输出数字；税率 9% 输出 0.09；日期统一 YYYY-MM-DD；"
            "confidence 为 0~1；sources 中注明 ling:semantic。"
        )

    @staticmethod
    def _parse_json(content: str) -> Dict[str, Any]:
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].lstrip()
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("Ling response must be a JSON object")
        return result
