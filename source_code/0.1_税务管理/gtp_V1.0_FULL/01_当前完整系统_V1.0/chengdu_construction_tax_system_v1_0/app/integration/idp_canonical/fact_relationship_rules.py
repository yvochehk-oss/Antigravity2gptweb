"""Pure Task30 relationship direction, type, and evidence fingerprint rules."""
from __future__ import annotations

from hashlib import sha256
import json
import unicodedata

from .fact_relationship_schemas import FactRelationshipEvidenceInput


TASK30_FACT_RELATIONSHIP_RULESET_V1 = "TASK30_FACT_RELATIONSHIP_RULESET_V1"
INVOICE_CONTRACT_RELATION_V1 = "INVOICE_CONTRACT_RELATION_V1"
PAYMENT_INVOICE_RELATION_V1 = "PAYMENT_INVOICE_RELATION_V1"
PAYMENT_CONTRACT_RELATION_V1 = "PAYMENT_CONTRACT_RELATION_V1"

RELATIONSHIP_FACT_TYPES: dict[str, tuple[str, str]] = {
    "INVOICE_FOR_CONTRACT": ("INVOICE", "CONTRACT"),
    "PAYMENT_FOR_INVOICE": ("PAYMENT", "INVOICE"),
    "PAYMENT_FOR_CONTRACT": ("PAYMENT", "CONTRACT"),
}

RELATIONSHIP_SEMANTIC_VERSION: dict[str, str] = {
    "INVOICE_FOR_CONTRACT": INVOICE_CONTRACT_RELATION_V1,
    "PAYMENT_FOR_INVOICE": PAYMENT_INVOICE_RELATION_V1,
    "PAYMENT_FOR_CONTRACT": PAYMENT_CONTRACT_RELATION_V1,
}


class FactRelationshipRuleError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def validate_relationship_direction(
    *,
    source_fact_id: int,
    source_fact_type: str,
    target_fact_id: int,
    target_fact_type: str,
    relationship_type: str,
) -> str:
    if int(source_fact_id) == int(target_fact_id):
        raise FactRelationshipRuleError(
            "SELF_RELATIONSHIP_FORBIDDEN",
            "a Canonical Fact cannot relate to itself",
        )
    expected = RELATIONSHIP_FACT_TYPES.get(relationship_type)
    if expected is None:
        raise FactRelationshipRuleError(
            "RELATIONSHIP_TYPE_UNSUPPORTED",
            f"Task30 does not manage relationship type {relationship_type!r}",
        )
    actual = (str(source_fact_type).upper(), str(target_fact_type).upper())
    if actual != expected:
        raise FactRelationshipRuleError(
            "RELATIONSHIP_DIRECTION_INVALID",
            f"{relationship_type} requires {expected[0]} -> {expected[1]}, got {actual[0]} -> {actual[1]}",
        )
    return RELATIONSHIP_SEMANTIC_VERSION[relationship_type]


def reference_descriptor(evidence: FactRelationshipEvidenceInput) -> tuple[str, str]:
    if evidence.target_fact_id is not None:
        return "TARGET_FACT_ID", str(int(evidence.target_fact_id))
    if evidence.business_identity_key is not None:
        return "BUSINESS_IDENTITY_KEY", evidence.business_identity_key
    if evidence.invoice_identity_key is not None:
        return "INVOICE_IDENTITY_KEY", evidence.invoice_identity_key
    if evidence.contract_business_identity_key is not None:
        return "CONTRACT_BUSINESS_IDENTITY_KEY", evidence.contract_business_identity_key
    raise FactRelationshipRuleError(
        "REFERENCE_MISSING",
        "one explicit target reference is required",
    )


def normalize_evidence_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def evidence_fingerprint(
    *,
    source_fact_id: int,
    relationship_type: str,
    source_document_id: int,
    source_extraction_id: str,
    evidence: FactRelationshipEvidenceInput,
) -> str:
    reference_type, reference_value = reference_descriptor(evidence)
    payload = {
        "source_fact_id": int(source_fact_id),
        "relationship_type": relationship_type,
        "source_document_id": int(source_document_id),
        "source_extraction_id": source_extraction_id,
        "reference_type": reference_type,
        "reference_value": reference_value,
        "evidence_text": normalize_evidence_text(evidence.evidence_text),
        "page_no": evidence.page_no,
        "ruleset_version": TASK30_FACT_RELATIONSHIP_RULESET_V1,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()
