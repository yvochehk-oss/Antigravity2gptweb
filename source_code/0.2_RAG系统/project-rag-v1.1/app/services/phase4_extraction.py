"""Conservative deterministic extraction for Phase 4 accounting evidence.

Only explicit labelled monetary/percentage fields are accepted.  Missing or
ambiguous fields are left empty and will become ``needs_review`` at the
Canonical promotion gate rather than being guessed by an LLM.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from ..models import Chunk, Document

_NUMBER = r"([0-9][0-9,]*(?:\.[0-9]+)?)"
_MONEY_UNIT = r"(亿元|万元|元)?"


def _money(value: str, unit: str | None) -> Decimal:
    amount = Decimal(value.replace(",", ""))
    if unit == "亿元":
        return amount * Decimal("100000000")
    if unit == "万元":
        return amount * Decimal("10000")
    return amount


def _labelled_money(text: str, labels: tuple[str, ...]) -> tuple[Decimal | None, str]:
    label = "|".join(re.escape(item) for item in labels)
    match = re.search(rf"(?:{label})\s*[：:]?\s*(?:人民币)?\s*[￥¥]?\s*{_NUMBER}\s*{_MONEY_UNIT}", text)
    if not match:
        return None, ""
    return _money(match.group(1), match.group(2)), match.group(0)


def _labelled_percent(text: str, labels: tuple[str, ...]) -> tuple[Decimal | None, str]:
    label = "|".join(re.escape(item) for item in labels)
    match = re.search(rf"(?:{label})\s*[：:]?\s*([0-9]+(?:\.[0-9]+)?)\s*%", text)
    if not match:
        return None, ""
    return Decimal(match.group(1)) / Decimal("100"), match.group(0)


def _document_text(db, document_id: int) -> tuple[str, list[int]]:
    rows = db.execute(
        select(Chunk.id, Chunk.content)
        .where(Chunk.document_id == int(document_id))
        .order_by(Chunk.chunk_index, Chunk.id)
    ).all()
    return "\n".join(str(content or "") for _chunk_id, content in rows), [int(chunk_id) for chunk_id, _ in rows]


def extract_phase4_payload_from_chunks(
    db,
    doc: Document,
    fact_type: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    text, chunk_ids = _document_text(db, int(doc.id))
    payload: dict[str, Any] = {
        "as_of_date": str(getattr(doc, "document_date", "") or ""),
        "period": str(getattr(doc, "period", "") or ""),
        "contract_no": str(getattr(doc, "contract_no", "") or ""),
    }
    matched: list[str] = []

    if fact_type == "progress":
        completion, raw = _labelled_percent(
            text,
            ("累计完工进度", "累计完成进度", "完工进度", "工程进度", "履约进度"),
        )
        if completion is not None:
            payload["completion_percent"] = str(completion)
            matched.append(raw)
        estimated, raw = _labelled_money(text, ("预计总成本", "预计合同总成本", "预算总成本"))
        if estimated is not None:
            payload["estimated_total_cost"] = str(estimated)
            matched.append(raw)
        recognized, raw = _labelled_money(text, ("累计确认收入", "应确认收入", "累计应确认收入"))
        if recognized is not None:
            payload["recognized_revenue"] = str(recognized)
            matched.append(raw)
        claim, raw = _labelled_money(text, ("进度款申报金额", "本期申报金额", "确权金额", "本期确权金额"))
        if claim is not None:
            payload["claim_amount"] = str(claim)
            matched.append(raw)

    elif fact_type == "accrual":
        amount, raw = _labelled_money(
            text,
            ("应计未票成本", "已发生未开票成本", "暂估成本", "本期暂估成本"),
        )
        if amount is not None:
            payload["amount"] = str(amount)
            matched.append(raw)
        reversal, raw = _labelled_money(text, ("暂估冲回", "冲回金额", "本期冲回金额"))
        if reversal is not None:
            payload["reversal_amount"] = str(reversal)
            matched.append(raw)
        payload["tax_deductible"] = False

    elif fact_type == "tax_adjustment":
        add, raw_add = _labelled_money(text, ("纳税调增", "应纳税所得额调增", "调增金额"))
        deduct, raw_deduct = _labelled_money(text, ("纳税调减", "应纳税所得额调减", "调减金额"))
        if add is not None and deduct is not None:
            payload["_extraction_error"] = "tax adjustment document contains both ADD and DEDUCT labels"
            matched.extend([raw_add, raw_deduct])
        elif add is not None:
            payload.update({"direction": "ADD", "amount": str(add)})
            matched.append(raw_add)
        elif deduct is not None:
            payload.update({"direction": "DEDUCT", "amount": str(deduct)})
            matched.append(raw_deduct)

    evidence = {
        "document_id": int(doc.id),
        "chunk_ids": chunk_ids,
        "matched_labels": matched,
        "extraction_policy": "phase4-labelled-fields-v1",
    }
    return payload, evidence


def phase4_business_key(doc: Document, fact_type: str, payload: dict[str, Any]) -> str:
    period = str(payload.get("period") or payload.get("as_of_date") or "").strip()
    contract_no = str(payload.get("contract_no") or "").strip()
    base = contract_no or f"document:{int(doc.id)}"
    return f"{fact_type}:{base}:{period or int(doc.id)}"


__all__ = ["extract_phase4_payload_from_chunks", "phase4_business_key"]
