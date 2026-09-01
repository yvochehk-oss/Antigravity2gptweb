from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.integration.idp_canonical.contract_role_resolver import (
    CONTRACT_ROLE_RULESET_V1,
    classify_explicit_legal_role,
    contract_role_evidence_fingerprint,
    evidence_set_fingerprint,
    resolve_contract_roles,
)
from app.integration.idp_canonical.contract_role_schemas import (
    ContractRoleCompletionRequest,
    ContractRoleEvidenceInput,
)
from app.integration.idp_canonical.contract_role_service import (
    ContractRoleServiceError,
    PreviousResolutionState,
    apply_contract_role_projection,
)


def line(role="PARTY_A", label="甲方（发包人）", text="甲方（发包人）负责发包。", confidence=0.99):
    return ContractRoleEvidenceInput(
        source_party_role=role,
        legal_role_label=label,
        evidence_text=text,
        page_no=1,
        confidence=confidence,
    )


def row(role, party_id, canonical_role, *, status="MAPPED", label="发包人", fp=None):
    return SimpleNamespace(
        source_party_role=role,
        party_id=party_id,
        canonical_role=canonical_role,
        classification_status=status,
        legal_role_label=label,
        evidence_fingerprint=fp or ("a" if role == "PARTY_A" else "b") * 64,
    )


def fact(status="NEEDS_REVIEW", *, version=1, supersedes=None, current=True):
    return SimpleNamespace(
        id=100,
        fact_type="CONTRACT",
        business_identity_key="CONTRACT|CONTRACT_PARTIES_V1|abc",
        version_no=version,
        supersedes_fact_id=supersedes,
        is_current=current,
        validation_status=status,
    )


def contract(buyer=None, seller=None):
    return SimpleNamespace(
        fact_id=100,
        buyer_party_id=buyer,
        seller_party_id=seller,
        contract_category=None,
    )


def resolved_ab():
    return resolve_contract_roles(
        [
            row("PARTY_A", 10, "BUYER", fp="a" * 64),
            row("PARTY_B", 20, "SELLER", fp="b" * 64),
        ]
    )


def test_ruleset_version():
    assert CONTRACT_ROLE_RULESET_V1 == "CONTRACT_ROLE_RULESET_V1"


@pytest.mark.parametrize(
    ("label", "text", "expected"),
    [
        ("采购人", "采购人负责采购。", "BUYER"),
        ("甲方（发包人）", "甲方（发包人）负责发包。", "BUYER"),
        ("买方", "买方负责付款。", "BUYER"),
        ("供应商", "供应商负责供货。", "SELLER"),
        ("乙方（承包人）", "乙方（承包人）负责施工。", "SELLER"),
        ("卖方", "卖方负责交付。", "SELLER"),
    ],
)
def test_explicit_roles_map(label, text, expected):
    result = classify_explicit_legal_role(
        legal_role_label=label,
        evidence_text=text,
    )
    assert result.status == "MAPPED"
    assert result.canonical_role == expected


@pytest.mark.parametrize(
    ("label", "text"),
    [("甲方", "合同列示甲方。"), ("乙方", "合同列示乙方。")],
)
def test_position_only_does_not_map(label, text):
    result = classify_explicit_legal_role(
        legal_role_label=label,
        evidence_text=text,
    )
    assert result.status == "UNSUPPORTED"
    assert result.canonical_role is None


def test_role_label_must_exist_in_evidence_text():
    result = classify_explicit_legal_role(
        legal_role_label="采购人",
        evidence_text="这里只写项目名称。",
    )
    assert result.status == "INVALID"
    assert result.code == "ROLE_LABEL_NOT_IN_EVIDENCE_TEXT"


def test_mixed_role_label_is_ambiguous():
    result = classify_explicit_legal_role(
        legal_role_label="甲方（采购人、供应商）",
        evidence_text="甲方（采购人、供应商）。",
    )
    assert result.status == "AMBIGUOUS"
    assert result.canonical_role is None


def test_request_normalizes_sha_and_rejects_bad_sha():
    req = ContractRoleCompletionRequest(
        source_document_id="doc",
        source_extraction_id="ext",
        document_sha256="A" * 64,
        submitted_by="reviewer",
    )
    assert req.document_sha256 == "a" * 64

    with pytest.raises(ValidationError):
        ContractRoleCompletionRequest(
            source_document_id="doc",
            source_extraction_id="ext",
            document_sha256="bad",
            submitted_by="reviewer",
        )


def test_evidence_fingerprint_ignores_confidence_but_tracks_source_event():
    first = contract_role_evidence_fingerprint(
        fact_id=1,
        receipt_id=11,
        source_system="IDP",
        source_document_id="DOC",
        source_extraction_id="EXT",
        source_party_role="PARTY_A",
        party_id=10,
        evidence=line(confidence=0.99),
    )
    same = contract_role_evidence_fingerprint(
        fact_id=1,
        receipt_id=11,
        source_system="IDP",
        source_document_id="DOC",
        source_extraction_id="EXT",
        source_party_role="PARTY_A",
        party_id=10,
        evidence=line(confidence=0.5),
    )
    other_event = contract_role_evidence_fingerprint(
        fact_id=1,
        receipt_id=12,
        source_system="IDP",
        source_document_id="DOC",
        source_extraction_id="EXT2",
        source_party_role="PARTY_A",
        party_id=10,
        evidence=line(confidence=0.99),
    )
    assert first == same
    assert first != other_event


