from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.integration.idp_canonical.contract_role_schemas import (
    ContractRoleCompletionRequest,
    ContractRoleCompletionResult,
)
from app.integration.idp_canonical.direct_v3_schemas import (
    DirectV3ProductionRequest,
    ProductionRouteState,
)
from app.integration.idp_canonical.direct_v3_service import (
    DirectV3IngestService,
    DirectV3ProductionError,
    capture_fact_invariants,
    completion_outcome,
    verify_fact_invariants,
)
from app.integration.idp_canonical.evidence_schemas import (
    InvoiceEvidenceCompletionRequest,
    InvoiceEvidenceCompletionResult,
    SourceDocumentEvidence,
)
from app.integration.idp_canonical.production_route_guard import (
    EXPECTED_ROUTE,
    ProductionRouteGuardError,
    validate_route_values,
)
from app.integration.idp_canonical.schemas import (
    CanonicalIngestRequest,
    CanonicalIngestResult,
)


def _route() -> ProductionRouteState:
    return ProductionRouteState(
        scope="GLOBAL",
        **EXPECTED_ROUTE,
        seal_finalized_by="task23-test",
        seal_finalized_at=datetime.now(timezone.utc),
    )


def _invoice_intake(*, review_status: str = "approved") -> CanonicalIngestRequest:
    return CanonicalIngestRequest(
        source_system="IDP",
        source_document_id="DOC-I-1",
        source_extraction_id="EXT-I-1",
        document_sha256="a" * 64,
        document_type="invoice",
        review_status=review_status,
        approved_by="tester",
        extraction_model="idp-test",
        extraction_model_version="1",
        data={
            "invoice_type": "增值税电子专用发票",
            "invoice_no": "INV-1",
            "invoice_date": "2026-09-01",
            "seller": {"name": "Seller"},
            "buyer": {"name": "Buyer"},
            "amount_excluding_tax": "100.00",
            "tax_amount": "13.00",
            "amount_including_tax": "113.00",
            "currency": "CNY",
        },
    )


def _invoice_evidence(**updates) -> InvoiceEvidenceCompletionRequest:
    values = dict(
        source_system="IDP",
        source_document_id="DOC-I-1",
        source_extraction_id="EXT-I-1",
        document_sha256="a" * 64,
        document=SourceDocumentEvidence(
            filename="invoice.pdf",
            validation_status="VALIDATED",
            validated_by="tester",
            validation_reason="reviewed",
        ),
        invoice_status="VALID",
        lines=[],
        tax_rules=None,
    )
    values.update(updates)
    return InvoiceEvidenceCompletionRequest(**values)


def _contract_intake() -> CanonicalIngestRequest:
    return CanonicalIngestRequest(
        source_system="IDP",
        source_document_id="DOC-C-1",
        source_extraction_id="EXT-C-1",
        document_sha256="b" * 64,
        document_type="contract",
        review_status="approved",
        approved_by="tester",
        data={
            "contract_no": "HT-1",
            "party_a": {"name": "A"},
            "party_b": {"name": "B"},
            "currency": "CNY",
        },
    )


def _contract_roles() -> ContractRoleCompletionRequest:
    return ContractRoleCompletionRequest(
        source_system="IDP",
        source_document_id="DOC-C-1",
        source_extraction_id="EXT-C-1",
        document_sha256="b" * 64,
        submitted_by="tester",
        evidences=[],
    )


def _task24_result(*, document_type: str, status: str = "CREATED") -> CanonicalIngestResult:
    return CanonicalIngestResult(
        status=status,
        receipt_id=1,
        fact_id=101,
        fact_type=document_type.upper(),
        business_identity_key=f"{document_type.upper()}|KEY",
        identity_version="DIGITAL_V1" if document_type == "invoice" else "CONTRACT_PARTIES_V1",
        version_no=1,
        validation_status="NEEDS_REVIEW",
        error_code="SOURCE_DATA_CONFLICT" if status == "REJECTED" else None,
        error_detail="conflict" if status == "REJECTED" else None,
    )


def _invoice_result(*, outcome: str = "COMPLETED", status: str = "VALID", fact_id: int = 101) -> InvoiceEvidenceCompletionResult:
    return InvoiceEvidenceCompletionResult(
        outcome=outcome,
        receipt_id=1,
        fact_id=fact_id,
        source_document_pk=11,
        binding_id=12,
        document_outcome="NOOP",
        provenance_outcome="NOOP",
        line_outcome="NOOP",
        invoice_status_outcome="NOOP",
        line_payload_fingerprint=None,
        tax_rule_version="TEST",
        task09_ruleset_version="V3_INVOICE_VALIDATION_V1",
        task09_desired_status=status,
        validation_status=status,
        findings=[],
    )


