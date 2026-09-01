"""Task31 deterministic Canonical four-flow graph read model.

The graph consumes Task30 explicit FactRelationship edges only.  It never
creates or infers an edge and deliberately exposes no invoice-payment amount
allocation because Task30 relationships do not carry a monetary split.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.v3_fact_models import Fact, FactRelationship
from app.v3_fact_relationship_evidence_models import FactRelationshipEvidence


CANONICAL_FOUR_FLOW_V1 = "CANONICAL_FOUR_FLOW_V1"
RELATIONSHIP_TYPES = frozenset({"INVOICE_FOR_CONTRACT", "PAYMENT_FOR_INVOICE", "PAYMENT_FOR_CONTRACT"})


class CanonicalFourFlow:
    def __init__(self, session: Session) -> None:
        self.session = session

    def read(self, project_id: int, *, eligible_fact_ids: list[int] | set[int]) -> dict[str, Any]:
        eligible = {int(value) for value in eligible_fact_ids}
        if not eligible:
            return self._empty(project_id)

        facts = {
            int(row.id): row
            for row in self.session.scalars(select(Fact).where(Fact.id.in_(eligible))).all()
        }
        relationships = list(
            self.session.scalars(
                select(FactRelationship)
                .where(
                    FactRelationship.relationship_type.in_(sorted(RELATIONSHIP_TYPES)),
                    or_(
                        FactRelationship.source_fact_id.in_(eligible),
                        FactRelationship.target_fact_id.in_(eligible),
                    ),
                )
                .order_by(FactRelationship.id)
            ).all()
        )
        relationship_ids = [int(row.id) for row in relationships]
        evidence_counts: dict[int, int] = defaultdict(int)
        if relationship_ids:
            for evidence in self.session.scalars(
                select(FactRelationshipEvidence).where(
                    FactRelationshipEvidence.relationship_id.in_(relationship_ids)
                )
            ).all():
                evidence_counts[int(evidence.relationship_id)] += 1

        in_project: list[dict[str, Any]] = []
        cross_project: list[int] = []
        missing_evidence: list[int] = []
        for relationship in relationships:
            source_id = int(relationship.source_fact_id)
            target_id = int(relationship.target_fact_id)
            if source_id not in eligible or target_id not in eligible:
                cross_project.append(int(relationship.id))
                continue
            source = facts.get(source_id) or self.session.get(Fact, source_id)
            target = facts.get(target_id) or self.session.get(Fact, target_id)
            count = evidence_counts[int(relationship.id)]
            if count == 0:
                missing_evidence.append(int(relationship.id))
            in_project.append(
                {
                    "relationship_id": int(relationship.id),
                    "relationship_type": str(relationship.relationship_type),
                    "source_fact_id": source_id,
                    "source_fact_type": str(source.fact_type) if source is not None else None,
                    "target_fact_id": target_id,
                    "target_fact_type": str(target.fact_type) if target is not None else None,
                    "reason": relationship.reason,
                    "evidence_count": count,
                }
            )

        contract_ids = {fact_id for fact_id, fact in facts.items() if fact.fact_type == "CONTRACT"}
        invoice_ids = {fact_id for fact_id, fact in facts.items() if fact.fact_type == "INVOICE"}
        payment_ids = {fact_id for fact_id, fact in facts.items() if fact.fact_type == "PAYMENT"}

        invoice_contract = [row for row in in_project if row["relationship_type"] == "INVOICE_FOR_CONTRACT"]
        payment_invoice = [row for row in in_project if row["relationship_type"] == "PAYMENT_FOR_INVOICE"]
        payment_contract = [row for row in in_project if row["relationship_type"] == "PAYMENT_FOR_CONTRACT"]

        invoice_with_contract = {row["source_fact_id"] for row in invoice_contract}
        invoice_with_payment = {row["target_fact_id"] for row in payment_invoice}
        payment_with_invoice = {row["source_fact_id"] for row in payment_invoice}
        payment_with_contract = {row["source_fact_id"] for row in payment_contract}
        contract_with_invoice = {row["target_fact_id"] for row in invoice_contract}
        contract_with_payment = {row["target_fact_id"] for row in payment_contract}

        quality: list[dict[str, Any]] = []
        quality.extend({"code": "RELATIONSHIP_EVIDENCE_MISSING", "relationship_id": rid} for rid in missing_evidence)
        quality.extend({"code": "CROSS_PROJECT_RELATIONSHIP_EXCLUDED", "relationship_id": rid} for rid in cross_project)
        quality.extend({"code": "INVOICE_WITHOUT_CONTRACT_EDGE", "fact_id": fid} for fid in sorted(invoice_ids - invoice_with_contract))
        quality.extend({"code": "PAYMENT_WITHOUT_EXPLICIT_EDGE", "fact_id": fid} for fid in sorted(payment_ids - payment_with_invoice - payment_with_contract))

        return {
            "ruleset_version": CANONICAL_FOUR_FLOW_V1,
            "project_id": int(project_id),
            "relationships": in_project,
            "coverage": {
                "contract_count": len(contract_ids),
                "invoice_count": len(invoice_ids),
                "payment_count": len(payment_ids),
                "invoice_contract_edge_count": len(invoice_contract),
                "payment_invoice_edge_count": len(payment_invoice),
                "payment_contract_edge_count": len(payment_contract),
                "contracts_with_invoice_link_count": len(contract_with_invoice),
                "contracts_with_payment_link_count": len(contract_with_payment),
                "invoices_with_contract_link_count": len(invoice_with_contract),
                "invoices_with_payment_link_count": len(invoice_with_payment),
                "payments_with_invoice_link_count": len(payment_with_invoice),
                "payments_with_contract_link_count": len(payment_with_contract),
                "unlinked_invoice_fact_ids": sorted(invoice_ids - invoice_with_contract),
                "unlinked_payment_fact_ids": sorted(payment_ids - payment_with_invoice - payment_with_contract),
            },
            "invoice_payment_amount_allocation": {
                "supported": False,
                "reason": "TASK30_RELATIONSHIP_HAS_NO_EXPLICIT_MONETARY_ALLOCATION",
            },
            "evidence_quality": quality,
        }

    @staticmethod
    def _empty(project_id: int) -> dict[str, Any]:
        return {
            "ruleset_version": CANONICAL_FOUR_FLOW_V1,
            "project_id": int(project_id),
            "relationships": [],
            "coverage": {
                "contract_count": 0,
                "invoice_count": 0,
                "payment_count": 0,
                "invoice_contract_edge_count": 0,
                "payment_invoice_edge_count": 0,
                "payment_contract_edge_count": 0,
                "contracts_with_invoice_link_count": 0,
                "contracts_with_payment_link_count": 0,
                "invoices_with_contract_link_count": 0,
                "invoices_with_payment_link_count": 0,
                "payments_with_invoice_link_count": 0,
                "payments_with_contract_link_count": 0,
                "unlinked_invoice_fact_ids": [],
                "unlinked_payment_fact_ids": [],
            },
            "invoice_payment_amount_allocation": {
                "supported": False,
                "reason": "TASK30_RELATIONSHIP_HAS_NO_EXPLICIT_MONETARY_ALLOCATION",
            },
            "evidence_quality": [],
        }
