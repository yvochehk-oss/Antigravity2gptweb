"""Stable Task24 application contract between IDP and Canonical Facts."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ReviewStatus = Literal[
    "extracted",
    "approved",
    "auto_approved",
    "needs_review",
    "validation_failed",
    "rejected",
]

DocumentType = Literal["invoice", "contract"]
IngestOutcome = Literal["CREATED", "NOOP", "REJECTED"]


class IDPPartyData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    credit_code: Optional[str] = None
    tax_id: Optional[str] = None
    address: Optional[str] = None
    legal_representative: Optional[str] = None


class IDPPaymentTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    percentage: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    condition: Optional[str] = None


class IDPInvoiceData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_type: Optional[str] = None
    invoice_code: Optional[str] = None
    invoice_no: Optional[str] = None
    invoice_date: Optional[date] = None

    buyer: IDPPartyData = Field(default_factory=IDPPartyData)
    seller: IDPPartyData = Field(default_factory=IDPPartyData)

    amount_excluding_tax: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    amount_including_tax: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None

    currency: str = "CNY"
    check_code: Optional[str] = None

    confidence: Dict[str, float] = Field(default_factory=dict)
    sources: Dict[str, str] = Field(default_factory=dict)


class IDPContractData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_no: Optional[str] = None
    contract_name: Optional[str] = None

    party_a: IDPPartyData = Field(default_factory=IDPPartyData)
    party_b: IDPPartyData = Field(default_factory=IDPPartyData)

    project_name: Optional[str] = None
    sign_date: Optional[date] = None
    currency: str = "CNY"

    amount_tax_included: Optional[Decimal] = None
    amount_tax_excluded: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None

    payment_terms: List[IDPPaymentTerm] = Field(default_factory=list)

    contract_start_date: Optional[date] = None
    contract_end_date: Optional[date] = None
    warranty_period: Optional[str] = None

    bank: Optional[str] = None
    bank_account: Optional[str] = None

    confidence: Dict[str, float] = Field(default_factory=dict)
    sources: Dict[str, str] = Field(default_factory=dict)


class CanonicalIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: Literal["IDP"] = "IDP"

    source_document_id: str = Field(min_length=1, max_length=160)
    source_extraction_id: str = Field(min_length=1, max_length=160)

    document_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )

    document_type: DocumentType
    review_status: ReviewStatus

    approved_by: Optional[str] = Field(default=None, max_length=80)

    extraction_model: Optional[str] = Field(default=None, max_length=120)
    extraction_model_version: Optional[str] = Field(default=None, max_length=80)

    data: Dict[str, Any]
    confidence: Dict[str, float] = Field(default_factory=dict)


class CanonicalIngestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: IngestOutcome
    receipt_id: int
    fact_id: int

    fact_type: str
    business_identity_key: str
    identity_version: str

    version_no: int
    validation_status: str

    error_code: Optional[str] = None
    error_detail: Optional[str] = None
