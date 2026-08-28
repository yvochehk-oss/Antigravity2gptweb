from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import httpx


class GraniteAuditClient:
    """Optional second-line auditor for high-risk documents only.

    Granite never owns field extraction or posting decisions. Its output is an
    advisory risk report that can route a document to human review.
    """

    def __init__(self) -> None:
        self.base_url = os.getenv("GRANITE_BASE_URL", "http://127.0.0.1:8001/v1").rstrip("/")
        self.model = os.getenv("GRANITE_MODEL", "granite-4.2-3b")
        self.api_key = os.getenv("GRANITE_API_KEY", "local")
        self.timeout = float(os.getenv("GRANITE_TIMEOUT_SECONDS", "180"))
        self.amount_threshold = self._amount_threshold_from_env()

    @staticmethod
    def _amount_threshold_from_env() -> Optional[Decimal]:
        """Return the operator-configured high-amount threshold.

        There is deliberately no business-policy default. Empty/zero values
        disable amount-only triggering so deployments must choose their own
        materiality threshold instead of inheriting an arbitrary number.
        """
        raw = os.getenv("GRANITE_MIN_AMOUNT", "").strip()
        if not raw:
            return None
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError("GRANITE_MIN_AMOUNT must be a valid non-negative number") from exc
        if value < 0:
            raise ValueError("GRANITE_MIN_AMOUNT must be non-negative")
        return value if value > 0 else None

    def should_audit(
        self,
        document_type: str,
        data: Dict[str, Any],
        validation: Dict[str, Any],
        text: str,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if validation.get("errors"):
            reasons.append("validation_error")
        if validation.get("warnings"):
            reasons.append("validation_warning")

        if self.amount_threshold is not None:
            for key in ("amount_tax_included", "amount_including_tax", "amount_excluding_tax"):
                value = data.get(key)
                if value in (None, ""):
                    continue
                try:
                    if Decimal(str(value)) >= self.amount_threshold:
                        reasons.append("high_amount")
                        break
                except (InvalidOperation, TypeError, ValueError):
                    continue

        head = text[:12000]
        suspicious_terms = (
            "个人账户", "私人账户", "现金支付", "代收款", "代付款", "税率异常",
            "发票不一致", "数量不符", "银行账户变更", "收款账户变更",
        )
        if any(term in head for term in suspicious_terms):
            reasons.append("risk_keyword")

        return document_type in {"contract", "invoice"} and bool(reasons), sorted(set(reasons))

    def audit(
        self,
        document_type: str,
        data: Dict[str, Any],
        validation: Dict[str, Any],
        text: str,
    ) -> Dict[str, Any]:
        should_run, trigger_reasons = self.should_audit(document_type, data, validation, text)
        if not should_run:
            return {
                "enabled": True,
                "executed": False,
                "trigger_reasons": trigger_reasons,
                "risk_level": "none",
                "risks": [],
            }

        system = (
            "你是成都建工财税风险二审模型。只根据提供的结构化字段、校验结果和原文证据判断。"
            "不要因为材料未提及外部备案、复试、环保、资质或其他外部信息而推测风险。"
            "未提供的信息只能标记为未提供，不能自动等同于异常、违规或缺失。"
            "只有存在明确文本证据、确定性校验异常或字段冲突时才报告风险。"
            "每一条风险必须引用本次输入中可核对的 evidence；没有证据就不要生成该风险。"
            "如果核心字段在已提供证据中一致，不要发散未提供的外部条件。"
            "返回严格 JSON，不要输出思考过程。"
        )
        payload = {
            "document_type": document_type,
            "structured_data": data,
            "validation": validation,
            "trigger_reasons": trigger_reasons,
            "source_excerpt": text[:10000],
            "required_schema": {
                "risk_level": "none|low|medium|high|critical",
                "risks": [
                    {
                        "code": "string",
                        "title": "string",
                        "level": "low|medium|high|critical",
                        "evidence": "string",
                        "reason": "string",
                        "confidence": "0-1"
                    }
                ],
                "summary": "string"
            },
        }
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
                ],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json"):
                content = content[4:].lstrip()
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("Granite audit response must be a JSON object")
        result.setdefault("risk_level", "none")
        result.setdefault("risks", [])
        result.setdefault("summary", "")
        result.update({
            "enabled": True,
            "executed": True,
            "trigger_reasons": trigger_reasons,
            "model": self.model,
        })
        return result
