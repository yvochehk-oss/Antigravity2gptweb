from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from .schemas import ContractData, InvoiceData


LLMCallable = Callable[[str, str, Dict[str, Any]], Dict[str, Any]]


def _clean_money(value: str) -> Decimal:
    return Decimal(value.replace(",", "").replace("，", "").strip())


class RuleExtractor:
    USCC_RE = re.compile(r"\b[0-9A-HJ-NPQRTUWXY]{18}\b")
    DATE_RE = re.compile(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?")
    MONEY_RE = re.compile(r"[¥￥]?\s*([0-9]{1,3}(?:[,，][0-9]{3})*(?:\.\d{1,2})|[0-9]+(?:\.\d{1,2})?)")

    @staticmethod
    def _first(pattern: str, text: str) -> Optional[str]:
        match = re.search(pattern, text, re.I | re.M)
        return match.group(1).strip() if match else None

    def contract(self, text: str) -> ContractData:
        data = ContractData()
        no = self._first(r"合同(?:编号|号)\s*[:：]?\s*([^\s，。,；;]{3,80})", text)
        if no:
            data.contract_no = no
            data.confidence["contract_no"] = 0.96
            data.sources["contract_no"] = "rule:contract_no"

        name = self._first(r"合同名称\s*[:：]?\s*([^\n]{2,120})", text)
        if name:
            data.contract_name = name
            data.confidence["contract_name"] = 0.93
            data.sources["contract_name"] = "rule:contract_name"

        rate = self._first(r"(?:增值税)?税率\s*(?:为|[:：])?\s*(\d+(?:\.\d+)?)\s*%", text)
        if rate:
            data.tax_rate = Decimal(rate) / Decimal("100")
            data.confidence["tax_rate"] = 0.98
            data.sources["tax_rate"] = "rule:tax_rate"

        amount = self._first(
            r"(?:含税总价|合同含税总价|合同总价|合同金额)\s*(?:为|[:：])?[^0-9¥￥]{0,30}[¥￥]?\s*([0-9][0-9,，]*(?:\.\d{1,2})?)",
            text,
        )
        if amount:
            data.amount_tax_included = _clean_money(amount)
            data.confidence["amount_tax_included"] = 0.95
            data.sources["amount_tax_included"] = "rule:contract_amount"

        return data

    def invoice(self, text: str) -> InvoiceData:
        data = InvoiceData()
        no = self._first(r"发票号码\s*[:：]?\s*([0-9A-Za-z]{6,30})", text)
        if no:
            data.invoice_no = no
            data.confidence["invoice_no"] = 0.99
            data.sources["invoice_no"] = "rule:invoice_no"

        code = self._first(r"发票代码\s*[:：]?\s*([0-9A-Za-z]{6,30})", text)
        if code:
            data.invoice_code = code
            data.confidence["invoice_code"] = 0.99
            data.sources["invoice_code"] = "rule:invoice_code"

        total = self._first(r"价税合计[^0-9¥￥]{0,30}[¥￥]?\s*([0-9][0-9,，]*(?:\.\d{1,2})?)", text)
        if total:
            data.amount_including_tax = _clean_money(total)
            data.confidence["amount_including_tax"] = 0.98
            data.sources["amount_including_tax"] = "rule:invoice_total"

        return data


class HybridExtractor:
    """Rules first; Ling only fills missing semantic fields.

    Semantic-model failure never discards deterministic extraction. Instead a
    _meta.semantic_error marker is returned and the pipeline routes the document
    to human review.
    """

    def __init__(self, llm: Optional[LLMCallable] = None) -> None:
        self.rules = RuleExtractor()
        self.llm = llm

    @staticmethod
    def candidate_context(text: str, keywords: list[str], window: int = 600) -> str:
        chunks: list[str] = []
        for keyword in keywords:
            start = 0
            while True:
                idx = text.find(keyword, start)
                if idx < 0:
                    break
                left = max(0, idx - window)
                right = min(len(text), idx + len(keyword) + window)
                chunks.append(text[left:right])
                start = idx + len(keyword)
        return "\n---\n".join(dict.fromkeys(chunks))[:6000]

    def _semantic_fill(self, document_type: str, ctx: str, base: Dict[str, Any]) -> Dict[str, Any]:
        if not self.llm or not ctx:
            return base
        try:
            extra = self.llm(document_type, ctx, base)
            base = self._merge(base, extra)
            base.setdefault("_meta", {})["semantic_model_used"] = True
        except Exception as exc:
            base.setdefault("_meta", {}).update({
                "semantic_model_used": False,
                "semantic_error": f"{type(exc).__name__}: {exc}",
            })
        return base

    def extract(self, document_type: str, text: str) -> Dict[str, Any]:
        if document_type == "contract":
            base = self.rules.contract(text).model_dump(mode="json")
            ctx = self.candidate_context(text, ["付款", "结算", "期限", "质保", "甲方", "乙方"])
            return self._semantic_fill("contract", ctx, base)

        if document_type == "invoice":
            base = self.rules.invoice(text).model_dump(mode="json")
            ctx = self.candidate_context(text, ["购买方", "销售方", "税额", "金额", "税率"])
            return self._semantic_fill("invoice", ctx, base)

        return {}

    @staticmethod
    def _merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in extra.items():
            if value is None:
                continue
            if key in {"confidence", "sources"} and isinstance(value, dict):
                base.setdefault(key, {}).update(value)
            elif base.get(key) in (None, "", [], {}):
                base[key] = value
        return base
