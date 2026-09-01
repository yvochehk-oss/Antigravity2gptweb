"""Task29 schemas for explicit Canonical Fact -> Project attribution.

The request surface intentionally accepts only ``project_code`` as the project
locator.  There is no project-name field, fuzzy hint, relationship hint, or
amount-direction hint: callers must supply explicit, reviewable project
identity evidence.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProjectAttributionOutcome(str, Enum):
    CREATED = "CREATED"
    NOOP = "NOOP"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ProjectAllocationInput(BaseModel):
    """One explicit project allocation item.

    Invoice allocations use the three amount components. Payment/Contract
    allocations use ``allocated_amount``.  A single allocation may omit all
    amounts, in which case Task29 deterministically uses the whole Fact basis.
    """

    model_config = ConfigDict(extra="forbid")

    project_code: str = Field(min_length=1, max_length=160)
    allocated_net: Decimal | None = None
    allocated_vat: Decimal | None = None
    allocated_gross: Decimal | None = None
    allocated_amount: Decimal | None = None
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("project_code")
    @classmethod
    def _project_code(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).strip()
        if not normalized:
            raise ValueError("project_code must not be blank")
        return normalized

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = unicodedata.normalize("NFKC", value).strip()
        return normalized or None


class ProjectAttributionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: int = Field(gt=0)
    allocations: list[ProjectAllocationInput] = Field(min_length=1, max_length=50)
    source_document_id: int | None = Field(default=None, gt=0)
    reviewed_by: str = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("reviewed_by")
    @classmethod
    def _reviewed_by(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).strip()
        if not normalized:
            raise ValueError("reviewed_by must not be blank")
        return normalized

    @field_validator("note")
    @classmethod
    def _request_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = unicodedata.normalize("NFKC", value).strip()
        return normalized or None


class ProjectAttributionReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    detail: str


class ProjectAttributionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: ProjectAttributionOutcome
    fact_id: int
    fact_type: str
    business_identity_key: str
    version_no: int
    validation_status: str
    ruleset_version: str
    basis_version: str | None = None
    allocation_ids: list[int] = Field(default_factory=list)
    project_codes: list[str] = Field(default_factory=list)
    reasons: list[ProjectAttributionReason] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> "ProjectAttributionResult":
        if self.outcome in {ProjectAttributionOutcome.CREATED, ProjectAttributionOutcome.NOOP}:
            if not self.allocation_ids:
                raise ValueError("successful attribution requires allocation_ids")
            if self.reasons:
                raise ValueError("successful attribution must not carry failure reasons")
        else:
            if self.allocation_ids:
                raise ValueError("NEEDS_REVIEW must not expose created allocation_ids")
            if not self.reasons:
                raise ValueError("NEEDS_REVIEW requires at least one reason")
        return self
