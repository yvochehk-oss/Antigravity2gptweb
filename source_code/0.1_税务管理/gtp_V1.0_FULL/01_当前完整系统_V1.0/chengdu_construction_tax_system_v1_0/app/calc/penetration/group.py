"""Task18 canonical group penetration.

ACCRUAL uses FulfillmentFact edges and PostgreSQL WITH RECURSIVE to eliminate
internal operating transfers while retaining external leaf cost. TAX aggregates
official legal-entity VAT ledgers without eliminating internal invoices. CASH is
fail-closed until Task19 introduces canonical PaymentFact.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.calc.basis.accrual_basis import validate_accrual_source
from app.calc.basis.cash_basis import CashBasisNotReady
from app.calc.basis.contracts import SourceKind, SourceRole
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_vat_ledger_models import EntityVatLedger

RULESET_VERSION = "V3_GROUP_PENETRATION_V1"
MONEY = Decimal("0.01")
MAX_DEPTH = 32


class GroupPenetrationError(ValueError):
    pass


class GroupCycleDetected(GroupPenetrationError):
    def __init__(self, paths: list[str]):
        super().__init__("CYCLE_DETECTED: " + "; ".join(paths))
        self.paths = tuple(paths)


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def canonical_hash(payload: Any) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def month_start(value: date | str) -> date:
    if isinstance(value, date):
        return date(value.year, value.month, 1)
    rendered = str(value)
    parsed = date.fromisoformat(f"{rendered}-01" if len(rendered) == 7 else rendered)
    return date(parsed.year, parsed.month, 1)


def next_month(period: date) -> date:
    if period.month == 12:
        return date(period.year + 1, 1, 1)
    return date(period.year, period.month + 1, 1)


ACCRUAL_RECURSIVE_SQL = text("""
    WITH RECURSIVE roots AS (
        SELECT ff.fact_id AS root_fact_id, ff.fact_id AS edge_fact_id,
               ff.performing_party_id AS current_party_id,
               ff.receiving_party_id AS receiving_party_id,
               ff.amount AS amount, 0::integer AS depth,
               ARRAY[ff.performing_party_id]::integer[] AS visited,
               ARRAY[ff.receiving_party_id, ff.performing_party_id]::integer[] AS path,
               false AS cycle_detected, 'EXTERNAL_REVENUE'::text AS edge_kind
        FROM fulfillment_facts ff
        JOIN facts f ON f.id = ff.fact_id
        JOIN parties performer ON performer.id = ff.performing_party_id
        JOIN parties receiver ON receiver.id = ff.receiving_party_id
        WHERE f.is_current IS TRUE
          AND f.validation_status = 'VALID'
          AND ff.fulfillment_date >= :period_start
          AND ff.fulfillment_date < :period_end
          AND ff.amount IS NOT NULL
          AND performer.party_type = 'internal'
          AND receiver.party_type = 'external'
    ),
    walk AS (
        SELECT * FROM roots
        UNION ALL
        SELECT w.root_fact_id, up.fact_id AS edge_fact_id,
               up.performing_party_id AS current_party_id,
               up.receiving_party_id AS receiving_party_id,
               up.amount AS amount, w.depth + 1 AS depth,
               w.visited || up.performing_party_id,
               w.path || up.performing_party_id,
               (up.performing_party_id = ANY(w.visited)) AS cycle_detected,
               CASE WHEN upstream_party.party_type = 'external'
                    THEN 'EXTERNAL_LEAF_COST'
                    ELSE 'INTERNAL_ELIMINATION' END::text AS edge_kind
        FROM walk w
        JOIN fulfillment_facts up ON up.receiving_party_id = w.current_party_id
        JOIN facts uf ON uf.id = up.fact_id
        JOIN parties upstream_party ON upstream_party.id = up.performing_party_id
        WHERE w.edge_kind <> 'EXTERNAL_LEAF_COST'
          AND w.cycle_detected IS FALSE
          AND w.depth < :max_depth
          AND uf.is_current IS TRUE
          AND uf.validation_status = 'VALID'
          AND up.fulfillment_date >= :period_start
          AND up.fulfillment_date < :period_end
          AND up.amount IS NOT NULL
    )
    SELECT root_fact_id, edge_fact_id, current_party_id, receiving_party_id,
           amount, depth, path, cycle_detected, edge_kind
    FROM walk
    ORDER BY root_fact_id, depth, edge_fact_id
