"""Tax advisory read model with a hard FACT/ADVISORY boundary.

FACT values are accepted/current RAG PostgreSQL facts and represent events that
have already occurred. ADVISORY values are deterministic Tax-engine outputs and
must never overwrite or masquerade as FACT values.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.domain.tax_data_policy import (
    ADVISORY_SOURCE_TAX_ENGINE,
    DATA_CLASS_ADVISORY,
    DATA_CLASS_FACT,
    FACT_SOURCE_RAG_POSTGRESQL,
)

from .phase4_accounting import build_project_accounting


def _money(value: Any) -> Decimal:
    try:
        result = Decimal(str(value if value is not None else 0))
    except Exception:
        return Decimal("0.00")
    if not result.is_finite():
        return Decimal("0.00")
    return result.quantize(Decimal("0.01"))


def _tax_family(value: Any) -> str:
    token = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if token in {"VAT", "VALUE_ADDED_TAX", "增值税"} or "VALUE_ADDED" in token:
        return "VAT"
    if token in {
        "CIT",
        "CORPORATE_INCOME_TAX",
        "ENTERPRISE_INCOME_TAX",
        "企业所得税",
    } or "CORPORATE_INCOME" in token or "ENTERPRISE_INCOME" in token:
        return "CIT"
    return token or "OTHER"


def _period_date(period: str | None) -> date | None:
    if period is None:
        return None
    token = str(period).strip()
    if len(token) != 7 or token[4] != "-":
        raise ValueError("period must be YYYY-MM")
    try:
        parsed = date.fromisoformat(f"{token}-01")
    except ValueError as exc:
        raise ValueError("period must be YYYY-MM") from exc
    if parsed.strftime("%Y-%m") != token:
        raise ValueError("period must be YYYY-MM")
    return parsed


def actual_tax_payment_facts(
    db,
    project_id: int,
    *,
    reporting_party_id: int | None = None,
    period: str | None = None,
) -> dict[str, Any]:
    """Read actual tax payments accepted by the RAG canonical pipeline.

    A current/VALID TaxPrepaymentFact is an occurred tax fact for Tax purposes.
    Formal VAT/CIT gate state does not suppress or downgrade these facts.
    """
    tax_period = _period_date(period)
    sql = (
        "SELECT tpf.fact_id, tpf.project_id, tpf.reporting_party_id, "
        "tpf.tax_type, tpf.tax_period, tpf.tax_event_date, tpf.tax_amount, "
        "tpf.taxable_base, tpf.currency, tpf.event_type, tpf.source_system, "
        "tpf.external_reference, tpf.note, f.source_document_id, "
        "f.source_hash, f.fact_version "
        "FROM tax_prepayment_facts tpf "
        "JOIN facts f ON f.id=tpf.fact_id "
        "WHERE tpf.project_id=:project_id "
        "AND f.is_current=TRUE AND f.validation_status='VALID'"
    )
    params: dict[str, Any] = {"project_id": int(project_id)}
    if reporting_party_id is not None:
        sql += " AND tpf.reporting_party_id=:reporting_party_id"
        params["reporting_party_id"] = int(reporting_party_id)
    if tax_period is not None:
        sql += " AND tpf.tax_period=:tax_period"
        params["tax_period"] = tax_period
    sql += " ORDER BY tpf.tax_period, tpf.tax_event_date, tpf.fact_id"

    rows = db.execute(text(sql), params).mappings().all()
    items: list[dict[str, Any]] = []
    totals: dict[str, Decimal] = {}
    for row in rows:
        amount = _money(row["tax_amount"])
        family = _tax_family(row["tax_type"])
        totals[family] = totals.get(family, Decimal("0.00")) + amount
        items.append(
            {
                "fact_id": int(row["fact_id"]),
                "project_id": int(row["project_id"]),
                "reporting_party_id": int(row["reporting_party_id"]),
                "tax_type": str(row["tax_type"] or ""),
                "tax_family": family,
                "tax_period": row["tax_period"].isoformat() if row["tax_period"] else None,
                "tax_event_date": row["tax_event_date"].isoformat() if row["tax_event_date"] else None,
                "tax_amount": amount,
                "taxable_base": _money(row["taxable_base"]),
                "currency": str(row["currency"] or "CNY"),
                "event_type": str(row["event_type"] or ""),
                "source_system": str(row["source_system"] or ""),
                "external_reference": str(row["external_reference"] or ""),
                "note": str(row["note"] or ""),
                "source_document_id": int(row["source_document_id"] or 0) or None,
                "source_hash": str(row["source_hash"] or ""),
                "fact_version": int(row["fact_version"] or 0),
                "data_class": DATA_CLASS_FACT,
                "source": FACT_SOURCE_RAG_POSTGRESQL,
                "actual_occurred": True,
                "is_filing_basis": False,
            }
        )

    return {
        "data_class": DATA_CLASS_FACT,
        "source": FACT_SOURCE_RAG_POSTGRESQL,
        "actual_occurred": True,
        "is_filing_basis": False,
        "items": items,
        "totals_by_tax_family": {key: _money(value) for key, value in sorted(totals.items())},
        "count": len(items),
    }


def build_tax_advisory(
    db,
    project_id: int,
    *,
    reporting_party_id: int | None = None,
    period: str | None = None,
) -> dict[str, Any]:
    """Combine immutable RAG facts with deterministic Tax advisory calculations."""
    facts = actual_tax_payment_facts(
        db,
        int(project_id),
        reporting_party_id=reporting_party_id,
        period=period,
    )
    accounting = build_project_accounting(db, int(project_id))
    recognition = accounting.get("recognition") or {}
    book_tax = accounting.get("book_tax") or {}
    boundary = accounting.get("boundary") or {}

    advisory_revenue = _money(recognition.get("recognized_revenue"))
    advisory_cost = _money(recognition.get("recognized_cost"))
    advisory_profit = _money(book_tax.get("accounting_profit"))
    advisory_vat = _money(boundary.get("signed_vat_position"))
    advisory_cit = _money(book_tax.get("current_cit"))

    actual_vat = _money(facts["totals_by_tax_family"].get("VAT"))
    actual_cit = _money(facts["totals_by_tax_family"].get("CIT"))

    advisory = {
        "data_class": DATA_CLASS_ADVISORY,
        "source": ADVISORY_SOURCE_TAX_ENGINE,
        "actual_occurred": False,
        "is_filing_basis": False,
        "engine_version": accounting.get("engine_version"),
        "revenue": advisory_revenue,
        "cost": advisory_cost,
        "profit": advisory_profit,
        "vat": advisory_vat,
        "cit": advisory_cit,
        "vat_scope": "PROJECT_BOUNDARY",
        "cit_scope": "PROJECT_ADVISORY",
    }
    differences = {
        "data_class": DATA_CLASS_ADVISORY,
        "source": ADVISORY_SOURCE_TAX_ENGINE,
        "is_filing_basis": False,
        "vat_advisory_minus_actual_paid": _money(advisory_vat - actual_vat),
        "cit_advisory_minus_actual_paid": _money(advisory_cit - actual_cit),
    }

    return {
        "status": accounting.get("status", "READY"),
        "project_id": int(project_id),
        "reporting_party_id": reporting_party_id,
        "period": period,
        "principles": {
            "fact_source": FACT_SOURCE_RAG_POSTGRESQL,
            "advisory_source": ADVISORY_SOURCE_TAX_ENGINE,
            "tax_reads_source_files": False,
            "actual_tax_payments_are_facts": True,
            "advisory_never_overwrites_fact": True,
        },
        "actual_tax_payments": facts,
        "advisory": advisory,
        "differences": differences,
        "lineage": accounting.get("lineage") or {},
        "data_gaps": accounting.get("data_gaps") or [],
    }


__all__ = ["actual_tax_payment_facts", "build_tax_advisory"]
