"""Deterministic Task26 Contract legal-role resolver.

Only explicit controlled legal/business labels are mapped. PARTY_A/PARTY_B,
甲方/乙方 alone, Party names, Party type, amount direction, fuzzy matching,
and LLM guesses are never role evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable

from .contract_role_schemas import ContractRoleEvidenceInput


CONTRACT_ROLE_RULESET_V1 = "CONTRACT_ROLE_RULESET_V1"

_BUYER_ROLE_LABELS = frozenset(
    {
        "买方",
        "买受人",
        "购买方",
        "采购人",
        "采购方",
        "购货方",
        "订购方",
        "发包人",
        "发包方",
    }
)

_SELLER_ROLE_LABELS = frozenset(
    {
        "卖方",
        "出卖人",
        "销售方",
        "供方",
        "供货方",
        "供应商",
        "承包人",
        "承包方",
    }
)

_CONTROLLED_ROLE_MAP: dict[str, str] = {
    **{label: "BUYER" for label in _BUYER_ROLE_LABELS},
    **{label: "SELLER" for label in _SELLER_ROLE_LABELS},
}


class ContractRoleResolutionError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RoleClassification:
    canonical_role: str | None
    status: str
    code: str
    matched_labels: tuple[str, ...]


@dataclass(frozen=True)
class ContractRoleDecision:
    status: str
    buyer_party_id: int | None
    seller_party_id: int | None
    reason_code: str
    reason_detail: str
    findings: tuple[tuple[str, str], ...]


def normalize_role_text(value: Any) -> str:
    normalized = unicodedata.normalize(
        "NFKC",
        "" if value is None else str(value),
    )
    return re.sub(r"\s+", "", normalized)


def _label_tokens(value: str) -> tuple[str, ...]:
    normalized = normalize_role_text(value)
    return tuple(
        token
        for token in re.split(
            r"[\(\)\[\]{}<>《》【】:：,，、/\\;；|]+",
            normalized,
        )
        if token
    )


def classify_explicit_legal_role(
    *,
    legal_role_label: str,
    evidence_text: str,
) -> RoleClassification:
    normalized_label = normalize_role_text(legal_role_label)
    normalized_evidence = normalize_role_text(evidence_text)

    if not normalized_label or not normalized_evidence:
        return RoleClassification(
            canonical_role=None,
            status="INVALID",
            code="ROLE_EVIDENCE_EMPTY",
            matched_labels=(),
        )

    if normalized_label not in normalized_evidence:
        return RoleClassification(
            canonical_role=None,
            status="INVALID",
            code="ROLE_LABEL_NOT_IN_EVIDENCE_TEXT",
            matched_labels=(),
        )

    matched_labels = tuple(
        sorted(
            {
                token
                for token in _label_tokens(normalized_label)
                if token in _CONTROLLED_ROLE_MAP
            }
        )
    )

    if not matched_labels:
        return RoleClassification(
            canonical_role=None,
            status="UNSUPPORTED",
            code="ROLE_LABEL_UNSUPPORTED",
            matched_labels=(),
        )

    mapped_roles = {
        _CONTROLLED_ROLE_MAP[label]
        for label in matched_labels
    }

    if len(mapped_roles) != 1:
        return RoleClassification(
            canonical_role=None,
            status="AMBIGUOUS",
            code="ROLE_LABEL_AMBIGUOUS",
            matched_labels=matched_labels,
        )

    return RoleClassification(
        canonical_role=next(iter(mapped_roles)),
        status="MAPPED",
        code="ROLE_MAPPED",
        matched_labels=matched_labels,
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0]),
            )
        }

    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]

    return value


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def contract_role_evidence_fingerprint(
    *,
    fact_id: int,
    receipt_id: int,
    source_system: str,
    source_document_id: str,
    source_extraction_id: str,
    source_party_role: str,
    party_id: int,
    evidence: ContractRoleEvidenceInput,
) -> str:
    """Stable identity for a source-specific explicit legal-role statement."""

    return _fingerprint(
        {
            "ruleset_version": CONTRACT_ROLE_RULESET_V1,
            "fact_id": int(fact_id),
            "receipt_id": int(receipt_id),
            "source_system": source_system.strip().upper(),
            "source_document_id": source_document_id.strip(),
            "source_extraction_id": source_extraction_id.strip(),
            "source_party_role": source_party_role,
            "party_id": int(party_id),
            "evidence_type": evidence.evidence_type,
            "legal_role_label": normalize_role_text(evidence.legal_role_label),
            "evidence_text": normalize_role_text(evidence.evidence_text),
            "page_no": evidence.page_no,
        }
    )


def evidence_set_fingerprint(rows: Iterable[Any]) -> str:
    fingerprints = sorted(
        str(row.evidence_fingerprint)
        for row in rows
    )
    return _fingerprint(
        {
            "ruleset_version": CONTRACT_ROLE_RULESET_V1,
            "evidence_fingerprints": fingerprints,
        }
    )


def _finding(code: str, message: str) -> tuple[str, str]:
    return code, message


def resolve_contract_roles(rows: Iterable[Any]) -> ContractRoleDecision:
    evidence_rows = tuple(rows)
    findings: list[tuple[str, str]] = []

    if not evidence_rows:
        return ContractRoleDecision(
            status="NEEDS_REVIEW",
            buyer_party_id=None,
            seller_party_id=None,
            reason_code="ROLE_EVIDENCE_INSUFFICIENT",
            reason_detail="no explicit Contract legal-role evidence exists",
            findings=(
                _finding(
                    "ROLE_EVIDENCE_INSUFFICIENT",
                    "no explicit Contract legal-role evidence exists",
                ),
            ),
        )

    for row in evidence_rows:
        if row.classification_status == "UNSUPPORTED":
            findings.append(
                _finding(
                    "ROLE_LABEL_UNSUPPORTED",
                    (
                        f"{row.source_party_role} legal-role label "
                        f"{row.legal_role_label!r} is not mapped by "
                        f"{CONTRACT_ROLE_RULESET_V1}"
                    ),
                )
            )
        elif row.classification_status in {"AMBIGUOUS", "INVALID"}:
            findings.append(
                _finding(
                    "ROLE_EVIDENCE_CONFLICT",
                    (
                        f"{row.source_party_role} evidence "
                        f"{row.legal_role_label!r} is "
                        f"{row.classification_status.lower()}"
                    ),
                )
            )

    if any(
        row.classification_status in {"AMBIGUOUS", "INVALID"}
        for row in evidence_rows
    ):
        return ContractRoleDecision(
            status="NEEDS_REVIEW",
            buyer_party_id=None,
            seller_party_id=None,
            reason_code="ROLE_EVIDENCE_CONFLICT",
            reason_detail="ambiguous or invalid legal-role evidence requires review",
            findings=tuple(findings),
        )

    party_ids_by_source_role: dict[str, set[int]] = {
        "PARTY_A": set(),
        "PARTY_B": set(),
    }
    mapped_roles_by_source_role: dict[str, set[str]] = {
        "PARTY_A": set(),
        "PARTY_B": set(),
    }

    for row in evidence_rows:
        source_role = str(row.source_party_role)

        if source_role not in {"PARTY_A", "PARTY_B"}:
            return ContractRoleDecision(
                status="NEEDS_REVIEW",
                buyer_party_id=None,
                seller_party_id=None,
                reason_code="ROLE_EVIDENCE_CONFLICT",
                reason_detail="stored evidence contains an invalid source participant role",
                findings=tuple(
                    findings
                    + [
                        _finding(
                            "ROLE_EVIDENCE_CONFLICT",
                            f"invalid stored source participant role {source_role!r}",
                        )
                    ]
                ),
            )

        party_ids_by_source_role[source_role].add(int(row.party_id))

        if (
            row.classification_status == "MAPPED"
            and row.canonical_role is not None
        ):
            mapped_roles_by_source_role[source_role].add(
                str(row.canonical_role)
            )

    if any(len(ids) > 1 for ids in party_ids_by_source_role.values()):
        findings.append(
            _finding(
                "ROLE_EVIDENCE_PARTY_CONFLICT",
                "one Task24 source participant points to multiple Canonical Parties",
            )
        )
        return ContractRoleDecision(
            status="NEEDS_REVIEW",
            buyer_party_id=None,
            seller_party_id=None,
            reason_code="ROLE_EVIDENCE_CONFLICT",
            reason_detail="participant Party identity changed across role evidence",
            findings=tuple(findings),
        )

    if any(len(roles) > 1 for roles in mapped_roles_by_source_role.values()):
        findings.append(
            _finding(
                "ROLE_EVIDENCE_CONFLICT",
                "one Contract participant has explicit evidence for both BUYER and SELLER",
            )
        )
        return ContractRoleDecision(
            status="NEEDS_REVIEW",
            buyer_party_id=None,
            seller_party_id=None,
            reason_code="ROLE_EVIDENCE_CONFLICT",
            reason_detail="contradictory legal roles exist for one participant",
            findings=tuple(findings),
        )

    def one_party(source_role: str) -> int | None:
        values = party_ids_by_source_role[source_role]
        return next(iter(values)) if values else None

    def one_role(source_role: str) -> str | None:
        values = mapped_roles_by_source_role[source_role]
        return next(iter(values)) if values else None

    party_a = one_party("PARTY_A")
    party_b = one_party("PARTY_B")
    role_a = one_role("PARTY_A")
    role_b = one_role("PARTY_B")

    if (
        party_a is None
        or party_b is None
        or role_a is None
        or role_b is None
    ):
        findings.append(
            _finding(
                "ROLE_EVIDENCE_INSUFFICIENT",
                "both Contract participants require explicit supported legal-role evidence",
            )
        )
        return ContractRoleDecision(
            status="NEEDS_REVIEW",
            buyer_party_id=None,
            seller_party_id=None,
            reason_code="ROLE_EVIDENCE_INSUFFICIENT",
            reason_detail="explicit role evidence is incomplete",
            findings=tuple(findings),
        )

    if party_a == party_b:
        findings.append(
            _finding(
                "ROLE_EVIDENCE_PARTY_CONFLICT",
                "PARTY_A and PARTY_B resolve to the same Canonical Party",
            )
        )
        return ContractRoleDecision(
            status="NEEDS_REVIEW",
            buyer_party_id=None,
            seller_party_id=None,
            reason_code="ROLE_EVIDENCE_CONFLICT",
            reason_detail="Contract participants must be distinct",
            findings=tuple(findings),
        )

    if role_a == role_b:
        findings.append(
            _finding(
                "ROLE_EVIDENCE_CONFLICT",
                (
                    "both Contract participants resolve to the same "
                    f"canonical role {role_a}"
                ),
            )
        )
        return ContractRoleDecision(
            status="NEEDS_REVIEW",
            buyer_party_id=None,
            seller_party_id=None,
            reason_code="ROLE_EVIDENCE_CONFLICT",
            reason_detail="BUYER and SELLER must resolve to distinct participants",
            findings=tuple(findings),
        )

    if {role_a, role_b} != {"BUYER", "SELLER"}:
        raise ContractRoleResolutionError(
            "ROLE_RULESET_INTERNAL_ERROR",
            "resolved roles are outside BUYER/SELLER",
        )

    buyer_party_id = party_a if role_a == "BUYER" else party_b
    seller_party_id = party_a if role_a == "SELLER" else party_b

    findings.append(
        _finding(
            "ROLE_RESOLVED",
            f"buyer Party={buyer_party_id}; seller Party={seller_party_id}",
        )
    )

    return ContractRoleDecision(
        status="RESOLVED",
        buyer_party_id=buyer_party_id,
        seller_party_id=seller_party_id,
        reason_code="ROLE_RESOLVED",
        reason_detail=(
            "both participants have consistent explicit legal-role evidence "
            f"under {CONTRACT_ROLE_RULESET_V1}"
        ),
        findings=tuple(findings),
    )