def _contract_result(*, outcome: str = "UPDATED", status: str = "NEEDS_REVIEW", fact_id: int = 101, key: str = "CONTRACT|KEY") -> ContractRoleCompletionResult:
    return ContractRoleCompletionResult(
        outcome=outcome,
        receipt_id=1,
        fact_id=fact_id,
        business_identity_key=key,
        version_no=1,
        validation_status=status,
        resolution_id=1,
        resolution_seq=1,
        resolution_status="NEEDS_REVIEW",
        buyer_party_id=None,
        seller_party_id=None,
        ruleset_version="CONTRACT_ROLE_RULESET_V1",
        evidence_outcome="NOOP",
        resolution_outcome="CREATED",
        evidence_ids=[],
        evidence_set_fingerprint="c" * 64,
        reason_code="ROLE_EVIDENCE_INSUFFICIENT",
        reason_detail="missing",
        findings=[],
    )


class FakeDB:
    def __init__(self, fact):
        self.fact = fact
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    def begin_nested(self):
        return nullcontext()

    def get(self, model, key):
        return self.fact if int(key) == int(self.fact.id) else None

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeGuard:
    def __init__(self, log):
        self.log = log

    def require(self, *, scope):
        self.log.append("guard")
        return _route()


class FakeIntake:
    def __init__(self, log, result):
        self.log = log
        self.result = result

    def ingest(self, request, *, commit):
        self.log.append("task24")
        assert commit is False
        return self.result


class FakeInvoice:
    def __init__(self, log, db, result, exc=None):
        self.log = log
        self.db = db
        self.result = result
        self.exc = exc

    def complete(self, request, *, commit):
        self.log.append("task25")
        assert commit is False
        if self.exc:
            raise self.exc
        self.db.fact.validation_status = self.result.validation_status
        return self.result


class FakeContract:
    def __init__(self, log, db, result):
        self.log = log
        self.db = db
        self.result = result

    def complete(self, request, *, commit):
        self.log.append("task26")
        assert commit is False
        self.db.fact.validation_status = self.result.validation_status
        return self.result


def _fact(key: str = "INVOICE|KEY"):
    return SimpleNamespace(
        id=101,
        business_identity_key=key,
        version_no=1,
        supersedes_fact_id=None,
        validation_status="NEEDS_REVIEW",
    )


def test_expected_route_is_strict_v3_primary():
    assert EXPECTED_ROUTE == {
        "writer_mode": "V3_PRIMARY",
        "legacy_write_enabled": False,
        "new_fact_write_enabled": True,
        "legacy_frozen": True,
        "new_fact_read_mode": "PRIMARY",
        "rag_source": "CANONICAL_FACTS",
    }


def test_route_validation_accepts_exact_live_and_sealed_state():
    result = validate_route_values(deepcopy(EXPECTED_ROUTE), deepcopy(EXPECTED_ROUTE))
    assert result.live == EXPECTED_ROUTE
    assert result.sealed == EXPECTED_ROUTE


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("writer_mode", "DUAL_WRITE"),
        ("legacy_write_enabled", True),
        ("new_fact_write_enabled", False),
        ("legacy_frozen", False),
        ("new_fact_read_mode", "SHADOW"),
        ("rag_source", "LEGACY"),
    ],
)
def test_route_validation_fails_closed_on_live_state(field, bad):
    live = deepcopy(EXPECTED_ROUTE)
    live[field] = bad
    with pytest.raises(ProductionRouteGuardError) as exc:
        validate_route_values(live, deepcopy(EXPECTED_ROUTE))
    assert exc.value.code == "PRODUCTION_ROUTE_STATE_MISMATCH"


def test_route_validation_fails_closed_on_seal_snapshot():
    seal = deepcopy(EXPECTED_ROUTE)
    seal["rag_source"] = "LEGACY"
    with pytest.raises(ProductionRouteGuardError) as exc:
        validate_route_values(deepcopy(EXPECTED_ROUTE), seal)
    assert exc.value.code == "PRODUCTION_SEAL_STATE_MISMATCH"


def test_invoice_request_requires_task25_evidence():
    with pytest.raises(ValidationError):
        DirectV3ProductionRequest(intake=_invoice_intake())


def test_invoice_request_rejects_contract_completion():
    with pytest.raises(ValidationError):
        DirectV3ProductionRequest(
            intake=_invoice_intake(),
            invoice_evidence=_invoice_evidence(),
            contract_roles=_contract_roles(),
        )


