from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.integration.idp_canonical.normalizer import (
    CONTRACT_IDENTITY_VERSION,
    build_contract_business_identity_key,
    normalize_invoice_type,
)
from app.integration.idp_canonical.party_resolver import (
    PartyResolution,
    PartyResolutionError,
    PartyResolver,
)
from app.integration.idp_canonical.schemas import (
    CanonicalIngestRequest,
    IDPPartyData,
)
from app.integration.idp_canonical.service import (
    CanonicalIngestRejected,
    CanonicalIngestService,
    _decide_existing,
)
from app.v3_fact_models import FactProvenance, InvoiceFact
from app.v3_integration_models import CanonicalIngestReceipt


class FakeResolver:
    def __init__(self) -> None:
        self.values = {
            "seller": PartyResolution(
                party_id=101,
                party_code="SELLER001",
                party_name="Seller Ltd",
                canonical_tax_identity="915101001234567890",
                resolution_method="CREDIT_CODE_EXACT",
                credit_code="915101001234567890",
                tax_id="915101001234567890",
            ),
            "buyer": PartyResolution(
                party_id=202,
                party_code="BUYER001",
                party_name="Buyer Ltd",
                canonical_tax_identity="915101009876543210",
                resolution_method="CREDIT_CODE_EXACT",
                credit_code="915101009876543210",
                tax_id="915101009876543210",
            ),
            "party a": PartyResolution(
                party_id=101,
                party_code="SELLER001",
                party_name="Party A",
                canonical_tax_identity="915101001234567890",
                resolution_method="CREDIT_CODE_EXACT",
                credit_code="915101001234567890",
                tax_id="915101001234567890",
            ),
            "party b": PartyResolution(
                party_id=202,
                party_code="BUYER001",
                party_name="Party B",
                canonical_tax_identity="915101009876543210",
                resolution_method="CREDIT_CODE_EXACT",
                credit_code="915101009876543210",
                tax_id="915101009876543210",
            ),
        }

    def resolve(self, source):
        key = str(source.name or "").strip().lower()
        if key not in self.values:
            raise PartyResolutionError(
                "PARTY_UNRESOLVED",
                key,
            )
        return self.values[key]


def _invoice_request(
    *,
    review_status: str = "approved",
    amount_including_tax: str = "113.00",
) -> CanonicalIngestRequest:
    return CanonicalIngestRequest(
        source_system="IDP",
        source_document_id="doc-1",
        source_extraction_id="ext-1",
        document_sha256="a" * 64,
        document_type="invoice",
        review_status=review_status,
        approved_by="tester",
        extraction_model="test-model",
        extraction_model_version="1",
        confidence={"invoice_no": 0.99},
        data={
            "invoice_type": "增值税电子专用发票",
            "invoice_code": None,
            "invoice_no": "INV-001",
            "invoice_date": "2026-08-31",
            "buyer": {
                "name": "Buyer",
                "credit_code": "915101009876543210",
                "tax_id": "915101009876543210",
                "address": None,
                "legal_representative": None,
            },
            "seller": {
                "name": "Seller",
                "credit_code": "915101001234567890",
                "tax_id": "915101001234567890",
                "address": None,
                "legal_representative": None,
            },
            "amount_excluding_tax": "100.00",
            "tax_amount": "13.00",
            "amount_including_tax": amount_including_tax,
            "tax_rate": "0.13",
            "currency": "CNY",
            "check_code": None,
            "confidence": {"invoice_no": 0.99},
            "sources": {},
        },
    )


def _contract_request() -> CanonicalIngestRequest:
    return CanonicalIngestRequest(
        source_system="IDP",
        source_document_id="contract-doc-1",
        source_extraction_id="contract-ext-1",
        document_sha256="b" * 64,
        document_type="contract",
        review_status="approved",
        approved_by="tester",
        extraction_model="test-model",
        extraction_model_version="1",
        confidence={"contract_no": 0.99},
        data={
            "contract_no": "HT-001",
            "contract_name": "Example",
            "party_a": {
                "name": "Party A",
                "credit_code": "915101001234567890",
                "tax_id": "915101001234567890",
                "address": None,
                "legal_representative": None,
            },
            "party_b": {
                "name": "Party B",
                "credit_code": "915101009876543210",
                "tax_id": "915101009876543210",
                "address": None,
                "legal_representative": None,
            },
            "project_name": None,
            "sign_date": "2026-08-31",
            "currency": "CNY",
            "amount_tax_included": "1000.00",
            "amount_tax_excluded": "917.43",
            "tax_amount": "82.57",
            "tax_rate": "0.09",
            "payment_terms": [],
            "contract_start_date": None,
            "contract_end_date": None,
            "warranty_period": None,
            "bank": None,
            "bank_account": None,
            "confidence": {"contract_no": 0.99},
            "sources": {},
        },
    )


def _service() -> CanonicalIngestService:
    return CanonicalIngestService(
        object(),
        party_resolver=FakeResolver(),
    )


def test_invoice_type_is_split_into_orthogonal_axes():
    result = normalize_invoice_type(
        "增值税电子专用发票"
    )

    assert result.medium == "DIGITAL"
    assert result.category == "SPECIAL"
    assert result.recognized is True
    assert result.mapping_version == "IDP_INVOICE_TYPE_V1"


def test_unknown_invoice_type_is_not_guessed():
    result = normalize_invoice_type(
        "一种未来新增票种"
    )

    assert result.medium == "OTHER"
    assert result.category == "OTHER"
    assert result.recognized is False


