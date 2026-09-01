"""Task28 Payment source-evidence completion DTOs."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .evidence_schemas import SourceDocumentEvidence

PaymentEvidenceOutcome = Literal["COMPLETED", "NOOP"]
PaymentValidationStatus = Literal["VALID", "NEEDS_REVIEW", "INVALID"]

class PaymentEvidenceCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_system: Literal["IDP"] = "IDP"
    source_document_id: str = Field(min_length=1, max_length=160)
    source_extraction_id: str = Field(min_length=1, max_length=160)
    document_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    document: SourceDocumentEvidence
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    @model_validator(mode="after")
    def _page_bounds(self) -> "PaymentEvidenceCompletionRequest":
        if self.page_start is not None and self.page_end is not None and self.page_end < self.page_start:
            raise ValueError("page_end must be >= page_start")
        return self

class PaymentEvidenceCompletionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: PaymentEvidenceOutcome
    receipt_id: int
    fact_id: int
    business_identity_key: str
    source_document_pk: int
    binding_id: int
    document_outcome: str
    provenance_outcome: str
    task19_ruleset_version: str
    validation_status: PaymentValidationStatus
    reason: str
