"""Task28 Payment Source Evidence Bridge and Canonical completion.

Task28 owns evidence completeness only. Payment value rules are reused from Task19.
"""
from __future__ import annotations
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.domain.facts.payment import PAYMENT_RULESET_VERSION, PaymentCandidate, PaymentFactError, build_payment_business_identity_key, validate_candidate
from app.v3_fact_models import Fact, FactProvenance
from app.v3_integration_models import CanonicalIngestReceipt
from app.v3_payment_models import PaymentFact
from .payment_schemas import PaymentEvidenceCompletionRequest, PaymentEvidenceCompletionResult
from .service import PAYMENT_DOCUMENT_TYPES, logical_receipt_document_type
from .source_document_bridge import SourceDocumentBindingError, SourceDocumentBridge

class PaymentEvidenceCompletionError(ValueError):
    def __init__(self,code:str,detail:str)->None:
        super().__init__(detail); self.code=code; self.detail=detail
@dataclass(frozen=True)
class ProvenanceCompletion:
    provenance: FactProvenance
    outcome: str

class PaymentEvidenceCompletionService:
    def __init__(self,db:Session,*,source_document_bridge:SourceDocumentBridge|None=None)->None:
        self.db=db; self.source_document_bridge=source_document_bridge or SourceDocumentBridge(db)
    def _receipt(self,request:PaymentEvidenceCompletionRequest)->CanonicalIngestReceipt:
        receipt=self.db.execute(select(CanonicalIngestReceipt).where(CanonicalIngestReceipt.source_system==request.source_system,CanonicalIngestReceipt.source_extraction_id==request.source_extraction_id).with_for_update()).scalar_one_or_none()
        if receipt is None: raise PaymentEvidenceCompletionError("TASK24_RECEIPT_MISSING","Task28 requires an existing Task24 receipt")
        if receipt.outcome not in {"CREATED","NOOP"}: raise PaymentEvidenceCompletionError("TASK24_RECEIPT_REJECTED","rejected Task24 receipt is not eligible for Payment completion")
        logical_type=logical_receipt_document_type(receipt)
        if logical_type not in PAYMENT_DOCUMENT_TYPES: raise PaymentEvidenceCompletionError("DOCUMENT_TYPE_MISMATCH",f"Task28 cannot complete {logical_type!r}")
        if receipt.source_document_id!=request.source_document_id: raise PaymentEvidenceCompletionError("SOURCE_DOCUMENT_MISMATCH","Task28 request differs from Task24 source_document_id")
        if receipt.document_sha256.lower()!=request.document_sha256.lower(): raise PaymentEvidenceCompletionError("SOURCE_DOCUMENT_SHA_CONFLICT","Task28 request differs from Task24 document SHA256")
        return receipt
    def _fact_and_payment(self,receipt:CanonicalIngestReceipt)->tuple[Fact,PaymentFact]:
        fact=self.db.execute(select(Fact).where(Fact.id==int(receipt.fact_id)).with_for_update()).scalar_one_or_none()
        if fact is None: raise PaymentEvidenceCompletionError("FACT_MISSING","Task24 receipt references missing Fact")
        if fact.fact_type!="PAYMENT" or not fact.is_current or fact.supersedes_fact_id is not None: raise PaymentEvidenceCompletionError("PAYMENT_FACT_STATE_INVALID","Task28 requires current unsuperseded PAYMENT Fact")
        payment=self.db.execute(select(PaymentFact).where(PaymentFact.fact_id==int(fact.id)).with_for_update()).scalar_one_or_none()
        if payment is None: raise PaymentEvidenceCompletionError("PAYMENT_PROJECTION_MISSING","PAYMENT Fact has no Task19 PaymentFact projection")
        expected=build_payment_business_identity_key(source_domain="IDP_DOCUMENT",source_row_id=receipt.source_document_id)
        if fact.business_identity_key!=expected: raise PaymentEvidenceCompletionError("PAYMENT_IDENTITY_DRIFT","persisted Payment business identity differs from Task19 identity")
        return fact,payment
    def _complete_provenance(self,*,fact_id:int,source_extraction_id:str,source_document_pk:int,request:PaymentEvidenceCompletionRequest)->ProvenanceCompletion:
        chunk=f"IDP:{source_extraction_id}"[:120]
        p=self.db.execute(select(FactProvenance).where(FactProvenance.fact_id==int(fact_id),FactProvenance.chunk_id==chunk).with_for_update()).scalar_one_or_none()
        if p is None: raise PaymentEvidenceCompletionError("TASK24_PROVENANCE_MISSING","Task28 cannot find Task24 IDP provenance")
        if p.document_id is not None and int(p.document_id)!=int(source_document_pk): raise PaymentEvidenceCompletionError("PROVENANCE_DOCUMENT_CONFLICT","Task24 provenance is already bound to another SourceDocument")
        changed=False
        if p.document_id is None: p.document_id=int(source_document_pk); changed=True
        if request.page_start is not None:
            if p.page_start is None: p.page_start=request.page_start; changed=True
            elif p.page_start!=request.page_start: raise PaymentEvidenceCompletionError("PROVENANCE_PAGE_CONFLICT","page_start conflicts with existing provenance")
        if request.page_end is not None:
            if p.page_end is None: p.page_end=request.page_end; changed=True
            elif p.page_end!=request.page_end: raise PaymentEvidenceCompletionError("PROVENANCE_PAGE_CONFLICT","page_end conflicts with existing provenance")
        reason="TASK24_IDP_APPROVED_INTAKE;TASK28_PAYMENT_SOURCE_DOCUMENT_BOUND"
        if request.document.validation_status=="VALIDATED": reason+=";SOURCE_DOCUMENT_VALIDATED"
        if p.verification_reason!=reason: p.verification_reason=reason; changed=True
        if request.document.validation_status=="VALIDATED" and request.document.validated_by and p.verifier_user_id!=request.document.validated_by: p.verifier_user_id=request.document.validated_by; changed=True
        if changed: self.db.flush()
        return ProvenanceCompletion(p,"UPDATED" if changed else "NOOP")
    @staticmethod
    def _candidate(payment:PaymentFact)->PaymentCandidate:
        return PaymentCandidate(payer_party_id=int(payment.payer_party_id),payee_party_id=int(payment.payee_party_id),transaction_date=payment.transaction_date,amount=payment.amount,currency=str(payment.currency),payer_account_id=payment.payer_account_id,payee_account_id=payment.payee_account_id,bank_reference=payment.bank_reference,settlement_method=str(payment.settlement_method),payment_nature=str(payment.payment_nature))
    def _complete(self,request:PaymentEvidenceCompletionRequest)->PaymentEvidenceCompletionResult:
        receipt=self._receipt(request); fact,payment=self._fact_and_payment(receipt); logical_type=logical_receipt_document_type(receipt)
        try:
            binding=self.source_document_bridge.bind(source_system=request.source_system,source_document_id=request.source_document_id,source_extraction_id=request.source_extraction_id,document_sha256=request.document_sha256,fact_id=int(fact.id),receipt_id=int(receipt.id),evidence=request.document,document_type=logical_type)
        except SourceDocumentBindingError as exc: raise PaymentEvidenceCompletionError(exc.code,exc.detail) from exc
        provenance=self._complete_provenance(fact_id=int(fact.id),source_extraction_id=request.source_extraction_id,source_document_pk=int(binding.source_document.id),request=request)
        try: validate_candidate(self._candidate(payment)); task19_valid=True; validation_error=None
        except PaymentFactError as exc: task19_valid=False; validation_error=str(exc)
        before=str(fact.validation_status)
        if not task19_valid: desired="INVALID"; reason=f"TASK19_PAYMENT_VALIDATION_FAILED:{validation_error}"
        elif str(binding.source_document.status)=="VALIDATED": desired="VALID"; reason="TASK19_PAYMENT_RULES_PASS;SOURCE_DOCUMENT_VALIDATED"
        else: desired="NEEDS_REVIEW"; reason="TASK19_PAYMENT_RULES_PASS;SOURCE_DOCUMENT_NOT_VALIDATED"
        if before!=desired: fact.validation_status=desired; self.db.flush()
        changed=binding.outcome!="NOOP" or provenance.outcome!="NOOP" or before!=desired
        return PaymentEvidenceCompletionResult(outcome="COMPLETED" if changed else "NOOP",receipt_id=int(receipt.id),fact_id=int(fact.id),business_identity_key=str(fact.business_identity_key),source_document_pk=int(binding.source_document.id),binding_id=int(binding.binding.id),document_outcome=binding.outcome,provenance_outcome=provenance.outcome,task19_ruleset_version=PAYMENT_RULESET_VERSION,validation_status=desired,reason=reason)
    def complete(self,request:PaymentEvidenceCompletionRequest,*,commit:bool=True)->PaymentEvidenceCompletionResult:
        try:
            with self.db.begin_nested(): result=self._complete(request)
            if commit: self.db.commit()
            return result
        except Exception:
            if commit: self.db.rollback()
            raise
