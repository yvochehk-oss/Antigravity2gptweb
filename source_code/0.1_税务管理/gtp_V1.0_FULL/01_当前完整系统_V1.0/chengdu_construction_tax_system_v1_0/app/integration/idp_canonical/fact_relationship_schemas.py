"""Task30 DTO contracts for explicit Canonical Fact relationships."""
from __future__ import annotations

from enum import Enum
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExplicitRelationshipType(str, Enum):
    INVOICE_FOR_CONTRACT = "INVOICE_FOR_CONTRACT"
    PAYMENT_FOR_INVOICE = "PAYMENT_FOR_INVOICE"
    PAYMENT_FOR_CONTRACT = "PAYMENT_FOR_CONTRACT"


class RelationshipCompletionOutcome(str, Enum):
    CREATED = "CREATED"
    NOOP = "NOOP"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class FactRelationshipEvidenceInput(BaseModel):
    """One explicit target reference.

    Exactly one target locator is accepted. There are deliberately no project,
    party, amount, date, name, similarity, or LLM hint fields.
    """

    model_config = ConfigDict(extra="forbid")

    target_fact_id: int | None = Field(default=None, gt=0)
    business_identity_key: str | None = Field(default=None, max_length=240)
    invoice_identity_key: str | None = Field(default=None, max_length=240)
    contract_business_identity_key: str | None = Field(default=None, max_length=240)
    evidence_text: str = Field(min_length=1)
    page_no: int | None = Field(default=None, ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator(
        "business_identity_key",
        "invoice_identity_key",
        "contract_business_identity_key",
    )
    @classmethod
    def _key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = unicodedata.normalize("NFKC", value).strip()
        if not cleaned:
            raise ValueError("explicit identity key must not be blank")
        return cleaned

    @field_validator("evidence_text")
    @classmethod
    def _text(cls, value: str) -> str:
        cleaned = unicodedata.normalize("NFKC", value).strip()
        if not cleaned:
            raise ValueError("evidence_text must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _exactly_one_reference(self) -> "FactRelationshipEvidenceInput":
        refs = (
            self.target_fact_id,
            self.business_identity_key,
            self.invoice_identity_key,
            self.contract_business_identity_key,
        )
        if sum(value is not None for value in refs) != 1:
            raise ValueError("exactly one explicit target reference is required")
        return self


class FactRelationshipCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_fact_id: int = Field(gt=0)
    relationship_type: ExplicitRelationshipType
    source_document_id: int = Field(
        gt=0,
        description="Canonical SourceDocument.id integer FK; never an IDP UUID",
    )
    source_extraction_id: str = Field(min_length=1, max_length=160)
    submitted_by: str = Field(min_length=1, max_length=80)
    evidences: list[FactRelationshipEvidenceInput] = Field(min_length=1, max_length=50)

    @field_validator("source_extraction_id", "submitted_by")
    @classmethod
    def _required_text(cls, value: str) -> str:
        cleaned = unicodedata.normalize("NFKC", value).strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned


class FactRelationshipReason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    detail: str


class FactRelationshipCompletionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: RelationshipCompletionOutcome
    source_fact_id: int
    source_fact_type: str
    target_fact_id: int | None = None
    target_fact_type: str | None = None
    relationship_id: int | None = None
    relationship_type: ExplicitRelationshipType
    evidence_ids: list[int] = Field(default_factory=list)
    ruleset_version: str
    semantic_version: str
    source_business_identity_key: str
    source_version_no: int
    source_validation_status: str
    reasons: list[FactRelationshipReason] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> "FactRelationshipCompletionResult":
        if self.outcome == RelationshipCompletionOutcome.NEEDS_REVIEW:
            if self.relationship_id is not None or self.evidence_ids:
                raise ValueError("NEEDS_REVIEW must expose zero writes")
            if not self.reasons:
                raise ValueError("NEEDS_REVIEW requires a reason")
        else:
            if self.relationship_id is None or self.target_fact_id is None:
                raise ValueError("successful completion requires relationship/target ids")
            if self.reasons:
                raise ValueError("successful completion must not carry failure reasons")
        return self