def test_contract_request_requires_task26_completion():
    with pytest.raises(ValidationError):
        DirectV3ProductionRequest(intake=_contract_intake())


def test_contract_request_rejects_invoice_completion():
    with pytest.raises(ValidationError):
        DirectV3ProductionRequest(
            intake=_contract_intake(),
            invoice_evidence=_invoice_evidence(
                source_document_id="DOC-C-1",
                source_extraction_id="EXT-C-1",
                document_sha256="b" * 64,
            ),
            contract_roles=_contract_roles(),
        )


def test_request_rejects_source_document_mismatch():
    with pytest.raises(ValidationError):
        DirectV3ProductionRequest(
            intake=_invoice_intake(),
            invoice_evidence=_invoice_evidence(source_document_id="OTHER"),
        )


def test_request_rejects_source_extraction_mismatch():
    with pytest.raises(ValidationError):
        DirectV3ProductionRequest(
            intake=_invoice_intake(),
            invoice_evidence=_invoice_evidence(source_extraction_id="OTHER"),
        )


def test_request_accepts_sha_case_normalization():
    intake = _invoice_intake().model_copy(update={"document_sha256": "A" * 64})
    request = DirectV3ProductionRequest(
        intake=intake,
        invoice_evidence=_invoice_evidence(document_sha256="a" * 64),
    )
    assert request.intake.document_sha256 == "A" * 64


def test_capture_fact_invariants():
    snap = capture_fact_invariants(_fact())
    assert snap.fact_id == 101
    assert snap.business_identity_key == "INVOICE|KEY"
    assert snap.version_no == 1
    assert snap.supersedes_fact_id is None


@pytest.mark.parametrize(
    ("attribute", "value", "code"),
    [
        ("id", 102, "FACT_ID_MUTATED"),
        ("business_identity_key", "OTHER", "BUSINESS_IDENTITY_MUTATED"),
        ("version_no", 2, "FACT_VERSION_MUTATED"),
        ("supersedes_fact_id", 88, "FACT_SUPERSESSION_MUTATED"),
    ],
)
def test_verify_fact_invariants_rejects_mutation(attribute, value, code):
    fact = _fact()
    before = capture_fact_invariants(fact)
    setattr(fact, attribute, value)
    with pytest.raises(DirectV3ProductionError) as exc:
        verify_fact_invariants(fact, before)
    assert exc.value.code == code


def test_completion_outcome_maps_noop_only_to_noop():
    assert completion_outcome("NOOP") == "NOOP"
    assert completion_outcome("COMPLETED") == "COMPLETED"
    assert completion_outcome("UPDATED") == "COMPLETED"


def test_invoice_orchestration_orders_guard_task24_task25_and_commits():
    log = []
    fact = _fact()
    db = FakeDB(fact)
    result25 = _invoice_result(status="VALID")
    service = DirectV3IngestService(
        db,
        route_guard=FakeGuard(log),
        intake_service=FakeIntake(log, _task24_result(document_type="invoice")),
        invoice_service=FakeInvoice(log, db, result25),
        contract_service=FakeContract(log, db, _contract_result()),
    )
    result = service.process(
        DirectV3ProductionRequest(
            intake=_invoice_intake(),
            invoice_evidence=_invoice_evidence(),
        )
    )
    assert log == ["guard", "task24", "task25"]
    assert result.validation_status == "VALID"
    assert result.outcome == "COMPLETED"
    assert db.commits == 1


def test_invoice_retry_child_noop_becomes_direct_noop_even_if_original_task24_created():
    log = []
    fact = _fact()
    db = FakeDB(fact)
    result25 = _invoice_result(outcome="NOOP", status="VALID")
    service = DirectV3IngestService(
        db,
        route_guard=FakeGuard(log),
        intake_service=FakeIntake(log, _task24_result(document_type="invoice", status="CREATED")),
        invoice_service=FakeInvoice(log, db, result25),
        contract_service=FakeContract(log, db, _contract_result()),
    )
    result = service.process(
        DirectV3ProductionRequest(
            intake=_invoice_intake(),
            invoice_evidence=_invoice_evidence(),
        )
    )
    assert result.outcome == "NOOP"


def test_contract_orchestration_orders_guard_task24_task26():
    log = []
    fact = _fact("CONTRACT|KEY")
    db = FakeDB(fact)
    service = DirectV3IngestService(
        db,
        route_guard=FakeGuard(log),
        intake_service=FakeIntake(log, _task24_result(document_type="contract")),
        invoice_service=FakeInvoice(log, db, _invoice_result()),
        contract_service=FakeContract(log, db, _contract_result()),
    )
    result = service.process(
        DirectV3ProductionRequest(
            intake=_contract_intake(),
            contract_roles=_contract_roles(),
        )
    )
    assert log == ["guard", "task24", "task26"]
    assert result.validation_status == "NEEDS_REVIEW"


