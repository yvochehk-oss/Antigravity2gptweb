"""Deterministic legal-entity aggregation across all Canonical-Fact projects.

This module is deliberately a projection, not a filing ledger.  It preserves
intercompany trades for each independent legal entity and aggregates every
accepted/current invoice fact involving that entity across projects.  Official
VAT payable/carry-forward remains authoritative in ``entity_vat_ledgers``.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import text

from .canonical_ssot import (
    DEDUCTIBILITY_ELIGIBLE,
    DEDUCTIBILITY_INELIGIBLE,
    _decimal,
    _payload,
    invoice_deductibility_status,
)

_SOURCE = "analytics_canonical_facts_current"
_SCOPE = "LEGAL_ENTITY_PROJECTION"


def _code(value: Any) -> str:
    return str(value or "").strip().upper()


def _invoice_period(payload: dict[str, Any]) -> str:
    raw = str(payload.get("invoice_date") or payload.get("period") or "").strip()
    if len(raw) >= 7 and raw[4] == "-":
        return raw[:7]
    return ""


def _new_bucket(project_id: int | None, project_meta: dict[int, dict[str, str]]) -> dict[str, Any]:
    meta = project_meta.get(int(project_id), {}) if project_id is not None else {}
    return {
        "project_id": int(project_id) if project_id is not None else None,
        "project_code": str(meta.get("code") or "") if project_id is not None else "",
        "project_name": str(meta.get("name") or "") if project_id is not None else "非项目归属",
        "revenue": Decimal("0"),
        "book_cost_projection": Decimal("0"),
        "accounting_profit_projection": Decimal("0"),
        "output_vat": Decimal("0"),
        "input_vat": Decimal("0"),
        "deductible_input_vat": Decimal("0"),
        "nondeductible_input_vat": Decimal("0"),
        "pending_input_vat": Decimal("0"),
        "internal_trade_net": Decimal("0"),
        "internal_trade_vat": Decimal("0"),
        "fact_count": 0,
        "fact_ids": [],
    }


def _aggregate_facts_for_entity(
    facts: Iterable[dict[str, Any]],
    *,
    entity_code: str,
    internal_codes: set[str],
    project_meta: dict[int, dict[str, str]] | None = None,
    period: str | None = None,
    through_period: str | None = None,
) -> dict[str, Any]:
    """Pure aggregation core used by the DB adapter and regression tests.

    ``period`` selects one exact invoice month. ``through_period`` selects all
    recognizable invoice months up to and including the requested month. They
    are intentionally mutually exclusive so current-period and cumulative
    projections cannot silently use different inclusion semantics.
    """
    if period and through_period:
        raise ValueError("period and through_period are mutually exclusive")

    wanted = _code(entity_code)
    projects = project_meta or {}
    buckets: dict[int | None, dict[str, Any]] = {}
    data_gaps: set[str] = set()

    revenue = Decimal("0")
    book_cost = Decimal("0")
    output_vat = Decimal("0")
    input_vat = Decimal("0")
    deductible_input_vat = Decimal("0")
    nondeductible_input_vat = Decimal("0")
    pending_input_vat = Decimal("0")
    internal_trade_net = Decimal("0")
    internal_trade_vat = Decimal("0")
    included_fact_ids: list[int] = []

    if wanted not in internal_codes:
        data_gaps.add(f"ENTITY_NOT_IN_INTERNAL_SCOPE:{wanted}")

    for fact in facts:
        payload = _payload(fact.get("payload"))
        seller = _code(payload.get("seller_entity_code") or payload.get("seller_code"))
        buyer = _code(payload.get("buyer_entity_code") or payload.get("buyer_code"))
        if wanted not in {seller, buyer}:
            continue

        if period or through_period:
            fact_period = _invoice_period(payload)
            if not fact_period:
                data_gaps.add("CANONICAL_INVOICE_PERIOD_MISSING")
                continue
            if period and fact_period != period:
                continue
            if through_period and fact_period > through_period:
                continue

        net = _decimal(payload.get("net_amount"))
        vat = _decimal(payload.get("vat_amount"))
        if net == 0 and payload.get("total_amount") is not None:
            net = _decimal(payload.get("total_amount")) - vat

        raw_project_id = fact.get("project_id")
        project_id = int(raw_project_id) if raw_project_id is not None else None
        bucket = buckets.setdefault(project_id, _new_bucket(project_id, projects))
        fact_id = int(fact.get("fact_id") or 0)
        if fact_id:
            bucket["fact_ids"].append(fact_id)
            included_fact_ids.append(fact_id)
        bucket["fact_count"] += 1

        if seller == wanted:
            revenue += net
            output_vat += vat
            bucket["revenue"] += net
            bucket["output_vat"] += vat
            if buyer in internal_codes:
                internal_trade_net += net
                internal_trade_vat += vat
                bucket["internal_trade_net"] += net
                bucket["internal_trade_vat"] += vat

        if buyer == wanted:
            input_vat += vat
            bucket["input_vat"] += vat
            status = invoice_deductibility_status(payload)
            nondeductible_cost_vat = Decimal("0")
            if status == DEDUCTIBILITY_ELIGIBLE:
                deductible_input_vat += vat
                bucket["deductible_input_vat"] += vat
            elif status == DEDUCTIBILITY_INELIGIBLE:
                nondeductible_input_vat += vat
                bucket["nondeductible_input_vat"] += vat
                nondeductible_cost_vat = vat
            else:
                pending_input_vat += vat
                bucket["pending_input_vat"] += vat

            cost_amount = net + nondeductible_cost_vat
            book_cost += cost_amount
            bucket["book_cost_projection"] += cost_amount

            if seller in internal_codes:
                internal_trade_net += net
                internal_trade_vat += vat
                bucket["internal_trade_net"] += net
                bucket["internal_trade_vat"] += vat

    for bucket in buckets.values():
        bucket["accounting_profit_projection"] = bucket["revenue"] - bucket["book_cost_projection"]

    accounted_input_vat = deductible_input_vat + nondeductible_input_vat + pending_input_vat
    input_vat_unaccounted = input_vat - accounted_input_vat
    if input_vat_unaccounted != 0:
        data_gaps.add("INPUT_VAT_ACCOUNTING_IDENTITY_FAILED")
    if pending_input_vat != 0:
        data_gaps.add("INPUT_VAT_DEDUCTIBILITY_NEEDS_REVIEW")

    ordered_projects = sorted(
        (bucket for key, bucket in buckets.items() if key is not None),
        key=lambda item: (item["project_code"], item["project_id"] or 0),
    )
    non_project = buckets.get(None, _new_bucket(None, projects))

    return {
        "status": "DEGRADED" if data_gaps else "READY",
        "scope": _SCOPE,
        "is_filing_basis": False,
        "entity_code": wanted,
        "period": period or through_period or "",
        "revenue": revenue,
        "book_cost_projection": book_cost,
        "accounting_profit_projection": revenue - book_cost,
        "output_vat": output_vat,
        "input_vat": input_vat,
        "deductible_input_vat": deductible_input_vat,
        "nondeductible_input_vat": nondeductible_input_vat,
        "pending_input_vat": pending_input_vat,
        "input_vat_accounted": accounted_input_vat,
        "input_vat_unaccounted": input_vat_unaccounted,
        "input_vat_identity_ok": input_vat_unaccounted == 0,
        "internal_trade_net": internal_trade_net,
        "internal_trade_vat": internal_trade_vat,
        "project_contributions": ordered_projects,
        "non_project_contribution": non_project,
        "fact_count": len(included_fact_ids),
        "fact_ids": included_fact_ids,
        "data_gaps": sorted(data_gaps),
        "source_of_truth": _SOURCE,
        "official_vat_ledger": "entity_vat_ledgers",
        "limitations": [
            "Canonical invoice projection; it does not replace the official legal-entity VAT ledger",
            "book_cost_projection uses invoice net amount plus confirmed non-deductible VAT only",
            "pending deductibility remains reviewable and is not silently included in deductible VAT or cost",
        ],
        "calculation_version": "legal-entity-canonical-scope-v1",
    }


def aggregate_legal_entity_scope(
    db,
    entity_code: str,
    *,
    period: str | None = None,
) -> dict[str, Any]:
    """Aggregate one legal entity across every project in Canonical Facts.

    Internal-to-internal trades are intentionally retained because each entity
    is an independent accounting/tax subject.  The returned VAT values are a
    documentary projection only; statutory payable and carry-forward must be
    read from the V3 legal-entity VAT ledger.
    """
    wanted = _code(entity_code)
    internal_codes = {
        _code(code)
        for code in db.execute(text("SELECT code FROM entities WHERE active = TRUE")).scalars().all()
        if code
    }
    project_meta = {
        int(row["id"]): {"code": str(row["code"] or ""), "name": str(row["name"] or "")}
        for row in db.execute(text("SELECT id, code, name FROM projects ORDER BY id")).mappings().all()
    }
    facts = [
        dict(row)
        for row in db.execute(
            text(
                "SELECT fact_id, source_document_id, project_id, business_key, fact_version, "
                "source_hash, payload, evidence, confidence, producer, accepted_at "
                "FROM analytics_canonical_facts_current "
                "WHERE fact_type='invoice' "
                "ORDER BY project_id NULLS LAST, business_key, fact_version"
            )
        ).mappings().all()
    ]
    projection = _aggregate_facts_for_entity(
        facts,
        entity_code=wanted,
        internal_codes=internal_codes,
        project_meta=project_meta,
        period=period,
    )
    if period:
        cumulative = _aggregate_facts_for_entity(
            facts,
            entity_code=wanted,
            internal_codes=internal_codes,
            project_meta=project_meta,
            through_period=period,
        )
        projection["cumulative"] = {
            "revenue": cumulative["revenue"],
            "book_cost_projection": cumulative["book_cost_projection"],
            "accounting_profit_projection": cumulative["accounting_profit_projection"],
            "output_vat": cumulative["output_vat"],
            "input_vat": cumulative["input_vat"],
            "fact_count": cumulative["fact_count"],
        }
        if cumulative["status"] == "DEGRADED":
            projection["status"] = "DEGRADED"
            projection["data_gaps"] = sorted(set(projection["data_gaps"]) | set(cumulative["data_gaps"]))
    return projection


__all__ = ["aggregate_legal_entity_scope"]