""")


def accrual_snapshot(session: Session, *, period: date | str, max_depth: int = MAX_DEPTH) -> dict[str, Any]:
    validate_accrual_source(SourceKind.FULFILLMENT_FACT, SourceRole.PRIMARY_AMOUNT)
    analysis_period = month_start(period)
    if max_depth < 1:
        raise GroupPenetrationError("max_depth must be >= 1")
    rows = session.execute(
        ACCRUAL_RECURSIVE_SQL,
        {"period_start": analysis_period, "period_end": next_month(analysis_period), "max_depth": max_depth},
    ).mappings().all()

    cycles: list[str] = []
    components_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    max_seen = 0
    for row in rows:
        edge_kind = str(row["edge_kind"])
        edge_fact_id = int(row["edge_fact_id"])
        path = "->".join(str(item) for item in row["path"])
        depth = int(row["depth"])
        max_seen = max(max_seen, depth)
        if row["cycle_detected"]:
            cycles.append(path)
            continue
        key = (edge_kind, edge_fact_id)
        candidate = {
            "component_type": edge_kind,
            "fulfillment_fact_id": edge_fact_id,
            "amount": str(money(row["amount"])),
            "depth": depth,
            "path": path,
            "root_fact_id": int(row["root_fact_id"]),
        }
        previous = components_by_key.get(key)
        if previous is None or depth < int(previous["depth"]):
            components_by_key[key] = candidate

    if cycles:
        raise GroupCycleDetected(sorted(set(cycles)))

    components = sorted(
        components_by_key.values(),
        key=lambda row: (row["component_type"], row["fulfillment_fact_id"], row["depth"]),
    )
    return {"basis": "ACCRUAL", "analysis_period": str(analysis_period), "max_depth": max_seen, "components": components}


def summarize_accrual(snapshot: dict[str, Any]) -> dict[str, Decimal]:
    components = snapshot["components"]
    external_revenue = sum((money(row["amount"]) for row in components if row["component_type"] == "EXTERNAL_REVENUE"), Decimal("0.00"))
    external_leaf_cost = sum((money(row["amount"]) for row in components if row["component_type"] == "EXTERNAL_LEAF_COST"), Decimal("0.00"))
    internal_eliminated = sum((money(row["amount"]) for row in components if row["component_type"] == "INTERNAL_ELIMINATION"), Decimal("0.00"))
    return {
        "external_revenue": money(external_revenue),
        "external_leaf_cost": money(external_leaf_cost),
        "internal_eliminated": money(internal_eliminated),
        "group_gross_margin": money(external_revenue - external_leaf_cost),
    }


def tax_snapshot(session: Session, *, period: date | str) -> dict[str, Any]:
    analysis_period = month_start(period)
    rows = session.execute(
        select(EntityVatLedger, CalculationRun)
        .join(CalculationRun, CalculationRun.id == EntityVatLedger.calculation_run_id)
        .join(
            TaxPeriodState,
            (TaxPeriodState.reporting_party_id == EntityVatLedger.reporting_party_id)
            & (TaxPeriodState.tax_type == "VAT")
            & (TaxPeriodState.tax_period == EntityVatLedger.tax_period)
            & (TaxPeriodState.current_run_id == EntityVatLedger.calculation_run_id),
        )
        .where(
            EntityVatLedger.tax_period == analysis_period,
            CalculationRun.run_status == "SUCCEEDED",
            CalculationRun.tax_type == "VAT",
        )
        .order_by(EntityVatLedger.reporting_party_id, EntityVatLedger.id)
    ).all()
    ledgers = [
        {
            "entity_vat_ledger_id": ledger.id,
            "reporting_party_id": ledger.reporting_party_id,
            "output_vat": str(money(ledger.output_vat)),
            "input_vat": str(money(ledger.input_vat)),
            "tax_prepayment": str(money(ledger.tax_prepayment)),
            "vat_payable_after_prepayment": str(money(ledger.vat_payable_after_prepayment)),
        }
        for ledger, _ in rows
    ]
    if not ledgers:
        raise GroupPenetrationError(
            f"no current official Entity VAT Ledgers for group TAX penetration in {analysis_period}"
        )
    return {"basis": "TAX", "analysis_period": str(analysis_period), "internal_elimination_applied": False, "ledgers": ledgers}


def summarize_tax(snapshot: dict[str, Any]) -> dict[str, Decimal]:
    ledgers = snapshot["ledgers"]
    return {
        "tax_output_vat": money(sum((money(row["output_vat"]) for row in ledgers), Decimal("0.00"))),
        "tax_input_vat": money(sum((money(row["input_vat"]) for row in ledgers), Decimal("0.00"))),
        "tax_prepayment": money(sum((money(row["tax_prepayment"]) for row in ledgers), Decimal("0.00"))),
        "tax_payable_after_prepayment": money(sum((money(row["vat_payable_after_prepayment"]) for row in ledgers), Decimal("0.00"))),
    }


def require_cash_penetration() -> None:
    raise CashBasisNotReady(
        "Task18 CASH group penetration requires canonical PaymentFact from Task19; "
        "legacy cashflows are not an allowed fallback"
    )
