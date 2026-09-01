"""Task25 Source Evidence Completion DTOs."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


SourceEvidenceStatus = Literal[
    "EXTRACTED",
    "VALIDATED",
]

InvoiceDocumentStatus = Literal[
    "VALID",
    "VOIDED",
    "RED",
]

EvidenceOutcome = Literal[
    "COMPLETED",
    "NOOP",
]

EvidenceStepOutcome = Literal[
    "CREATED",
    "UPDATED",
    "UPGRADED",
    "NOOP",
    "NONE",
]


def _finite_decimal(
    value: Decimal | None,
    *,
    field: str,
) -> Decimal | None:
    if value is None:
        return None
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")
    return value


class SourceDocumentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(
        min_length=1,
        max_length=255,
    )
    mime_type: str | None = Field(
        default=None,
        max_length=120,
    )
    source_uri: str | None = Field(
        default=None,
        max_length=500,
    )

    validation_status: SourceEvidenceStatus = "EXTRACTED"

    validated_by: str | None = Field(
        default=None,
        max_length=80,
    )
    validation_reason: str | None = None

    @field_validator(
        "filename",
        "mime_type",
        "source_uri",
        "validated_by",
        "validation_reason",
    )
    @classmethod
    def _strip_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _validated_requires_audit(
        self,
    ) -> "SourceDocumentEvidence":
        if self.validation_status == "VALIDATED":
            if not self.validated_by:
                raise ValueError(
                    "VALIDATED source document requires validated_by"
                )
            if not self.validation_reason:
                raise ValueError(
                    "VALIDATED source document requires validation_reason"
                )
        return self


class InvoiceLineEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_no: int = Field(ge=1)

    item_name: str | None = Field(
        default=None,
        max_length=300,
    )
    category: str | None = Field(
        default=None,
        max_length=80,
    )

    quantity: Decimal | None = None
    unit_price: Decimal | None = None

    net_amount: Decimal | None = None
    vat_amount: Decimal | None = None
    tax_rate: Decimal | None = None

    tax_classification_code: str | None = Field(
        default=None,
        max_length=80,
    )

    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )

    @field_validator(
        "item_name",
        "category",
        "tax_classification_code",
    )
    @classmethod
    def _strip_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator(
        "quantity",
        "unit_price",
        "net_amount",
        "vat_amount",
        "tax_rate",
    )
    @classmethod
    def _finite(
        cls,
        value: Decimal | None,
        info,
    ) -> Decimal | None:
        return _finite_decimal(
            value,
            field=info.field_name,
        )

    @field_validator("tax_rate")
    @classmethod
    def _tax_rate_range(
        cls,
        value: Decimal | None,
    ) -> Decimal | None:
        if value is not None and (
            value < Decimal("0")
            or value > Decimal("1")
        ):
            raise ValueError(
                "tax_rate must be between 0 and 1"
            )
        return value


class ReviewedTaxRateRules(BaseModel):
    """Reviewed/versioned tax-rate evidence consumed by Task09.

    Task25 does not invent a fallback rate set. If this object is absent,
    Task09 receives ``allowed_tax_rates=None`` and therefore keeps the Fact
    NEEDS_REVIEW.
    """

    model_config = ConfigDict(extra="forbid")

    rule_version: str = Field(
        min_length=1,
        max_length=80,
    )
    reviewed_by: str = Field(
        min_length=1,
        max_length=80,
    )
    allowed_tax_rates: list[Decimal] = Field(
        min_length=1,
    )

    @field_validator(
        "rule_version",
        "reviewed_by",
    )
    @classmethod
    def _strip_required(
        cls,
        value: str,
    ) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @field_validator("allowed_tax_rates")
    @classmethod
    def _rates(
        cls,
        values: list[Decimal],
    ) -> list[Decimal]:
        normalized: list[Decimal] = []

        for value in values:
            if not value.is_finite():
                raise ValueError(
                    "allowed tax rates must be finite"
                )
            if value < 0 or value > 1:
                raise ValueError(
                    "allowed tax rates must be between 0 and 1"
                )
            normalized.append(
                value.quantize(
                    Decimal("0.000001")
                )
            )

        if len(set(normalized)) != len(normalized):
            raise ValueError(
                "allowed_tax_rates must not contain duplicates"
            )

        return normalized


class InvoiceEvidenceCompletionRequest(BaseModel):
    """Evidence attached to an existing successful Task24 Invoice receipt."""

    model_config = ConfigDict(extra="forbid")

    source_system: Literal["IDP"] = "IDP"

    source_document_id: str = Field(
        min_length=1,
        max_length=160,
    )
    source_extraction_id: str = Field(
        min_length=1,
        max_length=160,
    )
    document_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )

    document: SourceDocumentEvidence

    invoice_status: InvoiceDocumentStatus | None = None

    lines: list[InvoiceLineEvidence] = Field(
        default_factory=list,
    )

    tax_rules: ReviewedTaxRateRules | None = None

    extraction_model: str | None = Field(
        default=None,
        max_length=120,
    )
    extraction_model_version: str | None = Field(
        default=None,
        max_length=80,
    )
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )

    page_start: int | None = Field(
        default=None,
        ge=1,
    )
    page_end: int | None = Field(
        default=None,
        ge=1,
    )

    @field_validator(
        "source_document_id",
        "source_extraction_id",
        "extraction_model",
        "extraction_model_version",
    )
    @classmethod
    def _strip_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("document_sha256")
    @classmethod
    def _lower_sha(
        cls,
        value: str,
    ) -> str:
        return value.lower()

    @model_validator(mode="after")
    def _page_range(
        self,
    ) -> "InvoiceEvidenceCompletionRequest":
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError(
                "page_end must not be earlier than page_start"
            )
        return self


class InvoiceEvidenceCompletionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: EvidenceOutcome

    receipt_id: int
    fact_id: int

    source_document_pk: int
    binding_id: int

    document_outcome: EvidenceStepOutcome
    provenance_outcome: EvidenceStepOutcome
    line_outcome: EvidenceStepOutcome
    invoice_status_outcome: EvidenceStepOutcome

    line_payload_fingerprint: str | None = None

    tax_rule_version: str | None = None

    task09_ruleset_version: str
    task09_desired_status: str
    validation_status: str

    findings: list[dict[str, str]] = Field(
        default_factory=list,
    )
