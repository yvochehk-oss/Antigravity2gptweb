from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


DocumentType = Literal[
    "contract",
    "invoice",
    "receipt",
    "settlement",
    "bank_receipt",
    "unknown",
]


class Party(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    credit_code: Optional[str] = None
    tax_id: Optional[str] = None
    address: Optional[str] = None
    legal_representative: Optional[str] = None


class PaymentTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    percentage: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    condition: Optional[str] = None


class ContractData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_no: Optional[str] = None
    contract_name: Optional[str] = None
    party_a: Party = Field(default_factory=Party)
    party_b: Party = Field(default_factory=Party)
    project_name: Optional[str] = None
    sign_date: Optional[date] = None
    currency: str = "CNY"
    amount_tax_included: Optional[Decimal] = None
    amount_tax_excluded: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None
    payment_terms: List[PaymentTerm] = Field(default_factory=list)
    contract_start_date: Optional[date] = None
    contract_end_date: Optional[date] = None
    warranty_period: Optional[str] = None
    bank: Optional[str] = None
    bank_account: Optional[str] = None
    confidence: Dict[str, float] = Field(default_factory=dict)
    sources: Dict[str, str] = Field(default_factory=dict)


class InvoiceData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_type: Optional[str] = None
    invoice_code: Optional[str] = None
    invoice_no: Optional[str] = None
    invoice_date: Optional[date] = None
    buyer: Party = Field(default_factory=Party)
    seller: Party = Field(default_factory=Party)
    amount_excluding_tax: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    amount_including_tax: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None
    currency: str = "CNY"
    check_code: Optional[str] = None
    confidence: Dict[str, float] = Field(default_factory=dict)
    sources: Dict[str, str] = Field(default_factory=dict)


class ParsedDocument(BaseModel):
    text: str
    parser: str
    page_count: int = 0
    ocr_confidence: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExtractionEnvelope(BaseModel):
    document_type: DocumentType
    data: Dict[str, Any]
    confidence: Dict[str, float] = Field(default_factory=dict)
    validation: Dict[str, Any] = Field(default_factory=dict)
    status: Literal[
        "extracted",
        "approved",
        "needs_review",
        "validation_failed",
    ] = "extracted"
