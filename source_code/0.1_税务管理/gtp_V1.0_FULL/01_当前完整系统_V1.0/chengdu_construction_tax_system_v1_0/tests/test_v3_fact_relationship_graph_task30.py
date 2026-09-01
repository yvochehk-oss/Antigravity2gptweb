"""Task30 focused tests: exact-only relationship rules and safety boundaries."""
from __future__ import annotations

from pathlib import Path
import inspect
import re

import pytest
from pydantic import ValidationError

from app.integration.idp_canonical.fact_relationship_resolver import FactRelationshipResolver
from app.integration.idp_canonical.fact_relationship_rules import (
    INVOICE_CONTRACT_RELATION_V1,
    PAYMENT_CONTRACT_RELATION_V1,
    PAYMENT_INVOICE_RELATION_V1,
    TASK30_FACT_RELATIONSHIP_RULESET_V1,
    FactRelationshipRuleError,
    evidence_fingerprint,
    reference_descriptor,
    validate_relationship_direction,
)
from app.integration.idp_canonical.fact_relationship_schemas import (
    FactRelationshipCompletionRequest,
    FactRelationshipEvidenceInput,
)
from app.integration.idp_canonical.fact_relationship_service import FactRelationshipService
from app.v3_fact_models import FactRelationship
from app.v3_fact_relationship_evidence_models import FactRelationshipEvidence


def ev(**kwargs):
    return FactRelationshipEvidenceInput(
        evidence_text="合同/发票/付款凭证中明确列示该精确引用",
        **kwargs,
    )


@pytest.mark.parametrize(
    ("rel", "source_type", "target_type", "semantic"),
    [
        ("INVOICE_FOR_CONTRACT", "INVOICE", "CONTRACT", INVOICE_CONTRACT_RELATION_V1),
        ("PAYMENT_FOR_INVOICE", "PAYMENT", "INVOICE", PAYMENT_INVOICE_RELATION_V1),
        ("PAYMENT_FOR_CONTRACT", "PAYMENT", "CONTRACT", PAYMENT_CONTRACT_RELATION_V1),
    ],
)
def test_allowed_direction(rel, source_type, target_type, semantic):
    assert validate_relationship_direction(
        source_fact_id=1,
        source_fact_type=source_type,
        target_fact_id=2,
        target_fact_type=target_type,
        relationship_type=rel,
    ) == semantic


@pytest.mark.parametrize(
    ("rel", "source_type", "target_type"),
    [
        ("INVOICE_FOR_CONTRACT", "PAYMENT", "CONTRACT"),
        ("INVOICE_FOR_CONTRACT", "INVOICE", "INVOICE"),
        ("INVOICE_FOR_CONTRACT", "CONTRACT", "INVOICE"),
        ("PAYMENT_FOR_INVOICE", "INVOICE", "PAYMENT"),
        ("PAYMENT_FOR_INVOICE", "PAYMENT", "CONTRACT"),
        ("PAYMENT_FOR_INVOICE", "PAYMENT", "PAYMENT"),
        ("PAYMENT_FOR_CONTRACT", "INVOICE", "CONTRACT"),
        ("PAYMENT_FOR_CONTRACT", "PAYMENT", "INVOICE"),
        ("PAYMENT_FOR_CONTRACT", "CONTRACT", "PAYMENT"),
    ],
)
def test_wrong_direction_fails_closed(rel, source_type, target_type):
    with pytest.raises(FactRelationshipRuleError) as exc:
        validate_relationship_direction(
            source_fact_id=1,
            source_fact_type=source_type,
            target_fact_id=2,
            target_fact_type=target_type,
            relationship_type=rel,
        )
    assert exc.value.code == "RELATIONSHIP_DIRECTION_INVALID"


def test_self_loop_rejected_before_type_inference():
    with pytest.raises(FactRelationshipRuleError) as exc:
        validate_relationship_direction(
            source_fact_id=7,
            source_fact_type="PAYMENT",
            target_fact_id=7,
            target_fact_type="PAYMENT",
            relationship_type="PAYMENT_FOR_INVOICE",
        )
    assert exc.value.code == "SELF_RELATIONSHIP_FORBIDDEN"


