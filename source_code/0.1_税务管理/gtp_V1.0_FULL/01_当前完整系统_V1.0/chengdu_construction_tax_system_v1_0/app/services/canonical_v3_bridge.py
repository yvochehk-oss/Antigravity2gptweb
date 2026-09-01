"""Read-only compatibility bridge from legacy V3 API shapes to Canonical Facts.

The historical ``facts`` / ``invoice_facts`` / ``contract_facts`` /
``payment_facts`` tables remain available for audit and reconciliation only.
Every production read generated here starts from ``canonical_facts``.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from ..models import Project
from .canonical_ledger import project_counterparties, project_ledger_bundle
from .canonical_ssot import load_current_facts
from .phase4_accounting import build_project_accounting


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _payload(fact: dict[str, Any]) -> dict[str, Any]:
    value = fact.get("payload") or {}
    return value if isinstance(value, dict) else {}


class CanonicalV3Bridge:
    """Preserve V3 read endpoints while removing their second fact source."""

    def __init__(self, db) -> None:
        self.db = db

    def _project(self, project_id: int) -> Project:
        project = self.db.get(Project, int(project_id))
        if project is None:
            raise LookupError(f"project not found: {project_id}")
        return project

    def finance(self, project_id: int) -> dict[str, Any]:
        self._project(project_id)
        ledger = project_ledger_bundle(self.db, int(project_id))
        accounting = build_project_accounting(self.db, int(project_id))
        contracts = ledger["contracts"]
        invoices = ledger["invoices"]
        payments = ledger["cash_flows"]
        return {
            "project_id": int(project_id),
            "data_source": "CANONICAL_FACTS",
            "source_of_truth": "canonical_facts",
            "legacy_v3_facts_used": False,
            "contract_amount": sum(_d(row.get("amount")) for row in contracts),
            "invoice_net": sum(_d(row.get("net_amount")) for row in invoices),
            "invoice_vat": sum(_d(row.get("vat_amount")) for row in invoices),
            "payment_amount": sum(_d(row.get("amount")) for row in payments),
            "external_cost": _d(accounting["boundary"].get("external_cost")),
            "external_revenue": _d(accounting["boundary"].get("external_revenue")),
            "internal_eliminated": _d(accounting["boundary"].get("internal_eliminated")),
            "accounting_profit": _d(accounting["book_tax"].get("accounting_profit")),
            "eligible_fact_ids": [item["fact_id"] for item in accounting["lineage"]["fact_versions"]],
            "evidence_quality": {
                "accepted_current_fact_count": len(accounting["lineage"]["fact_versions"]),
                "fact_snapshot_hash": accounting["lineage"]["fact_snapshot_hash"],
            },
            "lineage": accounting["lineage"],
        }

    def four_flow(self, project_id: int) -> dict[str, Any]:
        self._project(project_id)
        ledger = project_ledger_bundle(self.db, int(project_id))
        facts = load_current_facts(self.db, int(project_id))
        contracts_by_no: dict[str, list[int]] = {}
        invoices_by_contract: dict[str, list[int]] = {}
        payments_by_contract: dict[str, list[int]] = {}
        for fact in facts:
            payload = _payload(fact)
            fact_type = str(fact.get("fact_type") or "")
            contract_no = str(payload.get("contract_no") or "").strip()
            fact_id = int(fact.get("id") or 0)
            if not contract_no:
                continue
            if fact_type == "contract":
                contracts_by_no.setdefault(contract_no, []).append(fact_id)
            elif fact_type == "invoice":
                invoices_by_contract.setdefault(contract_no, []).append(fact_id)
            elif fact_type == "payment":
                payments_by_contract.setdefault(contract_no, []).append(fact_id)
        linked_contracts = set(contracts_by_no) | set(invoices_by_contract) | set(payments_by_contract)
        relations = [
            {
                "contract_no": contract_no,
                "contract_fact_ids": contracts_by_no.get(contract_no, []),
                "invoice_fact_ids": invoices_by_contract.get(contract_no, []),
                "payment_fact_ids": payments_by_contract.get(contract_no, []),
            }
            for contract_no in sorted(linked_contracts)
        ]
        return {
            "project_id": int(project_id),
            "data_source": "CANONICAL_FACTS",
            "source_of_truth": "canonical_facts",
            "legacy_relationship_graph_used": False,
            "contracts": ledger["contracts"],
            "invoices": ledger["invoices"],
            "payments": ledger["cash_flows"],
            "relationships": relations,
            "coverage": {
                "contract_keys": len(contracts_by_no),
                "invoice_contract_keys": len(invoices_by_contract),
                "payment_contract_keys": len(payments_by_contract),
                "linked_business_keys": len(linked_contracts),
            },
            "invoice_payment_amount_allocation": {
                "mode": "canonical_business_key_read_only",
                "legacy_allocation_table_used": False,
            },
            "evidence_quality": {
                "accepted_current_fact_count": len(facts),
            },
        }

    def tax(
        self,
        project_id: int,
        *,
        reporting_party_id: int | None = None,
        tax_period: date | None = None,
    ) -> dict[str, Any]:
        self._project(project_id)
        accounting = build_project_accounting(self.db, int(project_id))
        return {
            "project_id": int(project_id),
            "data_source": "CANONICAL_FACTS",
            "source_of_truth": "canonical_facts",
            "legacy_v3_facts_used": False,
            "reporting_party_id": reporting_party_id,
            "tax_period": tax_period,
            "recognition": accounting["recognition"],
            "accruals": accounting["accruals"],
            "book_tax": accounting["book_tax"],
            "lineage": accounting["lineage"],
        }

    def evidence_quality(self, project_id: int) -> dict[str, Any]:
        self._project(project_id)
        facts = load_current_facts(self.db, int(project_id))
        with_evidence = sum(1 for fact in facts if fact.get("evidence"))
        return {
            "project_id": int(project_id),
            "source_of_truth": "canonical_facts",
            "accepted_current_fact_count": len(facts),
            "with_evidence_count": with_evidence,
            "without_evidence_count": len(facts) - with_evidence,
            "coverage_ratio": (with_evidence / len(facts)) if facts else 1.0,
        }

    def rag_context(self, project_id: int, *, scope: str = "whole_project") -> dict[str, Any]:
        self._project(project_id)
        facts = load_current_facts(self.db, int(project_id))
        return {
            "project_id": int(project_id),
            "scope": scope,
            "data_source": "CANONICAL_FACTS",
            "source_of_truth": "canonical_facts",
            "facts": [
                {
                    "fact_id": int(fact.get("id") or 0),
                    "fact_type": str(fact.get("fact_type") or ""),
                    "business_key": str(fact.get("business_key") or ""),
                    "fact_version": int(fact.get("fact_version") or 0),
                    "payload": _payload(fact),
                    "evidence": fact.get("evidence") or {},
                }
                for fact in facts
            ],
            "availability": "AVAILABLE" if facts else "NO_CANONICAL_FACTS",
        }

    def counterparties(self, project_id: int) -> dict[str, Any]:
        self._project(project_id)
        return project_counterparties(self.db, int(project_id))


__all__ = ["CanonicalV3Bridge"]