def test_evidence_set_fingerprint_order_independent():
    left = evidence_set_fingerprint(
        [
            row("PARTY_A", 10, "BUYER", fp="a" * 64),
            row("PARTY_B", 20, "SELLER", fp="b" * 64),
        ]
    )
    right = evidence_set_fingerprint(
        [
            row("PARTY_B", 20, "SELLER", fp="b" * 64),
            row("PARTY_A", 10, "BUYER", fp="a" * 64),
        ]
    )
    assert left == right


def test_a_buyer_b_seller_resolves():
    decision = resolved_ab()
    assert decision.status == "RESOLVED"
    assert decision.buyer_party_id == 10
    assert decision.seller_party_id == 20


def test_reverse_orientation_resolves_without_position_assumption():
    decision = resolve_contract_roles(
        [
            row("PARTY_A", 10, "SELLER", fp="a" * 64),
            row("PARTY_B", 20, "BUYER", fp="b" * 64),
        ]
    )
    assert decision.status == "RESOLVED"
    assert decision.buyer_party_id == 20
    assert decision.seller_party_id == 10


def test_missing_evidence_stays_needs_review():
    decision = resolve_contract_roles(
        [row("PARTY_A", 10, "BUYER")]
    )
    assert decision.status == "NEEDS_REVIEW"
    assert decision.reason_code == "ROLE_EVIDENCE_INSUFFICIENT"
    assert decision.buyer_party_id is None
    assert decision.seller_party_id is None


def test_unsupported_position_evidence_stays_needs_review():
    decision = resolve_contract_roles(
        [
            row("PARTY_A", 10, None, status="UNSUPPORTED", label="甲方", fp="a" * 64),
            row("PARTY_B", 20, None, status="UNSUPPORTED", label="乙方", fp="b" * 64),
        ]
    )
    assert decision.status == "NEEDS_REVIEW"
    assert decision.buyer_party_id is None
    assert decision.seller_party_id is None


@pytest.mark.parametrize(
    "rows",
    [
        [
            row("PARTY_A", 10, "BUYER", fp="a" * 64),
            row("PARTY_A", 10, "SELLER", fp="b" * 64),
            row("PARTY_B", 20, "SELLER", fp="c" * 64),
        ],
        [
            row("PARTY_A", 10, "BUYER", fp="a" * 64),
            row("PARTY_B", 20, "BUYER", fp="b" * 64),
        ],
        [
            row("PARTY_A", 10, "BUYER", fp="a" * 64),
            row("PARTY_B", 10, "SELLER", fp="b" * 64),
        ],
        [
            row("PARTY_A", 10, "BUYER", fp="a" * 64),
            row("PARTY_B", 20, "SELLER", fp="b" * 64),
            row("PARTY_A", 10, None, status="INVALID", label="采购人", fp="c" * 64),
        ],
    ],
)
def test_conflicts_fail_closed(rows):
    decision = resolve_contract_roles(rows)
    assert decision.status == "NEEDS_REVIEW"
    assert decision.reason_code == "ROLE_EVIDENCE_CONFLICT"
    assert decision.buyer_party_id is None
    assert decision.seller_party_id is None


def test_projection_backfills_same_fact_and_keeps_needs_review():
    f = fact()
    c = contract()
    original = (
        f.id,
        f.business_identity_key,
        f.version_no,
        f.supersedes_fact_id,
    )

    changed = apply_contract_role_projection(
        fact=f,
        contract=c,
        decision=resolved_ab(),
        previous_resolution=None,
    )

    assert changed is True
    assert c.buyer_party_id == 10
    assert c.seller_party_id == 20
    assert f.validation_status == "NEEDS_REVIEW"
    assert (
        f.id,
        f.business_identity_key,
        f.version_no,
        f.supersedes_fact_id,
    ) == original


def test_draft_never_promotes_valid():
    f = fact("DRAFT")
    c = contract()
    apply_contract_role_projection(
        fact=f,
        contract=c,
        decision=resolved_ab(),
        previous_resolution=None,
    )
    assert f.validation_status == "NEEDS_REVIEW"


def test_later_conflict_clears_only_task26_owned_projection():
    f = fact()
    c = contract(10, 20)
    conflict = resolve_contract_roles(
        [
            row("PARTY_A", 10, "BUYER", fp="a" * 64),
            row("PARTY_A", 10, "SELLER", fp="c" * 64),
            row("PARTY_B", 20, "SELLER", fp="b" * 64),
        ]
    )
    changed = apply_contract_role_projection(
        fact=f,
        contract=c,
        decision=conflict,
        previous_resolution=PreviousResolutionState(
            resolution_status="RESOLVED",
            buyer_party_id=10,
            seller_party_id=20,
        ),
    )
    assert changed is True
    assert c.buyer_party_id is None
    assert c.seller_party_id is None
    assert f.validation_status == "NEEDS_REVIEW"


def test_unmanaged_existing_roles_are_never_overwritten_or_cleared():
    f = fact()
    c = contract(99, 98)

    with pytest.raises(ContractRoleServiceError) as exc:
        apply_contract_role_projection(
            fact=f,
            contract=c,
            decision=resolved_ab(),
            previous_resolution=None,
        )
    assert exc.value.code == "EXISTING_CONTRACT_ROLE_CONFLICT"

    with pytest.raises(ContractRoleServiceError):
        apply_contract_role_projection(
            fact=f,
            contract=c,
            decision=resolve_contract_roles([]),
            previous_resolution=None,
        )


@pytest.mark.parametrize(
    "candidate",
    [
        fact(version=2, supersedes=1),
        fact(current=False),
        fact("VALID"),
        fact("INVALID"),
    ],
)
def test_out_of_scope_fact_states_rejected(candidate):
    with pytest.raises(ContractRoleServiceError):
        apply_contract_role_projection(
            fact=candidate,
            contract=contract(),
            decision=resolved_ab(),
            previous_resolution=None,
        )
