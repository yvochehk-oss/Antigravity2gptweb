"""Canonical project summary read model.

All dynamic financial values exposed to project/dashboard consumers are derived
from accepted/current Canonical Facts and deterministic Tax engines. Project
ORM is used only for static master metadata.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import text

from ..domain.tax_data_policy import (
    ADVISORY_SOURCE_TAX_ENGINE,
    DATA_CLASS_ADVISORY,
    FACT_SOURCE_RAG_POSTGRESQL,
)
from ..models import Project
from .canonical_ssot import _decimal, _payload, consolidate_invoice_facts, load_current_facts


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _code(value: Any) -> str:
    return _clean(value).upper()


def resolve_project_transaction_price(
    db,
    project_id: int,
    *,
    contract_facts: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve project revenue transaction price only from RAG Canonical Facts.

    Resolution order is intentionally data-driven and non-crashing:
    1. explicit canonical contract_scope marker;
    2. ZB-* general/main-contract convention;
    3. largest external-boundary contract (degraded when ambiguity exists).

    Project master financial fields are intentionally not a fallback. When RAG
    PostgreSQL has no accepted/current contract-price fact, Tax reports EMPTY.
    """
    internal_codes = {
        _code(code)
        for code in db.execute(text("SELECT code FROM entities WHERE active = TRUE")).scalars().all()
        if code
    }
    facts = (
        list(contract_facts)
        if contract_facts is not None
        else load_current_facts(db, int(project_id), "contract")
    )
    candidates: list[dict[str, Any]] = []
    for fact in facts:
        payload = _payload(fact.get("payload"))
        contract_no = _clean(payload.get("contract_no") or fact.get("business_key"))
        amount = _decimal(payload.get("total_amount"))
        party_a = _code(payload.get("party_a_entity_code") or payload.get("party_a_code"))
        party_b = _code(payload.get("party_b_entity_code") or payload.get("party_b_code"))
        scope = _clean(payload.get("contract_scope")).upper()
        if amount <= 0:
            continue
        explicit_main = scope in {"PRIMARY_CUSTOMER_CONTRACT", "PROJECT_REVENUE"}
        zb_main = _code(contract_no).startswith("ZB-")
        crosses_boundary = bool(party_a and party_b) and ((party_a in internal_codes) != (party_b in internal_codes))
        candidates.append(
            {
                "fact_id": int(fact.get("fact_id") or 0),
                "fact_version": int(fact.get("fact_version") or 0),
                "contract_no": contract_no,
                "amount": amount,
                "explicit_main": explicit_main,
                "zb_main": zb_main,
                "crosses_boundary": crosses_boundary,
            }
        )

    def _pick(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return sorted(
            rows,
            key=lambda row: (row["amount"], row["fact_version"], row["fact_id"]),
            reverse=True,
        )[0]

    explicit = [row for row in candidates if row["explicit_main"]]
    if explicit:
        selected = _pick(explicit)
        status = "READY" if len(explicit) == 1 else "DEGRADED"
        reason = "EXPLICIT_SCOPE" if len(explicit) == 1 else "MULTIPLE_EXPLICIT_SCOPE_MAX_AMOUNT"
    else:
        zb_candidates = [row for row in candidates if row["zb_main"]]
        if zb_candidates:
            selected = _pick(zb_candidates)
            status = "READY" if len(zb_candidates) == 1 else "DEGRADED"
            reason = "ZB_PREFIX" if len(zb_candidates) == 1 else "MULTIPLE_ZB_MAX_AMOUNT"
        else:
            boundary = [row for row in candidates if row["crosses_boundary"]]
            if boundary:
                selected = _pick(boundary)
                status = "READY" if len(boundary) == 1 else "DEGRADED"
                reason = "SINGLE_BOUNDARY" if len(boundary) == 1 else "MULTIPLE_BOUNDARY_MAX_AMOUNT"
            else:
                return {
                    "amount": Decimal("0"),
                    "status": "EMPTY",
                    "source_fact_id": None,
                    "fact_version": None,
                    "contract_no": "",
                    "resolution_reason": "RAG_POSTGRESQL_NO_CONTRACT_PRICE_FACT",
                    "source": FACT_SOURCE_RAG_POSTGRESQL,
                }

    return {
        "amount": selected["amount"],
        "status": status,
        "source_fact_id": selected["fact_id"],
        "fact_version": selected["fact_version"],
        "contract_no": selected["contract_no"],
        "resolution_reason": reason,
        "source": FACT_SOURCE_RAG_POSTGRESQL,
    }


def payment_boundary(db, project_id: int) -> dict[str, Decimal]:
    internal_codes = {
        _code(code)
        for code in db.execute(text("SELECT code FROM entities WHERE active = TRUE")).scalars().all()
        if code
    }
    cash_in = Decimal("0")
    cash_out = Decimal("0")
    internal_eliminated = Decimal("0")
    for fact in load_current_facts(db, int(project_id), "payment"):
        payload = _payload(fact.get("payload"))
        payer = _code(payload.get("payer_entity_code") or payload.get("payer_code"))
        payee = _code(payload.get("payee_entity_code") or payload.get("payee_code"))
        amount = _decimal(payload.get("amount"))
        if amount <= 0 or not payer or not payee:
            continue
        payer_internal = payer in internal_codes
        payee_internal = payee in internal_codes
        if payer_internal and payee_internal:
            internal_eliminated += amount
        elif payer_internal and not payee_internal:
            cash_out += amount
        elif not payer_internal and payee_internal:
            cash_in += amount
    return {
        "external_cash_in": cash_in,
        "external_cash_out": cash_out,
        "internal_cash_eliminated": internal_eliminated,
    }


def canonical_project_summary(db, project_id: int) -> dict[str, Any]:
    project = db.get(Project, int(project_id))
    if project is None:
        raise LookupError(f"project not found: {project_id}")

    transaction = resolve_project_transaction_price(db, int(project_id))
    internal_codes = {
        _code(code)
        for code in db.execute(text("SELECT code FROM entities WHERE active = TRUE")).scalars().all()
        if code
    }
    invoice_facts = load_current_facts(db, int(project_id), "invoice")
    boundary = consolidate_invoice_facts(invoice_facts, internal_codes)
    cash = payment_boundary(db, int(project_id))

    from .phase4_accounting import build_project_accounting

    accounting = build_project_accounting(db, int(project_id))
    contract_total = _decimal(transaction["amount"])
    real_cost = _decimal(boundary.get("external_cost"))
    external_cash_out = _decimal(cash["external_cash_out"])
    recognized_revenue = _decimal(accounting["recognition"]["recognized_revenue"])
    accounting_profit = _decimal(accounting["book_tax"]["accounting_profit"])
    completion = _decimal(accounting["recognition"]["completion_percent"])

    vat = _decimal(boundary.get("signed_vat_position"))

    remaining_budget = max(Decimal("0"), contract_total - external_cash_out) if contract_total > 0 else Decimal("0")
    funding_progress = (external_cash_out / contract_total) if contract_total > 0 else Decimal("0")
    margin = (accounting_profit / recognized_revenue) if recognized_revenue else Decimal("0")

    return {
        "project": project,
        "contract_total": contract_total,
        "contract_amount": contract_total,
        "revenue": recognized_revenue,
        "real_cost": real_cost,
        "external_cash_cost": external_cash_out,
        "profit": accounting_profit,
        "margin": margin,
        "vat": vat,
        "vat_scope": "PROJECT_BOUNDARY",
        "vat_is_filing_basis": False,
        "boundary_output_vat": _decimal(boundary.get("boundary_output_vat")),
        "boundary_input_vat": _decimal(boundary.get("boundary_input_vat")),
        "deductible_input_vat": _decimal(boundary.get("deductible_input_vat")),
        "nondeductible_input_vat": _decimal(boundary.get("nondeductible_input_vat")),
        "pending_input_vat": _decimal(boundary.get("pending_input_vat")),
        "internal_eliminated_vat": _decimal(boundary.get("internal_eliminated_vat")),
        "input_vat_identity_ok": bool(boundary.get("input_vat_identity_ok")),
        "progress": completion,
        "eac": None,
        "eac_profit": None,
        "eac_efficiency": None,
        "eac_method": "canonical_phase4_accounting_v1",
        "remaining_budget": remaining_budget,
        "funding_progress": funding_progress,
        "boundary_real_cost": real_cost,
        "accounting_incurred_cost": _decimal(accounting["recognition"]["incurred_cost"]),
        "internal_eliminated": _decimal(boundary.get("internal_eliminated")),
        "external_cash_in": _decimal(cash["external_cash_in"]),
        "internal_cash_eliminated": _decimal(cash["internal_cash_eliminated"]),
        "source_of_truth": "analytics_canonical_facts_current",
        "fact_source": FACT_SOURCE_RAG_POSTGRESQL,
        "calculated_data_class": DATA_CLASS_ADVISORY,
        "calculated_source": ADVISORY_SOURCE_TAX_ENGINE,
        "legacy_tables_used": False,
        "transaction_price_source": {
            "status": transaction["status"],
            "fact_id": transaction["source_fact_id"],
            "fact_version": transaction["fact_version"],
            "contract_no": transaction["contract_no"],
            "resolution_reason": transaction.get("resolution_reason", ""),
            "source": transaction.get("source", FACT_SOURCE_RAG_POSTGRESQL),
        },
        "engine_version": accounting["engine_version"],
        "lineage": accounting["lineage"],
    }


__all__ = [
    "canonical_project_summary",
    "payment_boundary",
    "resolve_project_transaction_price",
]