def test_old_relationship_type_not_managed_by_task30():
    with pytest.raises(FactRelationshipRuleError) as exc:
        validate_relationship_direction(
            source_fact_id=1,
            source_fact_type="INVOICE",
            target_fact_id=2,
            target_fact_type="CONTRACT",
            relationship_type="REPLACES",
        )
    assert exc.value.code == "RELATIONSHIP_TYPE_UNSUPPORTED"


@pytest.mark.parametrize(
    ("kwargs", "expected_type", "expected_value"),
    [
        ({"target_fact_id": 88}, "TARGET_FACT_ID", "88"),
        ({"business_identity_key": "FACT|A"}, "BUSINESS_IDENTITY_KEY", "FACT|A"),
        ({"invoice_identity_key": "INV|A"}, "INVOICE_IDENTITY_KEY", "INV|A"),
        ({"contract_business_identity_key": "CONTRACT|A"}, "CONTRACT_BUSINESS_IDENTITY_KEY", "CONTRACT|A"),
    ],
)
def test_reference_descriptors(kwargs, expected_type, expected_value):
    assert reference_descriptor(ev(**kwargs)) == (expected_type, expected_value)


def test_evidence_requires_exactly_one_reference():
    with pytest.raises(ValidationError):
        ev()
    with pytest.raises(ValidationError):
        ev(target_fact_id=1, business_identity_key="A")


def test_request_requires_integer_source_document_fk():
    request = FactRelationshipCompletionRequest(
        source_fact_id=1,
        relationship_type="INVOICE_FOR_CONTRACT",
        source_document_id=123,
        source_extraction_id="IDP-EX-1",
        submitted_by="reviewer",
        evidences=[ev(target_fact_id=2)],
    )
    assert isinstance(request.source_document_id, int)
    with pytest.raises(ValidationError):
        FactRelationshipCompletionRequest(
            source_fact_id=1,
            relationship_type="INVOICE_FOR_CONTRACT",
            source_document_id="550e8400-e29b-41d4-a716-446655440000",
            source_extraction_id="IDP-EX-1",
            submitted_by="reviewer",
            evidences=[ev(target_fact_id=2)],
        )


def test_request_forbids_guessing_fields():
    with pytest.raises(ValidationError):
        FactRelationshipCompletionRequest(
            source_fact_id=1,
            relationship_type="INVOICE_FOR_CONTRACT",
            source_document_id=1,
            source_extraction_id="EX",
            submitted_by="reviewer",
            evidences=[ev(target_fact_id=2)],
            project_code="P001",
        )


def test_fingerprint_is_deterministic_and_ignores_confidence():
    one = ev(invoice_identity_key="INV-X", confidence=0.2)
    two = ev(invoice_identity_key="INV-X", confidence=0.99)
    args = dict(source_fact_id=1, relationship_type="PAYMENT_FOR_INVOICE", source_document_id=9, source_extraction_id="EX")
    assert evidence_fingerprint(evidence=one, **args) == evidence_fingerprint(evidence=two, **args)


@pytest.mark.parametrize("mutation", [{"source_fact_id": 2}, {"relationship_type": "PAYMENT_FOR_CONTRACT"}, {"source_document_id": 10}, {"source_extraction_id": "EX2"}])
def test_fingerprint_changes_on_source_identity_mutation(mutation):
    evidence = ev(invoice_identity_key="INV-X")
    args = dict(source_fact_id=1, relationship_type="PAYMENT_FOR_INVOICE", source_document_id=9, source_extraction_id="EX")
    original = evidence_fingerprint(evidence=evidence, **args)
    args.update(mutation)
    assert evidence_fingerprint(evidence=evidence, **args) != original


def test_fingerprint_changes_on_reference():
    args = dict(source_fact_id=1, relationship_type="PAYMENT_FOR_INVOICE", source_document_id=9, source_extraction_id="EX")
    assert evidence_fingerprint(evidence=ev(invoice_identity_key="INV-A"), **args) != evidence_fingerprint(evidence=ev(invoice_identity_key="INV-B"), **args)


