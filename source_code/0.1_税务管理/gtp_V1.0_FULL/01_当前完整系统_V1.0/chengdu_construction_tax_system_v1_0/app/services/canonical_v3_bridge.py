"""Read-only compatibility bridge from legacy V3 API shapes to Canonical Facts.

The historical ``facts`` / ``invoice_facts`` / ``contract_facts`` /
``payment_facts`` tables remain available for audit and reconciliation only.
Every production business-fact read generated here starts from
``canonical_facts``. Legal-entity VAT results are read from the deterministic
V3 calculation ledger, not recomputed from project aggregates.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text

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


def _fact_id(fact: dict[str, Any]) -> int:
    return int(fact.get("fact_id") or fact.get("id") or 0)


class CanonicalV3Bridge:
    """Preserve V3 read endpoints while removing their second business fact source."""

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
            "eligible_fact_ids": [
                item["fact_id"] for item in accounting["lineage"]["fact_versions"]
            ],
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
            fact_id = _fact_id(fact)
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

    @staticmethod
    def list_official_entity_vat_ledgers(
        db,
        *,
        entity_code: str | None = None,
        tax_period: date | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """List current SUCCEEDED statutory VAT ledgers without N+1 ledger reads.

        ``internal_entities.party_id`` is the reporting identity.  The legacy
        ``entities.id`` value is deliberately never used as a substitute.
        Lineage is returned from typed ``entity_vat_ledger_components`` rows;
        source identifiers are exposed only when the database actually stores
        them.
        """
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 100)
        where = [
            "r.tax_type = 'VAT'",
            "r.run_status = 'SUCCEEDED'",
            "ie.legal_entity = TRUE",
            "ie.active = TRUE",
            "p.party_type = 'internal'",
            "p.active = TRUE",
        ]
        params: dict[str, Any] = {}
        wanted_code = str(entity_code or "").strip().upper()
        if wanted_code:
            where.append("upper(ie.canonical_code) = :entity_code")
            params["entity_code"] = wanted_code
        if tax_period is not None:
            where.append("l.tax_period = :tax_period")
            params["tax_period"] = tax_period
        where_sql = " AND ".join(where)
        base_from = """
            FROM entity_vat_ledgers AS l
            JOIN calculation_runs AS r
              ON r.id = l.calculation_run_id
            JOIN tax_period_states AS s
              ON s.reporting_party_id = l.reporting_party_id
             AND s.tax_type = 'VAT'
             AND s.tax_period = l.tax_period
             AND s.current_run_id = l.calculation_run_id
            JOIN internal_entities AS ie
              ON ie.party_id = l.reporting_party_id
            JOIN parties AS p
              ON p.id = ie.party_id
        """
        total = int(
            db.execute(
                text(f"SELECT count(*) {base_from} WHERE {where_sql}"),
                params,
            ).scalar_one()
        )
        if total == 0:
            return [], 0

        rows = db.execute(
            text(
                f"""
                SELECT
                    l.id,
                    l.calculation_run_id,
                    l.reporting_party_id,
                    l.tax_period,
                    l.opening_input_credit,
                    l.output_vat,
                    l.input_vat,
                    l.tax_prepayment,
                    l.vat_payable_before_prepayment,
                    l.closing_input_credit,
                    l.vat_payable_after_prepayment,
                    l.unapplied_tax_prepayment,
                    r.run_kind,
                    r.run_status,
                    r.ruleset_version,
                    r.input_snapshot_sha256,
                    r.result_sha256,
                    s.state AS period_state,
                    ie.party_id AS entity_id,
                    ie.canonical_code AS entity_code,
                    ie.business_role,
                    ie.legal_entity,
                    p.name AS entity_name
                {base_from}
                WHERE {where_sql}
                ORDER BY l.tax_period DESC, ie.canonical_code, l.calculation_run_id DESC
                OFFSET :offset_rows LIMIT :limit_rows
                """
            ),
            {
                **params,
                "offset_rows": (page - 1) * page_size,
                "limit_rows": page_size,
            },
        ).mappings().all()

        ledger_ids = [int(row["id"]) for row in rows]
        lineage_by_ledger: dict[int, list[dict[str, Any]]] = {ledger_id: [] for ledger_id in ledger_ids}
        if ledger_ids:
            lineage_rows = db.execute(
                text(
                    """
                    SELECT
                        c.ledger_id,
                        c.id AS component_id,
                        c.component_type,
                        c.amount,
                        c.output_vat_event_id,
                        c.input_vat_claim_id,
                        c.tax_prepayment_fact_id,
                        c.prior_ledger_id,
                        c.opening_balance_seed_id,
                        ove.invoice_fact_id AS output_invoice_fact_id,
                        ove.source_document_id AS output_source_document_id,
                        ivc.invoice_fact_id AS input_invoice_fact_id,
                        ivc.source_document_id AS input_source_document_id,
                        seed.source_document_id AS opening_source_document_id
                    FROM entity_vat_ledger_components AS c
                    LEFT JOIN output_vat_events AS ove
                      ON ove.id = c.output_vat_event_id
                    LEFT JOIN input_vat_claims AS ivc
                      ON ivc.id = c.input_vat_claim_id
                    LEFT JOIN vat_opening_balance_seeds AS seed
                      ON seed.id = c.opening_balance_seed_id
                    WHERE c.ledger_id = ANY(CAST(:ledger_ids AS INTEGER[]))
                    ORDER BY c.ledger_id, c.id
                    """
                ),
                {"ledger_ids": ledger_ids},
            ).mappings().all()
            for component in lineage_rows:
                lineage_by_ledger[int(component["ledger_id"])].append(dict(component))

        items: list[dict[str, Any]] = []
        for row in rows:
            result = dict(row)
            payable_before = _d(result["vat_payable_before_prepayment"])
            expected_payable = max(
                _d(result["output_vat"])
                - _d(result["input_vat"])
                - _d(result["opening_input_credit"]),
                Decimal("0"),
            )
            closing_credit = _d(result["closing_input_credit"])
            identity_ok = (
                payable_before == expected_payable
                and not (payable_before > 0 and closing_credit > 0)
            )
            result.update(
                {
                    "id": str(result["id"]),
                    "period": result["tax_period"].strftime("%Y-%m"),
                    "scope": "LEGAL_ENTITY_STATUTORY",
                    "is_filing_basis": True,
                    "source_of_truth": "entity_vat_ledgers",
                    "legal_entity_vat_identity_ok": identity_ok,
                    "data_status": "READY" if identity_ok else "DEGRADED",
                    "data_gaps": [] if identity_ok else ["LEGAL_ENTITY_VAT_IDENTITY_FAILED"],
                    "trusted": identity_ok,
                    "lineage_components": lineage_by_ledger.get(int(row["id"]), []),
                }
            )
            items.append(result)
        return items, total

    def _official_entity_vat_ledger(
        self,
        reporting_party_id: int,
        tax_period: date | None,
    ) -> dict[str, Any] | None:
        """Read the latest current SUCCEEDED statutory VAT ledger for one party."""
        entity_code = self.db.execute(
            text(
                """
                SELECT ie.canonical_code
                FROM internal_entities AS ie
                JOIN parties AS p ON p.id = ie.party_id
                WHERE ie.party_id = :reporting_party_id
                  AND ie.legal_entity = TRUE
                  AND ie.active = TRUE
                  AND p.party_type = 'internal'
                  AND p.active = TRUE
                """
            ),
            {"reporting_party_id": int(reporting_party_id)},
        ).scalar_one_or_none()
        if entity_code is None:
            return None
        items, _ = self.list_official_entity_vat_ledgers(
            self.db,
            entity_code=str(entity_code),
            tax_period=tax_period,
            page=1,
            page_size=1,
        )
        return items[0] if items else None

    def tax(
        self,
        project_id: int,
        *,
        reporting_party_id: int | None = None,
        tax_period: date | None = None,
    ) -> dict[str, Any]:
        self._project(project_id)
        accounting = build_project_accounting(self.db, int(project_id))
        official_vat = (
            self._official_entity_vat_ledger(reporting_party_id, tax_period)
            if reporting_party_id is not None
            else None
        )
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
            "entity_vat_ledger": official_vat,
            "entity_vat_ledger_status": (
                "READY"
                if official_vat is not None and bool(official_vat.get("legal_entity_vat_identity_ok"))
                else "DEGRADED"
                if official_vat is not None
                else "NOT_REQUESTED"
                if reporting_party_id is None
                else "NO_CURRENT_SUCCEEDED_LEDGER"
            ),
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
                    "fact_id": _fact_id(fact),
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