def test_task24_rejected_does_not_call_completion_service():
    log = []
    fact = _fact()
    db = FakeDB(fact)
    service = DirectV3IngestService(
        db,
        route_guard=FakeGuard(log),
        intake_service=FakeIntake(log, _task24_result(document_type="invoice", status="REJECTED")),
        invoice_service=FakeInvoice(log, db, _invoice_result()),
        contract_service=FakeContract(log, db, _contract_result()),
    )
    result = service.process(
        DirectV3ProductionRequest(
            intake=_invoice_intake(),
            invoice_evidence=_invoice_evidence(),
        )
    )
    assert result.outcome == "REJECTED"
    assert log == ["guard", "task24"]


def test_contract_cannot_be_promoted_valid_by_task27():
    log = []
    fact = _fact("CONTRACT|KEY")
    db = FakeDB(fact)
    result26 = _contract_result(status="VALID")
    service = DirectV3IngestService(
        db,
        route_guard=FakeGuard(log),
        intake_service=FakeIntake(log, _task24_result(document_type="contract")),
        invoice_service=FakeInvoice(log, db, _invoice_result()),
        contract_service=FakeContract(log, db, result26),
    )
    with pytest.raises(DirectV3ProductionError) as exc:
        service.process(
            DirectV3ProductionRequest(
                intake=_contract_intake(),
                contract_roles=_contract_roles(),
            )
        )
    assert exc.value.code == "CONTRACT_VALIDATION_SCOPE_VIOLATION"
    assert db.rollbacks == 1


def test_task25_cannot_return_different_fact():
    log = []
    fact = _fact()
    db = FakeDB(fact)
    service = DirectV3IngestService(
        db,
        route_guard=FakeGuard(log),
        intake_service=FakeIntake(log, _task24_result(document_type="invoice")),
        invoice_service=FakeInvoice(log, db, _invoice_result(fact_id=999)),
        contract_service=FakeContract(log, db, _contract_result()),
    )
    with pytest.raises(DirectV3ProductionError) as exc:
        service.process(
            DirectV3ProductionRequest(
                intake=_invoice_intake(),
                invoice_evidence=_invoice_evidence(),
            )
        )
    assert exc.value.code == "TASK25_FACT_DRIFT"


def test_task26_cannot_return_different_identity():
    log = []
    fact = _fact("CONTRACT|KEY")
    db = FakeDB(fact)
    service = DirectV3IngestService(
        db,
        route_guard=FakeGuard(log),
        intake_service=FakeIntake(log, _task24_result(document_type="contract")),
        invoice_service=FakeInvoice(log, db, _invoice_result()),
        contract_service=FakeContract(log, db, _contract_result(key="CONTRACT|OTHER")),
    )
    with pytest.raises(DirectV3ProductionError) as exc:
        service.process(
            DirectV3ProductionRequest(
                intake=_contract_intake(),
                contract_roles=_contract_roles(),
            )
        )
    assert exc.value.code == "TASK26_IDENTITY_DRIFT"


def test_completion_exception_rolls_back_when_service_owns_commit():
    log = []
    fact = _fact()
    db = FakeDB(fact)
    service = DirectV3IngestService(
        db,
        route_guard=FakeGuard(log),
        intake_service=FakeIntake(log, _task24_result(document_type="invoice")),
        invoice_service=FakeInvoice(log, db, _invoice_result(), exc=RuntimeError("boom")),
        contract_service=FakeContract(log, db, _contract_result()),
    )
    with pytest.raises(RuntimeError, match="boom"):
        service.process(
            DirectV3ProductionRequest(
                intake=_invoice_intake(),
                invoice_evidence=_invoice_evidence(),
            )
        )
    assert db.rollbacks == 1
    assert db.commits == 0


def test_commit_false_leaves_outer_transaction_ownership_to_caller():
    log = []
    fact = _fact()
    db = FakeDB(fact)
    service = DirectV3IngestService(
        db,
        route_guard=FakeGuard(log),
        intake_service=FakeIntake(log, _task24_result(document_type="invoice")),
        invoice_service=FakeInvoice(log, db, _invoice_result()),
        contract_service=FakeContract(log, db, _contract_result()),
    )
    service.process(
        DirectV3ProductionRequest(
            intake=_invoice_intake(),
            invoice_evidence=_invoice_evidence(),
        ),
        commit=False,
    )
    assert db.commits == 0
    assert db.rollbacks == 0
