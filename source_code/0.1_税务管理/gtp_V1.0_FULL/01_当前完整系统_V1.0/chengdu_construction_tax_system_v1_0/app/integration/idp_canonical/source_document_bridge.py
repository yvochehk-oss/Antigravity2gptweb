"""Task25/28 IDP -> Canonical SourceDocument bridge."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.v3_evidence_integration_models import IDPSourceDocumentBinding
from app.v3_party_models import SourceDocument
from .evidence_schemas import SourceDocumentEvidence

class SourceDocumentBindingError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail); self.code=code; self.detail=detail

@dataclass(frozen=True)
class SourceDocumentBindingResult:
    binding: IDPSourceDocumentBinding
    source_document: SourceDocument
    outcome: str

_SOURCE_STATUS_RANK={"RECEIVED":0,"PARSED":1,"EXTRACTED":2,"VALIDATED":3}
_SUPPORTED_DOCUMENT_TYPES=frozenset({"invoice","contract","payment","bank_receipt"})

def _document_type(value: str) -> str:
    normalized=str(value).strip().lower()
    if normalized not in _SUPPORTED_DOCUMENT_TYPES:
        raise SourceDocumentBindingError("SOURCE_DOCUMENT_TYPE_UNSUPPORTED",f"unsupported SourceDocument type {value!r}")
    return normalized

def merge_source_document_status(current: str, requested: str) -> str:
    if current=="FAILED": raise SourceDocumentBindingError("SOURCE_DOCUMENT_FAILED","FAILED SourceDocument requires explicit recovery workflow")
    if current not in _SOURCE_STATUS_RANK: raise SourceDocumentBindingError("SOURCE_DOCUMENT_STATUS_UNKNOWN",f"unsupported current SourceDocument status {current!r}")
    if requested not in _SOURCE_STATUS_RANK: raise SourceDocumentBindingError("SOURCE_DOCUMENT_STATUS_UNKNOWN",f"unsupported requested SourceDocument status {requested!r}")
    return requested if _SOURCE_STATUS_RANK[requested] > _SOURCE_STATUS_RANK[current] else current

class SourceDocumentBridge:
    def __init__(self, db: Session) -> None: self.db=db
    def _lock(self, source_system: str, source_document_id: str) -> None:
        key="TASK25_SOURCE_DOCUMENT|" f"{source_system}|{source_document_id}"
        self.db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),{"lock_key":key})
    def _binding(self,*,source_system:str,source_document_id:str):
        return self.db.execute(select(IDPSourceDocumentBinding).where(IDPSourceDocumentBinding.source_system==source_system,IDPSourceDocumentBinding.source_document_id==source_document_id).with_for_update()).scalar_one_or_none()
    def _source_document(self,*,source_system:str,source_document_id:str):
        return self.db.execute(select(SourceDocument).where(SourceDocument.source_system==source_system,SourceDocument.external_document_id==source_document_id).with_for_update()).scalar_one_or_none()
    @staticmethod
    def _verify_source_document(document: SourceDocument,*,source_system:str,source_document_id:str,document_sha256:str,document_type:str="invoice") -> None:
        expected=_document_type(document_type)
        if document.source_system!=source_system or document.external_document_id!=source_document_id: raise SourceDocumentBindingError("SOURCE_DOCUMENT_IDENTITY_CONFLICT","SourceDocument identity differs from IDP binding")
        if document.file_sha256 is not None and document.file_sha256.lower()!=document_sha256: raise SourceDocumentBindingError("SOURCE_DOCUMENT_SHA_CONFLICT","existing SourceDocument SHA256 differs from IDP document")
        if document.document_type is not None and document.document_type!=expected: raise SourceDocumentBindingError("SOURCE_DOCUMENT_TYPE_CONFLICT",f"{expected} evidence cannot bind to SourceDocument type {document.document_type!r}")
    def bind(self,*,source_system:str,source_document_id:str,source_extraction_id:str,document_sha256:str,fact_id:int,receipt_id:int,evidence:SourceDocumentEvidence,document_type:str="invoice") -> SourceDocumentBindingResult:
        sha=document_sha256.lower(); expected=_document_type(document_type); self._lock(source_system,source_document_id)
        binding=self._binding(source_system=source_system,source_document_id=source_document_id)
        requested="VALIDATED" if evidence.validation_status=="VALIDATED" else "EXTRACTED"
        if binding is not None:
            if binding.document_sha256!=sha: raise SourceDocumentBindingError("SOURCE_DOCUMENT_SHA_CONFLICT","same IDP document id was reused with a different SHA256")
            if int(binding.fact_id)!=int(fact_id): raise SourceDocumentBindingError("SOURCE_DOCUMENT_FACT_CONFLICT","same IDP document is already bound to another Fact")
            document=self.db.get(SourceDocument,int(binding.source_document_pk))
            if document is None: raise SourceDocumentBindingError("SOURCE_DOCUMENT_ORPHAN_BINDING","IDP binding references a missing SourceDocument")
            self._verify_source_document(document,source_system=source_system,source_document_id=source_document_id,document_sha256=sha,document_type=expected)
            changed=False
            if document.file_sha256 is None: document.file_sha256=sha; changed=True
            if document.document_type is None: document.document_type=expected; changed=True
            desired=merge_source_document_status(str(document.status),requested)
            if desired!=document.status: document.status=desired; changed=True
            if evidence.validation_status=="VALIDATED" and binding.binding_status!="VALIDATED":
                binding.binding_status="VALIDATED"; binding.validated_by=evidence.validated_by; binding.validation_reason=evidence.validation_reason; changed=True
            if changed: binding.updated_at=datetime.now(timezone.utc); self.db.flush()
            return SourceDocumentBindingResult(binding,document,"UPGRADED" if changed else "NOOP")
        document=self._source_document(source_system=source_system,source_document_id=source_document_id); created=False
        if document is None:
            document=SourceDocument(source_system=source_system,external_document_id=source_document_id,filename=evidence.filename,mime_type=evidence.mime_type,file_sha256=sha,document_type=expected,source_uri=evidence.source_uri,status=requested); self.db.add(document); self.db.flush(); created=True
        else:
            self._verify_source_document(document,source_system=source_system,source_document_id=source_document_id,document_sha256=sha,document_type=expected)
            if document.file_sha256 is None: document.file_sha256=sha
            if document.document_type is None: document.document_type=expected
            document.status=merge_source_document_status(str(document.status),requested)
            if document.mime_type is None and evidence.mime_type: document.mime_type=evidence.mime_type
            if document.source_uri is None and evidence.source_uri: document.source_uri=evidence.source_uri
            self.db.flush()
        existing=self.db.execute(select(IDPSourceDocumentBinding).where(IDPSourceDocumentBinding.source_document_pk==int(document.id)).with_for_update()).scalar_one_or_none()
        if existing is not None: raise SourceDocumentBindingError("SOURCE_DOCUMENT_ALREADY_BOUND","Canonical SourceDocument is already owned by another IDP binding")
        binding=IDPSourceDocumentBinding(source_system=source_system,source_document_id=source_document_id,document_sha256=sha,source_document_pk=int(document.id),fact_id=int(fact_id),first_source_extraction_id=source_extraction_id,first_receipt_id=int(receipt_id),binding_status="VALIDATED" if evidence.validation_status=="VALIDATED" else "BOUND",validated_by=evidence.validated_by if evidence.validation_status=="VALIDATED" else None,validation_reason=evidence.validation_reason if evidence.validation_status=="VALIDATED" else None)
        self.db.add(binding); self.db.flush()
        return SourceDocumentBindingResult(binding,document,"CREATED" if created else "UPDATED")
