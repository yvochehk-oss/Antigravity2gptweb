"""Read-only Canonical Facts adapter for deterministic Tax calculations."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from sqlalchemy import text


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def load_current_facts(db, project_id: int, fact_type: str | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT fact_id, source_document_id, project_id, fact_type, business_key, fact_version, "
        "source_hash, payload, evidence, confidence, producer, accepted_at "
        "FROM analytics_canonical_facts_current WHERE project_id=:project_id"
    )
    params: dict[str, Any] = {"project_id": project_id}
    if fact_type:
        sql += " AND fact_type=:fact_type"
        params["fact_type"] = fact_type
    sql += " ORDER BY fact_type, business_key, fact_version"
    return [dict(row) for row in db.execute(text(sql), params).mappings().all()]


def ssot_status(db) -> dict[str, int]:
    current = int(db.execute(text("SELECT count(*) FROM analytics_canonical_facts_current")).scalar_one())
    review = int(db.execute(text("SELECT count(*) FROM analytics_canonical_fact_review_queue")).scalar_one())
    unpublished = int(db.execute(text("SELECT count(*) FROM canonical_fact_outbox WHERE published_at IS NULL")).scalar_one())
    return {"current": current, "needs_review": review, "outbox_unpublished": unpublished}


def consolidate_invoice_facts(
    facts: Iterable[dict[str, Any]],
    internal_codes: set[str],
) -> dict[str, Any]:
    """Eliminate internal pass-through and keep only economic boundary flows."""
    external_revenue = Decimal("0")
    external_cost = Decimal("0")
    internal_eliminated = Decimal("0")
    boundary_edges: list[dict[str, Any]] = []

    for fact in facts:
        payload = _payload(fact.get("payload"))
        seller = str(payload.get("seller_entity_code") or payload.get("seller_code") or "").strip().upper()
        buyer = str(payload.get("buyer_entity_code") or payload.get("buyer_code") or "").strip().upper()
        net = _decimal(payload.get("net_amount"))
        vat = _decimal(payload.get("vat_amount"))
        if net == 0 and payload.get("total_amount") is not None:
            net = _decimal(payload.get("total_amount")) - vat
        seller_internal = seller in internal_codes
        buyer_internal = buyer in internal_codes

        if seller_internal and buyer_internal:
            internal_eliminated += net
            classification = "internal_eliminated"
            economic_amount = Decimal("0")
        elif (not seller_internal) and buyer_internal:
            nondeductible_vat = Decimal("0") if bool(payload.get("deductible", True)) else vat
            economic_amount = net + nondeductible_vat
            external_cost += economic_amount
            classification = "external_cost"
        elif seller_internal and (not buyer_internal):
            economic_amount = net
            external_revenue += economic_amount
            classification = "external_revenue"
        else:
            classification = "outside_system"
            economic_amount = Decimal("0")

        boundary_edges.append(
            {
                "fact_id": fact.get("fact_id"),
                "seller_code": seller,
                "buyer_code": buyer,
                "net_amount": net,
                "vat_amount": vat,
                "classification": classification,
                "economic_amount": economic_amount,
            }
        )

    return {
        "external_revenue": external_revenue,
        "external_cost": external_cost,
        "internal_eliminated": internal_eliminated,
        "true_profit": external_revenue - external_cost,
        "edges": boundary_edges,
    }


def build_consolidated_project_pnl(db, project_id: int) -> dict[str, Any]:
    internal_codes = {
        str(code).strip().upper()
        for code in db.execute(text("SELECT code FROM entities WHERE active = TRUE")).scalars().all()
        if code
    }
    invoice_facts = load_current_facts(db, project_id, "invoice")
    result = consolidate_invoice_facts(invoice_facts, internal_codes)
    result.update(
        {
            "project_id": project_id,
            "source": "analytics_canonical_facts_current",
            "fact_count": len(invoice_facts),
            "calculation_version": "canonical-boundary-pnl-v1",
        }
    )
    return result
