from __future__ import annotations
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.domain.facts.payment import PAYMENT_IDENTITY_VERSION, PAYMENT_RULESET_VERSION, PaymentCandidate, PaymentFactError, build_payment_business_identity_key, validate_candidate
from app.integration.idp_canonical.direct_v3_schemas import DirectV3ProductionRequest
from app.integration.idp_canonical.evidence_schemas import SourceDocumentEvidence
from app.integration.idp_canonical.payment_schemas import PaymentEvidenceCompletionRequest
from app.integration.idp_canonical.schemas import CanonicalIngestRequest, IDPPaymentData
from app.integration.idp_canonical.service import PAYMENT_DOCUMENT_TYPES, PAYMENT_RECEIPT_STORAGE_TYPE

def _intake(document_type="payment",**updates):
    data={"payer":{"name":"Payer"},"payee":{"name":"Payee"},"payer_role_evidence":"银行回单明确列示付款人","payee_role_evidence":"银行回单明确列示收款人","transaction_date":"2026-09-01","amount":"100.00","currency":"CNY","bank_reference":"BANK-REF-1","settlement_method":"BANK_TRANSFER","payment_nature":"NORMAL"}; data.update(updates)
    return CanonicalIngestRequest(source_system="IDP",source_document_id="PAY-DOC-1",source_extraction_id="PAY-EXT-1",document_sha256="a"*64,document_type=document_type,review_status="approved",approved_by="tester",data=data)
def _evidence(**updates):
    values=dict(source_system="IDP",source_document_id="PAY-DOC-1",source_extraction_id="PAY-EXT-1",document_sha256="a"*64,document=SourceDocumentEvidence(filename="payment.pdf",validation_status="VALIDATED",validated_by="tester",validation_reason="manual review")); values.update(updates); return PaymentEvidenceCompletionRequest(**values)

@pytest.mark.parametrize("document_type",["payment","bank_receipt"])
def test_task24_schema_accepts_payment_aliases(document_type): assert _intake(document_type).document_type==document_type
@pytest.mark.parametrize("field",["payer_role_evidence","payee_role_evidence"])
def test_explicit_role_evidence_is_required(field):
    payload=_intake().data.copy(); payload.pop(field)
    with pytest.raises(ValidationError): IDPPaymentData.model_validate(payload)
@pytest.mark.parametrize("field",["payer_role_evidence","payee_role_evidence"])
def test_blank_role_evidence_is_rejected(field):
    payload=_intake().data.copy(); payload[field]="   "
    with pytest.raises(ValidationError): IDPPaymentData.model_validate(payload)
def test_direction_is_not_an_accepted_payment_field():
    payload=_intake().data.copy(); payload["direction"]="out"
    with pytest.raises(ValidationError): IDPPaymentData.model_validate(payload)
def test_task19_identity_preserves_legacy_shape(): assert build_payment_business_identity_key(source_domain="LEGACY_CASHFLOW",source_row_id=7)=="PAYMENT|LEGACY_CASHFLOW|ROW|7"
def test_idp_identity_is_source_document_stable(): assert build_payment_business_identity_key(source_domain="IDP_DOCUMENT",source_row_id="PAY-DOC-1")=="PAYMENT|IDP_DOCUMENT|ROW|PAY-DOC-1"
def test_identity_rejects_separator_injection():
    with pytest.raises(PaymentFactError): build_payment_business_identity_key(source_domain="IDP_DOCUMENT",source_row_id="A|B")
def test_task19_ruleset_and_identity_versions_are_versioned(): assert PAYMENT_IDENTITY_VERSION=="TASK19_SOURCE_ROW_V1" and PAYMENT_RULESET_VERSION=="V3_PAYMENT_VALIDATION_V1"
def _candidate(**updates):
    values=dict(payer_party_id=1,payee_party_id=2,transaction_date=date(2026,9,1),amount=Decimal("100.00"),currency="CNY",settlement_method="BANK_TRANSFER",payment_nature="NORMAL"); values.update(updates); return PaymentCandidate(**values)
def test_task19_rejects_same_payer_payee():
    with pytest.raises(PaymentFactError): validate_candidate(_candidate(payee_party_id=1))
@pytest.mark.parametrize("amount",[Decimal("0"),Decimal("-1")])
def test_task19_rejects_nonpositive_amount(amount):
    with pytest.raises(PaymentFactError): validate_candidate(_candidate(amount=amount))
def test_task19_rejects_invalid_currency():
    with pytest.raises(PaymentFactError): validate_candidate(_candidate(currency="cny"))
def test_task19_rejects_unknown_settlement_method():
    with pytest.raises(PaymentFactError): validate_candidate(_candidate(settlement_method="WIRE_GUESS"))
def test_task19_rejects_unknown_payment_nature():
    with pytest.raises(PaymentFactError): validate_candidate(_candidate(payment_nature="GUESSED"))
@pytest.mark.parametrize("document_type",["payment","bank_receipt"])
def test_direct_v3_requires_payment_completion(document_type):
    with pytest.raises(ValidationError): DirectV3ProductionRequest(intake=_intake(document_type))
@pytest.mark.parametrize("document_type",["payment","bank_receipt"])
def test_direct_v3_accepts_matching_payment_completion(document_type): assert DirectV3ProductionRequest(intake=_intake(document_type),payment_evidence=_evidence()).payment_evidence is not None
def test_direct_v3_rejects_payment_source_mismatch():
    with pytest.raises(ValidationError): DirectV3ProductionRequest(intake=_intake(),payment_evidence=_evidence(source_document_id="OTHER"))
def test_payment_evidence_page_bounds_fail_closed():
    with pytest.raises(ValidationError): _evidence(page_start=3,page_end=2)
def test_payment_document_types_are_explicit(): assert PAYMENT_DOCUMENT_TYPES=={"payment","bank_receipt"}
def test_zero_migration_receipt_compatibility_is_explicit(): assert PAYMENT_RECEIPT_STORAGE_TYPE in {"invoice","contract"}
def test_payment_service_never_imports_legacy_direction_helper(): assert "legacy_direction_parties" not in (Path(__file__).parents[1]/"app/integration/idp_canonical/service.py").read_text(encoding="utf-8")
def test_payment_evidence_service_reuses_task19_validator():
    text=(Path(__file__).parents[1]/"app/integration/idp_canonical/payment_evidence_service.py").read_text(encoding="utf-8"); assert "validate_candidate" in text and "legacy_direction_parties" not in text
def test_no_task28_migration_exists():
    root=Path(__file__).parents[1]/"alembic/versions"; assert not list(root.glob("98*v3*"))
def test_task28_has_no_project_or_relationship_write():
    combined="\n".join((Path(__file__).parents[1]/p).read_text(encoding="utf-8") for p in ["app/integration/idp_canonical/service.py","app/integration/idp_canonical/payment_evidence_service.py"]); assert "project_id =" not in combined and "FactRelationship(" not in combined
