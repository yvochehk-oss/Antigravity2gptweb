"""Read-only Canonical Facts adapter for deterministic Tax calculations."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from sqlalchemy import text

DEDUCTIBILITY_ELIGIBLE = "ELIGIBLE"
DEDUCTIBILITY_INELIGIBLE = "INELIGIBLE"
DEDUCTIBILITY_NEEDS_REVIEW = "NEEDS_REVIEW"


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


def invoice_deductibility_status(payload: dict[str, Any]) -> str:
    """Return a tri-state management classification without collapsing unknown to False.

    Canonical facts preserve documentary evidence. They are not allowed to turn a
    missing extraction into a legal conclusion. A producer may provide either the
    historical ``deductible`` boolean or the explicit tri-state field. Anything else
    is reviewable rather than silently non-deductible.
    """
    explicit = str(payload.get("deductibility_status") or "").strip().upper()
    if explicit in {DEDUCTIBILITY_ELIGIBLE, "CONFIRMED", "DEDUCTIBLE"}:
        return DEDUCTIBILITY_ELIGIBLE
    if explicit in {DEDUCTIBILITY_INELIGIBLE, "REJECTED", "NONDEDUCTIBLE", "NON_DEDUCTIBLE"}:
        return DEDUCTIBILITY_INELIGIBLE
    if explicit in {DEDUCTIBILITY_NEEDS_REVIEW, "PENDING_REVIEW", "UNKNOWN", "UNVALIDATED"}:
        return DEDUCTIBILITY_NEEDS_REVIEW

    raw = payload.get("deductible")
    if raw is True:
        return DEDUCTIBILITY_ELIGIBLE
    if raw is False:
        return DEDUCTIBILITY_INELIGIBLE
    return DEDUCTIBILITY_NEEDS_REVIEW


def classify_invoice_edge(
    payload: dict[str, Any],
    internal_codes: set[str],
) -> dict[str, Any]:
    seller = str(
        payload.get("seller_entity_code")
        or payload.get("seller_code")
        or ""
    ).strip().upper()
    buyer = str(
        payload.get("buyer_entity_code")
        or payload.get("buyer_code")
        or ""
    ).strip().upper()
    net = _decimal(payload.get("net_amount"))
    vat = _decimal(payload.get("vat_amount"))
    if net == 0 and payload.get("total_amount") is not None:
        net = _decimal(payload.get("total_amount")) - vat

    seller_internal = seller in internal_codes
    buyer_internal = buyer in internal_codes
    if seller_internal and buyer_internal:
        classification = "internal_eliminated"
    elif (not seller_internal) and buyer_internal:
        classification = "external_cost"
    elif seller_internal and (not buyer_internal):
        classification = "external_revenue"
    else:
        classification = "outside_system"

    return {
        "seller_code": seller,
        "buyer_code": buyer,
        "net_amount": net,
        "vat_amount": vat,
        "seller_internal": seller_internal,
        "buyer_internal": buyer_internal,
        "classification": classification,
        "deductibility_status": invoice_deductibility_status(payload),
    }


def consolidate_invoice_facts(
    facts: Iterable[dict[str, Any]],
    internal_codes: set[str],
) -> dict[str, Any]:
    """Eliminate internal pass-through and expose one auditable project boundary.

    Project boundary VAT is a management metric, not a legal-entity filing result.
    Internal-to-internal invoices are retained in lineage but contribute zero to
    project boundary revenue/cost/VAT.

    Input VAT is never silently lost:
        boundary_input_vat
          == deductible_input_vat
           + nondeductible_input_vat
           + pending_input_vat
    """
    external_revenue = Decimal("0")
    external_cost = Decimal("0")
    internal_eliminated = Decimal("0")

    boundary_output_net = Decimal("0")
    boundary_output_vat = Decimal("0")
    boundary_input_net = Decimal("0")
    boundary_input_vat = Decimal("0")
    deductible_input_vat = Decimal("0")
    nondeductible_input_vat = Decimal("0")
    pending_input_vat = Decimal("0")
    internal_eliminated_vat = Decimal("0")

    boundary_edges: list[dict[str, Any]] = []

    for fact in facts:
        payload = _payload(fact.get("payload"))
        edge = classify_invoice_edge(payload, internal_codes)
        net = edge["net_amount"]
        vat = edge["vat_amount"]
        classification = edge["classification"]
        deductibility = edge["deductibility_status"]

        if classification == "internal_eliminated":
            internal_eliminated += net
            internal_eliminated_vat += vat
            economic_amount = Decimal("0")
        elif classification == "external_cost":
            boundary_input_net += net
            boundary_input_vat += vat
            if deductibility == DEDUCTIBILITY_ELIGIBLE:
                deductible_input_vat += vat
                economic_amount = net
            elif deductibility == DEDUCTIBILITY_INELIGIBLE:
                nondeductible_input_vat += vat
                economic_amount = net + vat
            else:
                pending_input_vat += vat
                economic_amount = net
            external_cost += economic_amount
        elif classification == "external_revenue":
            boundary_output_net += net
            boundary_output_vat += vat
            economic_amount = net
            external_revenue += economic_amount
        else:
            economic_amount = Decimal("0")

        boundary_edges.append(
            {
                "fact_id": fact.get("fact_id"),
                "seller_code": edge["seller_code"],
                "buyer_code": edge["buyer_code"],
                "net_amount": net,
                "vat_amount": vat,
                "classification": classification,
                "deductibility_status": deductibility,
                "economic_amount": economic_amount,
            }
        )

    accounted_input_vat = deductible_input_vat + nondeductible_input_vat + pending_input_vat
    input_vat_unaccounted = boundary_input_vat - accounted_input_vat
    input_vat_identity_ok = input_vat_unaccounted == Decimal("0")
    signed_vat_position = boundary_output_vat - deductible_input_vat

    return {
        "external_revenue": external_revenue,
        "external_cost": external_cost,
        "internal_eliminated": internal_eliminated,
        "internal_eliminated_vat": internal_eliminated_vat,
        "boundary_margin": external_revenue - external_cost,
        "boundary_output_net": boundary_output_net,
        "boundary_output_vat": boundary_output_vat,
        "boundary_input_net": boundary_input_net,
        "boundary_input_vat": boundary_input_vat,
        "deductible_input_vat": deductible_input_vat,
        "nondeductible_input_vat": nondeductible_input_vat,
        "pending_input_vat": pending_input_vat,
        "input_vat_accounted": accounted_input_vat,
        "input_vat_unaccounted": input_vat_unaccounted,
        "input_vat_identity_ok": input_vat_identity_ok,
        "signed_vat_position": signed_vat_position,
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
            "basis": "accepted_invoice_facts",
            "is_final_profit": False,
            "limitations": [
                "invoice boundary metric only; completion progress and unbilled accruals are not included",
                "settlement adjustments and book-tax differences require later deterministic layers",
                "project signed VAT position is a management metric and is not a legal-entity filing result",
            ],
            "calculation_version": "canonical-boundary-pnl-v2",
        }
    )
    return result
