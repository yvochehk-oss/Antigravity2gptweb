"""Task26 Contract Role & Semantic Completion DTOs."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SourcePartyRole = Literal["PARTY_A", "PARTY_B"]
CanonicalContractRole = Literal["BUYER", "SELLER"]
ContractRoleEvidenceType = Literal["LEGAL_ROLE_LABEL", "CONTRACT_CLAUSE_ROLE"]
RoleClassificationStatus = Literal["MAPPED", "UNSUPPORTED", "AMBIGUOUS", "INVALID"]
RoleResolutionStatus = Literal["RESOLVED", "NEEDS_REVIEW"]
RoleCompletionOutcome = Literal["UPDATED", "NOOP"]
EvidenceWriteOutcome = Literal["CREATED", "NOOP"]
ResolutionWriteOutcome = Literal["CREATED", "SUPERSEDED", "NOOP"]


class ContractRoleEvidenceInput(BaseModel):
    """One explicit legal-role statement for a role-neutral Task24 participant."""

    model_config = ConfigDict(extra="forbid")

    source_party_role: SourcePartyRole
    evidence_type: ContractRoleEvidenceType = "LEGAL_ROLE_LABEL"
    legal_role_label: str = Field(min_length=1, max_length=160)
    evidence_text: str = Field(min_length=1)
    page_no: int | None = Field(default=None, ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("legal_role_label", "evidence_text")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("explicit legal-role evidence must not be empty")
        return normalized


class ContractRoleCompletionRequest(BaseModel):
    """Complete roles for one successful Task24 Contract receipt."""

    model_config = ConfigDict(extra="forbid")

    source_system: Literal["IDP"] = "IDP"
    source_document_id: str = Field(min_length=1, max_length=160)
    source_extraction_id: str = Field(min_length=1, max_length=160)
    document_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    submitted_by: str = Field(min_length=1, max_length=80)
    evidences: list[ContractRoleEvidenceInput] = Field(
        default_factory=list,
        max_length=50,
    )

    @field_validator("source_document_id", "source_extraction_id", "submitted_by")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("document_sha256")
    @classmethod
    def _lower_sha(cls, value: str) -> str:
        return value.lower()


class ContractRoleFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ContractRoleCompletionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: RoleCompletionOutcome
    receipt_id: int
    fact_id: int
    business_identity_key: str
    version_no: int
    validation_status: str
    resolution_id: int
    resolution_seq: int
    resolution_status: RoleResolutionStatus
    buyer_party_id: int | None = None
    seller_party_id: int | None = None
    ruleset_version: str
    evidence_outcome: EvidenceWriteOutcome
    resolution_outcome: ResolutionWriteOutcome
    evidence_ids: list[int] = Field(default_factory=list)
    evidence_set_fingerprint: str
    reason_code: str
    reason_detail: str
    findings: list[ContractRoleFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_resolution_shape(self) -> "ContractRoleCompletionResult":
        if self.resolution_status == "RESOLVED":
            if self.buyer_party_id is None or self.seller_party_id is None:
                raise ValueError(
                    "RESOLVED requires buyer_party_id and seller_party_id"
                )
            if self.buyer_party_id == self.seller_party_id:
                raise ValueError("buyer and seller must be distinct")
        elif self.buyer_party_id is not None or self.seller_party_id is not None:
            raise ValueError("NEEDS_REVIEW must remain role-neutral")
        return self
