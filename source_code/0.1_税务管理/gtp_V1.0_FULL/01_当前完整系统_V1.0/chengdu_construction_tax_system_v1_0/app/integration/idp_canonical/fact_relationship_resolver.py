"""Task30 exact-only Canonical Fact target resolver."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...v3_fact_models import Fact, InvoiceFact
from .fact_relationship_rules import reference_descriptor
from .fact_relationship_schemas import FactRelationshipEvidenceInput


class FactRelationshipResolutionError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ResolvedFactTarget:
    fact: Fact
    reference_type: str
    reference_value: str


class FactRelationshipResolver:
    """Resolve only exact canonical identifiers and equality predicates.

    The resolver surface intentionally contains no heuristic matching inputs.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _one(rows: list[Fact], *, reference_type: str, reference_value: str) -> Fact:
        if not rows:
            raise FactRelationshipResolutionError(
                "TARGET_NOT_FOUND",
                f"{reference_type} did not resolve to a current Canonical Fact",
            )
        if len(rows) != 1:
            raise FactRelationshipResolutionError(
                "TARGET_AMBIGUOUS",
                f"{reference_type} resolved to more than one current Canonical Fact",
            )
        return rows[0]

    def resolve(self, evidence: FactRelationshipEvidenceInput) -> ResolvedFactTarget:
        reference_type, reference_value = reference_descriptor(evidence)

        if reference_type == "TARGET_FACT_ID":
            rows = list(
                self.session.scalars(
                    select(Fact)
                    .where(
                        Fact.id == int(reference_value),
                        Fact.is_current.is_(True),
                        Fact.validation_status != "SUPERSEDED",
                    )
                    .with_for_update(read=True)
                ).all()
            )
        elif reference_type == "BUSINESS_IDENTITY_KEY":
            rows = list(
                self.session.scalars(
                    select(Fact)
                    .where(
                        Fact.business_identity_key == reference_value,
                        Fact.is_current.is_(True),
                        Fact.validation_status != "SUPERSEDED",
                    )
                    .with_for_update(read=True)
                ).all()
            )
        elif reference_type == "INVOICE_IDENTITY_KEY":
            rows = list(
                self.session.scalars(
                    select(Fact)
                    .join(InvoiceFact, InvoiceFact.fact_id == Fact.id)
                    .where(
                        InvoiceFact.invoice_identity_key == reference_value,
                        Fact.fact_type == "INVOICE",
                        Fact.is_current.is_(True),
                        Fact.validation_status != "SUPERSEDED",
                    )
                    .with_for_update(read=True)
                ).all()
            )
        elif reference_type == "CONTRACT_BUSINESS_IDENTITY_KEY":
            rows = list(
                self.session.scalars(
                    select(Fact)
                    .where(
                        Fact.business_identity_key == reference_value,
                        Fact.fact_type == "CONTRACT",
                        Fact.is_current.is_(True),
                        Fact.validation_status != "SUPERSEDED",
                    )
                    .with_for_update(read=True)
                ).all()
            )
        else:  # pragma: no cover - schema/rules prevent this
            raise FactRelationshipResolutionError(
                "REFERENCE_TYPE_UNSUPPORTED",
                f"unsupported reference type {reference_type}",
            )

        fact = self._one(rows, reference_type=reference_type, reference_value=reference_value)
        return ResolvedFactTarget(
            fact=fact,
            reference_type=reference_type,
            reference_value=reference_value,
        )
