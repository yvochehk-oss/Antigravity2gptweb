from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from typing import Any, Dict

import httpx


class GraniteAuditor:
    """Conditional second-line risk audit using a local OpenAI-compatible endpoint."""

    def __init__(self) -> None:
        self.base_url = os.getenv("GRANITE_BASE_URL", "http://127.0.0.1:8001/v1").rstrip("/")
        self.model = os.getenv("GRANITE_MODEL", "Granite-4.2-3B")
        self.api_key = os.getenv("GRANITE_API_KEY", "local")
        self.timeout = float(os.getenv("GRANITE_TIMEOUT_SECONDS", "180"))
        self.min_amount = Decimal(os.getenv("GRANITE_AUDIT_MIN_AMOUNT", "500000"))
        self.audit_on_warning = os.getenv("GRANITE_AUDIT_ON_WARNING", "1").lower() not in {
            "0", "false", "no"
        }

    def trigger_reasons(
        self,
        document_type: str,
        data: Dict[str, Any],
        validation: Dict[str, Any],
    ) -> list[str]:
        reasons: list[str] = []
        if validation.get("errors"):
            reasons.append("validation_error")
        if self.audit_on_warning and validation.get("warnings"):
            reasons.append("validation_warning")

        amount_keys = (
            "amount_tax_included",
            "amount_including_tax",
            "amount_excluding_tax",
        )
        for key in amount_keys:
            value = data.get(key)
            if value in (None, ""):
                continue
            try:
                if Decimal(str(value)) >= self.min_amount:
                    reasons.append(f"high_amount:{key}")
                    break
            except (InvalidOperation, ValueError):
                continue

        if document_type not in {"contract", "invoice"}:
            reasons.append("non_standard_document")
        return list(dict.fromkeys(reasons))

    def audit(
        self,
        document_type: str,
        data: Dict[str, Any],
        validation: Dict[str, Any],
        source_text: str,
    ) -> Dict[str, Any]:
        triggered_by = self.trigger_reasons(document_type, data, validation)
        if not triggered_by:
            return {
                "status": "not_triggered",
                "model": self.model,
                "triggered_by": [],
                "risk_level": "none",
                "risk_score": 0,
                "risks": [],
            }

        system = (
            "你是成都建工财税风险二审程序。只依据提供的结构化字段、校验结果和原文证据判断。"
            "禁止因为原文没有提及现场复试、环保备案、外部审批等材料而自行推定风险。"
            "如果核心四流、税率、数量、账户和合同条件在已提供证据中一致，不要发散假设。"
            "每个风险必须给出直接证据；没有直接证据就不要报告。"
            "只输出合法 JSON，不输出思考过程、Markdown 或解释。"
        )
        user = {
            "document_type": document_type,
            "triggered_by": triggered_by,
            "structured_data": data,
            "validation": validation,
            "source_excerpt": source_text[:9000],
            "required_schema": {
                "risk_level": "none|low|medium|high|critical",
                "risk_score": "0-100 integer",
                "risks": [
                    {
                        "code": "short stable code",
                        "title": "risk title",
                        "level": "low|medium|high|critical",
                        "evidence": "direct source evidence",
                        "reason": "why evidence creates risk",
                        "suggestion": "human verification suggestion",
                    }
                ],
                "summary": "short summary",
                "confidence": "0-1 number",
            },
        }
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"].strip()

        result = self._parse_json(content)
        result["status"] = "completed"
        result["model"] = self.model
        result["triggered_by"] = triggered_by
        return result

    @staticmethod
    def _parse_json(content: str) -> Dict[str, Any]:
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].lstrip()
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("Granite audit response must be a JSON object")
        result.setdefault("risk_level", "none")
        result.setdefault("risk_score", 0)
        result.setdefault("risks", [])
        result.setdefault("summary", "")
        result.setdefault("confidence", 0.0)
        return result
