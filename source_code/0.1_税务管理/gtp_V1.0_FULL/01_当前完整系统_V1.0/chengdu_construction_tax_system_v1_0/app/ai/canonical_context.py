"""Task31 Native V3 RAG context builder.

This module intentionally imports no legacy Contract/Invoice/CashFlow/Budget/
Progress/RealCost models.  Canonical mode fails closed and reports unavailable
legacy-only scopes instead of reading them as a fallback.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.constants import SCOPES
from app.domain.finance.canonical_four_flow import CanonicalFourFlow
from app.domain.finance.canonical_project_finance import CanonicalProjectFinance
from app.domain.tax.canonical_project_tax import CanonicalProjectTax
from app.models import Project


CANONICAL_RAG_CONTEXT_V1 = "CANONICAL_RAG_CONTEXT_V1"
LEGACY_ONLY_SCOPES = frozenset({"budget", "cost", "eac", "risk"})


def _project_payload(project: Project) -> dict[str, Any]:
    return {
        "id": int(project.id),
        "code": project.code,
        "project_code": project.project_code,
        "name": project.name,
        "city": project.city,
        "tax_method": project.tax_method,
    }


def _unavailable(scope: str) -> dict[str, str]:
    return {
        "status": "NOT_AVAILABLE_IN_CANONICAL_V3",
        "scope": scope,
        "reason": "NO_CANONICAL_SOURCE",
    }


def build_canonical_context(db: Session, project_id: int, scope: str) -> dict[str, Any]:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError("项目不存在")

    finance = CanonicalProjectFinance(db).read(project_id)
    graph = CanonicalFourFlow(db).read(project_id, eligible_fact_ids=finance["eligible_fact_ids"])
    quality = [*finance["evidence_quality"], *graph["evidence_quality"]]

    base: dict[str, Any] = {
        "context_version": CANONICAL_RAG_CONTEXT_V1,
        "project": _project_payload(project),
        "scope": scope,
        "scope_name": SCOPES.get(scope, scope),
        "finance_summary": finance["summary"],
        "relationship_coverage": graph["coverage"],
        "evidence_quality": quality,
    }

    if scope in LEGACY_ONLY_SCOPES:
        base["availability"] = _unavailable(scope)
        return base

    if scope in {"overview", "whole_project"}:
        base["contracts"] = finance["contracts"]
        base["invoices"] = finance["invoices"]
        base["cashflows"] = finance["payments"]
        base["four_flow"] = graph

    if scope == "contract":
        base["contracts"] = finance["contracts"]
        base["four_flow_relationships"] = [
            row for row in graph["relationships"] if row["relationship_type"] in {"INVOICE_FOR_CONTRACT", "PAYMENT_FOR_CONTRACT"}
        ]
    elif scope == "invoice":
        base["invoices"] = finance["invoices"]
        base["four_flow_relationships"] = [
            row for row in graph["relationships"] if row["relationship_type"] in {"INVOICE_FOR_CONTRACT", "PAYMENT_FOR_INVOICE"}
        ]
    elif scope == "cashflow":
        base["cashflows"] = finance["payments"]
        base["four_flow_relationships"] = [
            row for row in graph["relationships"] if row["relationship_type"] in {"PAYMENT_FOR_INVOICE", "PAYMENT_FOR_CONTRACT"}
        ]
    elif scope == "fulfillment":
        base["availability"] = {
            "status": "NOT_INCLUDED_IN_TASK31_FOUR_FLOW_V1",
            "scope": scope,
            "reason": "FULFILLMENT_IS_CANONICAL_BUT_OUTSIDE_TASK31_FINANCE_GRAPH_SCOPE",
        }
    elif scope in {"material", "labor", "equipment", "subcontract"}:
        base["availability"] = {
            "status": "NOT_AVAILABLE_IN_CANONICAL_V3",
            "scope": scope,
            "reason": "NO_RELIABLE_CANONICAL_CATEGORY_ATTRIBUTION",
        }

    if scope in {"tax", "whole_project"}:
        base["tax_analysis"] = CanonicalProjectTax(db).read(project_id)

    return base