def test_invoice_identity_version_is_not_fact_version():
    service = _service()
    request = _invoice_request()

    prepared = service.prepare(request)
    fact, domain, _ = service._build_new_fact(
        request,
        prepared,
    )

    assert isinstance(domain, InvoiceFact)
    assert domain.invoice_identity_version == "DIGITAL_V1"
    assert fact.version_no == 1
    assert domain.invoice_identity_version != str(
        fact.version_no
    )


def test_invoice_deprecated_type_axis_is_not_used():
    service = _service()
    request = _invoice_request()

    prepared = service.prepare(request)
    _, domain, _ = service._build_new_fact(
        request,
        prepared,
    )

    assert isinstance(domain, InvoiceFact)
    assert domain.invoice_type is None
    assert domain.invoice_medium == "DIGITAL"
    assert domain.invoice_category == "SPECIAL"


def test_invoice_without_lines_and_source_document_starts_review():
    service = _service()
    request = _invoice_request()

    prepared = service.prepare(request)
    fact, _, provenance = service._build_new_fact(
        request,
        prepared,
    )

    assert fact.validation_status == "NEEDS_REVIEW"
    assert provenance.document_id is None
    assert isinstance(provenance, FactProvenance)
    assert (
        prepared.canonical_payload[
            "invoice_lines_present"
        ]
        is False
    )
    assert (
        prepared.canonical_payload[
            "source_document_registered"
        ]
        is False
    )


def test_contract_party_a_and_party_b_are_not_assumed_roles():
    service = _service()
    request = _contract_request()

    prepared = service.prepare(request)
    fact, domain, provenance = service._build_new_fact(
        request,
        prepared,
    )

    assert prepared.identity_version == CONTRACT_IDENTITY_VERSION
    assert domain.buyer_party_id is None
    assert domain.seller_party_id is None
    assert fact.validation_status == "NEEDS_REVIEW"
    assert provenance.document_id is None


def test_contract_identity_is_participant_order_independent():
    left = build_contract_business_identity_key(
        "HT-001",
        ["PARTY-A", "PARTY-B"],
    )
    right = build_contract_business_identity_key(
        "HT-001",
        ["PARTY-B", "PARTY-A"],
    )

    assert left == right


def test_same_business_same_payload_is_noop():
    assert _decide_existing("abc", "abc") == (
        "NOOP",
        None,
    )


def test_same_business_conflicting_payload_is_rejected():
    assert _decide_existing("abc", "def") == (
        "REJECTED",
        "SOURCE_DATA_CONFLICT",
    )


def test_existing_unmanaged_fact_is_rejected():
    assert _decide_existing(None, "def") == (
        "REJECTED",
        "EXISTING_FACT_UNMANAGED",
    )


def test_unapproved_source_is_rejected_before_database_work():
    service = _service()
    request = _invoice_request(
        review_status="needs_review"
    )

    with pytest.raises(
        CanonicalIngestRejected,
        match="approved",
    ) as exc:
        service.ingest(
            request,
            commit=False,
        )

    assert exc.value.code == "SOURCE_NOT_APPROVED"


def test_invoice_amount_contradiction_is_rejected():
    service = _service()
    request = _invoice_request(
        amount_including_tax="999.00"
    )

    with pytest.raises(
        CanonicalIngestRejected
    ) as exc:
        service.prepare(request)

    assert exc.value.code == "AMOUNT_INCONSISTENT"


def test_new_fact_never_contains_automatic_supersede():
    service = _service()
    request = _invoice_request()

    prepared = service.prepare(request)
    fact, _, _ = service._build_new_fact(
        request,
        prepared,
    )

    assert fact.supersedes_fact_id is None
    assert fact.is_current is True


def test_receipt_has_task24_source_idempotency_constraint():
    names = {
        constraint.name
        for constraint
        in CanonicalIngestReceipt.__table__.constraints
        if constraint.name
    }

    assert (
        "uq_canonical_ingest_receipts_source_extraction"
        in names
    )


def test_party_identifier_conflict_fails_closed(monkeypatch):
    resolver = PartyResolver(object())

    def identifiers(value: str):
        if value == "915101001234567890":
            return {1}
        if value == "915101009876543210":
            return {2}
        return set()

    monkeypatch.setattr(
        resolver,
        "_identifier_party_ids",
        identifiers,
    )

    source = IDPPartyData(
        name="Conflicted",
        credit_code="915101001234567890",
        tax_id="915101009876543210",
    )

    with pytest.raises(
        PartyResolutionError
    ) as exc:
        resolver.resolve(source)

    assert exc.value.code == "PARTY_IDENTIFIER_CONFLICT"


def test_strong_credit_code_does_not_fall_back_to_different_tax_id(
    monkeypatch,
):
    resolver = PartyResolver(object())

    def identifiers(value: str):
        if value == "915101001234567890":
            return set()
        if value == "915101009876543210":
            return {2}
        return set()

    monkeypatch.setattr(
        resolver,
        "_identifier_party_ids",
        identifiers,
    )

    source = IDPPartyData(
        name="Conflicted",
        credit_code="915101001234567890",
        tax_id="915101009876543210",
    )

    with pytest.raises(
        PartyResolutionError
    ) as exc:
        resolver.resolve(source)

    assert exc.value.code == "PARTY_IDENTIFIER_CONFLICT"

