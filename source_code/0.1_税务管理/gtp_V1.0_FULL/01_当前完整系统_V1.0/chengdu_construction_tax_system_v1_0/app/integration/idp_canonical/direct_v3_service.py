"""Task27/28 single production entry point for approved IDP -> Canonical V3."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from sqlalchemy.orm import Session
from app.v3_fact_models import Fact
from .contract_role_service import ContractRoleService
from .direct_v3_schemas import DirectV3ProductionRequest, DirectV3ProductionResult
from .evidence_service import InvoiceEvidenceCompletionService
from .payment_evidence_service import PaymentEvidenceCompletionService
from .production_route_guard import ProductionRouteGuard
from .service import CanonicalIngestService

class DirectV3ProductionError(RuntimeError):
    def __init__(self,code:str,detail:str)->None:
        super().__init__(detail); self.code=code; self.detail=detail
@dataclass(frozen=True)
class FactInvariantSnapshot:
    fact_id:int; business_identity_key:str; version_no:int; supersedes_fact_id:int|None

def capture_fact_invariants(fact:Any)->FactInvariantSnapshot:
    return FactInvariantSnapshot(int(fact.id),str(fact.business_identity_key),int(fact.version_no),int(fact.supersedes_fact_id) if fact.supersedes_fact_id is not None else None)
def verify_fact_invariants(fact:Any,before:FactInvariantSnapshot)->None:
    after=capture_fact_invariants(fact)
    if after.fact_id!=before.fact_id: raise DirectV3ProductionError("FACT_ID_MUTATED","Task27 completion changed Canonical Fact id")
    if after.business_identity_key!=before.business_identity_key: raise DirectV3ProductionError("BUSINESS_IDENTITY_MUTATED","Task27 completion changed Task24 business identity")
    if after.version_no!=before.version_no: raise DirectV3ProductionError("FACT_VERSION_MUTATED","Task27 completion changed Fact.version_no")
    if after.supersedes_fact_id!=before.supersedes_fact_id: raise DirectV3ProductionError("FACT_SUPERSESSION_MUTATED","Task27 completion changed Fact.supersession state")
def completion_outcome(value:str)->str: return "NOOP" if value=="NOOP" else "COMPLETED"

class DirectV3IngestService:
    def __init__(self,db:Session,*,route_guard:ProductionRouteGuard|None=None,intake_service:CanonicalIngestService|None=None,invoice_service:InvoiceEvidenceCompletionService|None=None,contract_service:ContractRoleService|None=None,payment_service:PaymentEvidenceCompletionService|None=None)->None:
        self.db=db; self.route_guard=route_guard or ProductionRouteGuard(db); self.intake_service=intake_service or CanonicalIngestService(db); self.invoice_service=invoice_service or InvoiceEvidenceCompletionService(db); self.contract_service=contract_service or ContractRoleService(db); self.payment_service=payment_service or PaymentEvidenceCompletionService(db)
    def _fact(self,fact_id:int)->Fact:
        fact=self.db.get(Fact,int(fact_id))
        if fact is None: raise DirectV3ProductionError("TASK24_FACT_MISSING",f"Task24 result references missing Fact {fact_id}")
        return fact
    def _process(self,request:DirectV3ProductionRequest)->DirectV3ProductionResult:
        route=self.route_guard.require(scope=request.scope)
        task24=self.intake_service.ingest(request.intake,commit=False)
        if task24.status=="REJECTED":
            return DirectV3ProductionResult(outcome="REJECTED",document_type=request.intake.document_type,route=route,task24=task24,invoice_evidence=None,contract_roles=None,payment_evidence=None,fact_id=int(task24.fact_id),business_identity_key=str(task24.business_identity_key),version_no=int(task24.version_no),validation_status=str(task24.validation_status))
        fact=self._fact(task24.fact_id); before=capture_fact_invariants(fact)
        if before.business_identity_key!=str(task24.business_identity_key): raise DirectV3ProductionError("TASK24_IDENTITY_RESULT_DRIFT","Task24 result business identity differs from persisted Fact")
        if before.version_no!=int(task24.version_no): raise DirectV3ProductionError("TASK24_VERSION_RESULT_DRIFT","Task24 result version differs from persisted Fact")
        invoice_result=None; contract_result=None; payment_result=None
        if request.intake.document_type=="invoice":
            assert request.invoice_evidence is not None
            invoice_result=self.invoice_service.complete(request.invoice_evidence,commit=False)
            if int(invoice_result.fact_id)!=before.fact_id: raise DirectV3ProductionError("TASK25_FACT_DRIFT","Task25 completed a different Fact than Task24")
            operation_outcome=completion_outcome(invoice_result.outcome); final_status=str(invoice_result.validation_status)
        elif request.intake.document_type=="contract":
            assert request.contract_roles is not None
            contract_result=self.contract_service.complete(request.contract_roles,commit=False)
            if int(contract_result.fact_id)!=before.fact_id: raise DirectV3ProductionError("TASK26_FACT_DRIFT","Task26 completed a different Fact than Task24")
            if str(contract_result.business_identity_key)!=before.business_identity_key: raise DirectV3ProductionError("TASK26_IDENTITY_DRIFT","Task26 result changed Task24 business identity")
            if str(contract_result.validation_status)!="NEEDS_REVIEW": raise DirectV3ProductionError("CONTRACT_VALIDATION_SCOPE_VIOLATION","Task27 must not promote Contract Facts beyond NEEDS_REVIEW")
            operation_outcome=completion_outcome(contract_result.outcome); final_status=str(contract_result.validation_status)
        else:
            assert request.payment_evidence is not None
            payment_result=self.payment_service.complete(request.payment_evidence,commit=False)
            if int(payment_result.fact_id)!=before.fact_id: raise DirectV3ProductionError("TASK28_FACT_DRIFT","Task28 completed a different Fact than Task24")
            if str(payment_result.business_identity_key)!=before.business_identity_key: raise DirectV3ProductionError("TASK28_IDENTITY_DRIFT","Task28 result changed Task19/Task24 Payment identity")
            operation_outcome=completion_outcome(payment_result.outcome); final_status=str(payment_result.validation_status)
        self.db.flush(); fact=self._fact(before.fact_id); verify_fact_invariants(fact,before)
        if str(fact.validation_status)!=final_status: raise DirectV3ProductionError("VALIDATION_STATUS_RESULT_DRIFT","completion result validation_status differs from persisted Fact")
        return DirectV3ProductionResult(outcome=operation_outcome,document_type=request.intake.document_type,route=route,task24=task24,invoice_evidence=invoice_result,contract_roles=contract_result,payment_evidence=payment_result,fact_id=before.fact_id,business_identity_key=before.business_identity_key,version_no=before.version_no,validation_status=final_status)
    def process(self,request:DirectV3ProductionRequest,*,commit:bool=True)->DirectV3ProductionResult:
        try:
            with self.db.begin_nested(): result=self._process(request)
            if commit: self.db.commit()
            return result
        except Exception:
            if commit: self.db.rollback()
            raise
