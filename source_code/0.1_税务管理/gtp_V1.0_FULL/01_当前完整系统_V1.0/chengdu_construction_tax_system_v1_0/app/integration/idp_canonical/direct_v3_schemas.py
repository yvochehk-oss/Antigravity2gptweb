"""Task27/28 production contract for the single IDP -> V3 entry point."""
from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contract_role_schemas import ContractRoleCompletionRequest, ContractRoleCompletionResult
from .evidence_schemas import InvoiceEvidenceCompletionRequest, InvoiceEvidenceCompletionResult
from .payment_schemas import PaymentEvidenceCompletionRequest, PaymentEvidenceCompletionResult
from .schemas import CanonicalIngestRequest, CanonicalIngestResult

DirectV3Outcome=Literal["COMPLETED","NOOP","REJECTED"]
class ProductionRouteState(BaseModel):
    model_config=ConfigDict(extra="forbid")
    scope: Literal["GLOBAL"]="GLOBAL"
    writer_mode: Literal["V3_PRIMARY"]
    legacy_write_enabled: Literal[False]
    new_fact_write_enabled: Literal[True]
    legacy_frozen: Literal[True]
    new_fact_read_mode: Literal["PRIMARY"]
    rag_source: Literal["CANONICAL_FACTS"]
    seal_finalized_by: str=Field(min_length=1,max_length=80)
    seal_finalized_at: datetime

class DirectV3ProductionRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    scope: Literal["GLOBAL"]="GLOBAL"
    intake: CanonicalIngestRequest
    invoice_evidence: InvoiceEvidenceCompletionRequest|None=None
    contract_roles: ContractRoleCompletionRequest|None=None
    payment_evidence: PaymentEvidenceCompletionRequest|None=None
    @model_validator(mode="after")
    def _match_completion_to_document(self):
        if sum([self.invoice_evidence is not None,self.contract_roles is not None,self.payment_evidence is not None])!=1:
            raise ValueError("DirectV3 production request requires exactly one matching completion payload")
        t=self.intake.document_type
        if t=="invoice":
            if self.invoice_evidence is None: raise ValueError("invoice production path requires invoice_evidence")
            completion=self.invoice_evidence
        elif t=="contract":
            if self.contract_roles is None: raise ValueError("contract production path requires contract_roles")
            completion=self.contract_roles
        elif t in {"payment","bank_receipt"}:
            if self.payment_evidence is None: raise ValueError("payment production path requires payment_evidence")
            completion=self.payment_evidence
        else: raise ValueError(f"unsupported DirectV3 document type {t!r}")
        for field in ("source_system","source_document_id","source_extraction_id","document_sha256"):
            left=getattr(self.intake,field); right=getattr(completion,field)
            if field=="document_sha256": left=str(left).lower(); right=str(right).lower()
            if left!=right: raise ValueError(f"Task27 intake/completion {field} mismatch: {left!r} != {right!r}")
        return self

class DirectV3ProductionResult(BaseModel):
    model_config=ConfigDict(extra="forbid")
    outcome: DirectV3Outcome
    document_type: Literal["invoice","contract","payment","bank_receipt"]
    route: ProductionRouteState
    task24: CanonicalIngestResult
    invoice_evidence: InvoiceEvidenceCompletionResult|None=None
    contract_roles: ContractRoleCompletionResult|None=None
    payment_evidence: PaymentEvidenceCompletionResult|None=None
    fact_id:int
    business_identity_key:str
    version_no:int
    validation_status:str