def test_fingerprint_changes_on_evidence_text_or_page():
    args = dict(source_fact_id=1, relationship_type="INVOICE_FOR_CONTRACT", source_document_id=9, source_extraction_id="EX")
    a = FactRelationshipEvidenceInput(contract_business_identity_key="C", evidence_text="明确引用合同 C", page_no=1)
    b = FactRelationshipEvidenceInput(contract_business_identity_key="C", evidence_text="明确引用合同 C 的补充条款", page_no=1)
    c = a.model_copy(update={"page_no": 2})
    assert evidence_fingerprint(evidence=a, **args) != evidence_fingerprint(evidence=b, **args)
    assert evidence_fingerprint(evidence=a, **args) != evidence_fingerprint(evidence=c, **args)


def test_ruleset_is_versioned():
    assert TASK30_FACT_RELATIONSHIP_RULESET_V1 == "TASK30_FACT_RELATIONSHIP_RULESET_V1"


def test_orm_relationship_type_metadata_contains_task30_types():
    checks = [str(item.sqltext) for item in FactRelationship.__table__.constraints if hasattr(item, "sqltext")]
    joined = "\n".join(checks)
    for value in ("INVOICE_FOR_CONTRACT", "PAYMENT_FOR_INVOICE", "PAYMENT_FOR_CONTRACT"):
        assert value in joined


def test_evidence_orm_uses_integer_source_document_fk():
    column = FactRelationshipEvidence.__table__.c.source_document_id
    assert column.type.python_type is int
    assert any(str(fk.column) == "source_documents.id" for fk in column.foreign_keys)


def test_evidence_orm_relationship_fk():
    column = FactRelationshipEvidence.__table__.c.relationship_id
    assert any(str(fk.column) == "fact_relationships.id" for fk in column.foreign_keys)


def test_resolver_has_no_project_or_party_matching():
    source = inspect.getsource(FactRelationshipResolver)
    assert "Project" not in source
    assert "Party" not in source
    assert "amount" not in source.lower()
    assert "date_proximity" not in source.lower()
    assert "invoice_date" not in source.lower()


def test_resolver_has_no_fuzzy_matching():
    source = inspect.getsource(FactRelationshipResolver).lower()
    for forbidden in ("ilike", "similarity(", "levenshtein", "embedding", "fuzzy"):
        assert forbidden not in source


def test_resolver_uses_exact_invoice_identity_equality():
    source = inspect.getsource(FactRelationshipResolver)
    assert "InvoiceFact.invoice_identity_key == reference_value" in source


def test_resolver_uses_exact_business_identity_equality():
    source = inspect.getsource(FactRelationshipResolver)
    assert "Fact.business_identity_key == reference_value" in source


def test_service_does_not_modify_fact_validation_or_supersession():
    source = inspect.getsource(FactRelationshipService)
    assert re.search(r"\.validation_status\s*=(?!=)", source) is None
    assert re.search(r"\.supersedes_fact_id\s*=(?!=)", source) is None
    assert "Fact(" not in source


def test_service_does_not_use_project_allocation_or_tax_analysis():
    source = inspect.getsource(FactRelationshipService)
    assert "FactProjectAllocation" not in source
    assert "ProjectTaxAnalysis" not in source
    assert "Project(" not in source


def test_service_prepares_before_graph_write():
    source = inspect.getsource(FactRelationshipService._complete)
    assert source.index("prepared_result = self._prepare") < source.index("# All fail-closed validation is complete")


def test_migration_98_contract():
    root = Path(__file__).resolve().parents[1]
    path = root / "alembic" / "versions" / "98_v3_explicit_fact_relationship_graph.py"
    text = path.read_text(encoding="utf-8")
    assert 'down_revision = "97_v3_contract_role_semantics"' in text
    assert '"fact_relationship_evidence"' in text
    assert '"source_documents.id"' in text
    assert '"fact_relationships.id"' in text
    for value in ("INVOICE_FOR_CONTRACT", "PAYMENT_FOR_INVOICE", "PAYMENT_FOR_CONTRACT"):
        assert value in text


def test_migration_98_has_evidence_indexes_and_uniques():
    root = Path(__file__).resolve().parents[1]
    text = (root / "alembic" / "versions" / "98_v3_explicit_fact_relationship_graph.py").read_text(encoding="utf-8")
    assert "uq_fact_relationship_evidence_relationship_fingerprint" in text
    assert "uq_fact_relationship_evidence_source_event_fingerprint" in text
    assert "ix_fact_relationship_evidence_source_document_id" in text
    assert "ix_fact_relationship_evidence_source_extraction" in text
