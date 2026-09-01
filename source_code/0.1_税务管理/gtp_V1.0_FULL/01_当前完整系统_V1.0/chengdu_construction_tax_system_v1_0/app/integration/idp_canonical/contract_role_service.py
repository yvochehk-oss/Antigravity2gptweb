"""Task26 Contract Role & Semantic Completion service.

Task26 enriches the existing Task24 Contract Fact only. It never creates a
Fact, changes its business identity/version, performs Fact supersession,
guesses roles from PARTY_A/PARTY_B position, infers contract_category, promotes
a Contract to VALID, or writes Production Seal/cutover state.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.v3_contract_models import ContractFact
from app.v3_contract_role_models import ContractRoleEvidence, ContractRoleResolution
from app.v3_fact_models import Fact
from app.v3_integration_models import CanonicalIngestReceipt

from .contract_role_resolver import (
    CONTRACT_ROLE_RULESET_V1,
    ContractRoleDecision,
    ContractRoleResolutionError,
    classify_explicit_legal_role,
    contract_role_evidence_fingerprint,
    evidence_set_fingerprint,
    resolve_contract_roles,
)
from .contract_role_schemas import (
    ContractRoleCompletionRequest,
    ContractRoleCompletionResult,
    ContractRoleEvidenceInput,
    ContractRoleFinding,
)
from .normalizer import CONTRACT_IDENTITY_VERSION


class ContractRoleServiceError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PreviousResolutionState:
    resolution_status: str
    buyer_party_id: int | None
    seller_party_id: int | None


def apply_contract_role_projection(
    *,
    fact: Any,
    contract: Any,
    decision: ContractRoleDecision,
    previous_resolution: PreviousResolutionState | None,
) -> bool:
    """Project a role decision onto the existing ContractFact safely."""

    if not bool(getattr(fact, "is_current", False)):
        raise ContractRoleServiceError(
            "FACT_NOT_CURRENT",
            "Task26 requires a current Contract Fact",
        )

    if (
        int(getattr(fact, "version_no", 0)) != 1
        or getattr(fact, "supersedes_fact_id", None) is not None
    ):
        raise ContractRoleServiceError(
            "CONTRACT_SUPERSESSION_UNSUPPORTED",
            "Task26 v1 only operates on the original Task24 Contract Fact",
        )

    current_status = str(getattr(fact, "validation_status", ""))

    if current_status not in {"DRAFT", "NEEDS_REVIEW"}:
        raise ContractRoleServiceError(
            "FACT_STATUS_NOT_TASK26_MANAGED",
            "Task26 does not modify VALID/INVALID/SUPERSEDED Contract Facts",
        )

    existing_buyer = getattr(contract, "buyer_party_id", None)
    existing_seller = getattr(contract, "seller_party_id", None)
    changed = False

    if decision.status == "RESOLVED":
        if (
            decision.buyer_party_id is None
            or decision.seller_party_id is None
            or decision.buyer_party_id == decision.seller_party_id
        ):
            raise ContractRoleServiceError(
                "ROLE_DECISION_INVALID",
                "RESOLVED decision has invalid buyer/seller ids",
            )

        if existing_buyer is None and existing_seller is None:
            contract.buyer_party_id = int(decision.buyer_party_id)
            contract.seller_party_id = int(decision.seller_party_id)
            changed = True
        elif (
            existing_buyer == decision.buyer_party_id
            and existing_seller == decision.seller_party_id
        ):
            pass
        else:
            raise ContractRoleServiceError(
                "EXISTING_CONTRACT_ROLE_CONFLICT",
                (
                    "ContractFact already has buyer/seller values different "
                    "from explicit Task26 evidence"
                ),
            )

    elif decision.status == "NEEDS_REVIEW":
        if existing_buyer is not None or existing_seller is not None:
            previous_matches = (
                previous_resolution is not None
                and previous_resolution.resolution_status == "RESOLVED"
                and previous_resolution.buyer_party_id == existing_buyer
                and previous_resolution.seller_party_id == existing_seller
            )

            if not previous_matches:
                raise ContractRoleServiceError(
                    "EXISTING_CONTRACT_ROLE_CONFLICT",
                    (
                        "Task26 cannot clear buyer/seller values that were not "
                        "established by its previous resolution"
                    ),
                )

            contract.buyer_party_id = None
            contract.seller_party_id = None
            changed = True
    else:
        raise ContractRoleServiceError(
            "ROLE_DECISION_INVALID",
            f"unsupported role decision {decision.status!r}",
        )

    if current_status == "DRAFT":
        fact.validation_status = "NEEDS_REVIEW"
        changed = True

    return changed


class ContractRoleService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _lock_fact(self, fact_id: int) -> None:
        self.db.execute(
            text(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(:lock_key, 0)
                )
                """
            ),
            {"lock_key": f"TASK26_CONTRACT_ROLE|{int(fact_id)}"},
        )

    def _receipt(
        self,
        request: ContractRoleCompletionRequest,
    ) -> CanonicalIngestReceipt:
        receipt = self.db.execute(
            select(CanonicalIngestReceipt)
            .where(
                CanonicalIngestReceipt.source_system == request.source_system,
                CanonicalIngestReceipt.source_extraction_id
                == request.source_extraction_id,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if receipt is None:
            raise ContractRoleServiceError(
                "TASK24_RECEIPT_NOT_FOUND",
                "Task24 receipt not found",
            )

        if receipt.outcome not in {"CREATED", "NOOP"}:
            raise ContractRoleServiceError(
                "TASK24_RECEIPT_NOT_ELIGIBLE",
                f"Task24 receipt outcome {receipt.outcome!r} is not eligible",
            )

        if receipt.document_type != "contract":
            raise ContractRoleServiceError(
                "UNSUPPORTED_DOCUMENT_TYPE",
                "Task26 accepts Contract receipts only",
            )

        if receipt.source_document_id != request.source_document_id:
            raise ContractRoleServiceError(
                "SOURCE_DOCUMENT_ID_CONFLICT",
                "source_document_id differs from Task24 receipt",
            )

        if receipt.document_sha256.lower() != request.document_sha256.lower():
            raise ContractRoleServiceError(
                "SOURCE_DOCUMENT_SHA_CONFLICT",
                "document SHA256 differs from Task24 receipt",
            )

        if str(receipt.identity_version) != CONTRACT_IDENTITY_VERSION:
            raise ContractRoleServiceError(
                "CONTRACT_IDENTITY_VERSION_CONFLICT",
                (
                    "Task26 requires Task24 role-neutral "
                    f"{CONTRACT_IDENTITY_VERSION} identity"
                ),
            )

        return receipt

    def _fact_and_contract(
        self,
        receipt: CanonicalIngestReceipt,
    ) -> tuple[Fact, ContractFact]:
        fact = self.db.execute(
            select(Fact)
            .where(Fact.id == int(receipt.fact_id))
            .with_for_update()
        ).scalar_one_or_none()

        contract = self.db.execute(
            select(ContractFact)
            .where(ContractFact.fact_id == int(receipt.fact_id))
            .with_for_update()
        ).scalar_one_or_none()

        if fact is None or contract is None:
            raise ContractRoleServiceError(
                "CANONICAL_CONTRACT_NOT_FOUND",
                "Task24 receipt does not reference an existing Contract Fact",
            )

        if fact.fact_type != "CONTRACT":
            raise ContractRoleServiceError(
                "FACT_TYPE_MISMATCH",
                f"Fact {fact.id} is not CONTRACT",
            )

        if str(fact.business_identity_key) != str(receipt.business_identity_key):
            raise ContractRoleServiceError(
                "TASK24_BUSINESS_IDENTITY_DRIFT",
                "Fact business identity differs from Task24 receipt",
            )

        if not fact.is_current or fact.validation_status == "SUPERSEDED":
            raise ContractRoleServiceError(
                "FACT_NOT_CURRENT",
                "Task26 requires a current Contract Fact",
            )

        if int(fact.version_no) != 1 or fact.supersedes_fact_id is not None:
            raise ContractRoleServiceError(
                "CONTRACT_SUPERSESSION_UNSUPPORTED",
                "Task26 never operates on or creates Contract Fact supersession",
            )

        if fact.validation_status not in {"DRAFT", "NEEDS_REVIEW"}:
            raise ContractRoleServiceError(
                "FACT_STATUS_NOT_TASK26_MANAGED",
                "Task26 does not modify VALID/INVALID Contract Facts",
            )

        return fact, contract

    def _participants(
        self,
        receipt: CanonicalIngestReceipt,
    ) -> dict[str, int]:
        payload = receipt.canonical_payload

        if not isinstance(payload, dict):
            raise ContractRoleServiceError(
                "TASK24_CONTRACT_PAYLOAD_INVALID",
                "Task24 canonical_payload is not an object",
            )

        if (
            payload.get("buyer_party_id") is not None
            or payload.get("seller_party_id") is not None
        ):
            raise ContractRoleServiceError(
                "TASK24_CONTRACT_ROLE_BASELINE_INVALID",
                "Task24 Contract receipt is not role-neutral",
            )

        raw_participants = payload.get("participants")

        if not isinstance(raw_participants, list):
            raise ContractRoleServiceError(
                "TASK24_PARTICIPANTS_MISSING",
                "Task24 Contract receipt contains no participants array",
            )

        participants: dict[str, int] = {}

        for item in raw_participants:
            if not isinstance(item, dict):
                raise ContractRoleServiceError(
                    "TASK24_PARTICIPANTS_INVALID",
                    "participant entry is not an object",
                )

            source_role = str(item.get("source_role", "")).strip().upper()

            if source_role not in {"PARTY_A", "PARTY_B"}:
                raise ContractRoleServiceError(
                    "TASK24_PARTICIPANTS_INVALID",
                    "participant source_role must be party_a or party_b",
                )

            try:
                party_id = int(item["party_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ContractRoleServiceError(
                    "TASK24_PARTICIPANTS_INVALID",
                    "participant has no valid Canonical Party id",
                ) from exc

            if party_id < 1:
                raise ContractRoleServiceError(
                    "TASK24_PARTICIPANTS_INVALID",
                    "participant Party id must be positive",
                )

            if source_role in participants:
                raise ContractRoleServiceError(
                    "TASK24_PARTICIPANTS_INVALID",
                    f"duplicate Task24 participant {source_role}",
                )

            participants[source_role] = party_id

        if set(participants) != {"PARTY_A", "PARTY_B"}:
            raise ContractRoleServiceError(
                "TASK24_PARTICIPANTS_INCOMPLETE",
                "Task26 requires exactly PARTY_A and PARTY_B",
            )

        if participants["PARTY_A"] == participants["PARTY_B"]:
            raise ContractRoleServiceError(
                "TASK24_PARTICIPANTS_CONFLICT",
                "PARTY_A and PARTY_B resolve to the same Canonical Party",
            )

        return participants

    def _existing_evidence(
        self,
        *,
        fact_id: int,
        fingerprint: str,
    ) -> ContractRoleEvidence | None:
        return self.db.execute(
            select(ContractRoleEvidence).where(
                ContractRoleEvidence.fact_id == int(fact_id),
                ContractRoleEvidence.evidence_fingerprint == fingerprint,
            )
        ).scalar_one_or_none()

    def _persist_evidence(
        self,
        *,
        request: ContractRoleCompletionRequest,
        receipt: CanonicalIngestReceipt,
        fact_id: int,
        participants: dict[str, int],
        evidence: ContractRoleEvidenceInput,
    ) -> tuple[ContractRoleEvidence, bool]:
        party_id = participants[evidence.source_party_role]

        classification = classify_explicit_legal_role(
            legal_role_label=evidence.legal_role_label,
            evidence_text=evidence.evidence_text,
        )

        fingerprint = contract_role_evidence_fingerprint(
            fact_id=int(fact_id),
            receipt_id=int(receipt.id),
            source_system=request.source_system,
            source_document_id=request.source_document_id,
            source_extraction_id=request.source_extraction_id,
            source_party_role=evidence.source_party_role,
            party_id=int(party_id),
            evidence=evidence,
        )

        existing = self._existing_evidence(
            fact_id=int(fact_id),
            fingerprint=fingerprint,
        )

        if existing is not None:
            return existing, False

        confidence = (
            Decimal(str(evidence.confidence)).quantize(Decimal("0.00001"))
            if evidence.confidence is not None
            else None
        )

        row = ContractRoleEvidence(
            fact_id=int(fact_id),
            receipt_id=int(receipt.id),
            source_system=request.source_system,
            source_document_id=request.source_document_id,
            source_extraction_id=request.source_extraction_id,
            document_sha256=request.document_sha256,
            source_party_role=evidence.source_party_role,
            party_id=int(party_id),
            evidence_type=evidence.evidence_type,
            legal_role_label=evidence.legal_role_label,
            evidence_text=evidence.evidence_text,
            page_no=evidence.page_no,
            confidence=confidence,
            canonical_role=classification.canonical_role,
            classification_status=classification.status,
            classification_code=classification.code,
            ruleset_version=CONTRACT_ROLE_RULESET_V1,
            evidence_fingerprint=fingerprint,
            submitted_by=request.submitted_by,
        )

        self.db.add(row)
        self.db.flush()
        return row, True

    def _all_evidence(self, fact_id: int) -> list[ContractRoleEvidence]:
        return list(
            self.db.execute(
                select(ContractRoleEvidence)
                .where(
                    ContractRoleEvidence.fact_id == int(fact_id),
                    ContractRoleEvidence.ruleset_version
                    == CONTRACT_ROLE_RULESET_V1,
                )
                .order_by(ContractRoleEvidence.id)
            ).scalars().all()
        )

    def _current_resolution(
        self,
        fact_id: int,
    ) -> ContractRoleResolution | None:
        return self.db.execute(
            select(ContractRoleResolution)
            .where(
                ContractRoleResolution.fact_id == int(fact_id),
                ContractRoleResolution.is_current.is_(True),
            )
            .with_for_update()
        ).scalar_one_or_none()

    @staticmethod
    def _previous_state(
        resolution: ContractRoleResolution | None,
    ) -> PreviousResolutionState | None:
        if resolution is None:
            return None

        return PreviousResolutionState(
            resolution_status=resolution.resolution_status,
            buyer_party_id=resolution.buyer_party_id,
            seller_party_id=resolution.seller_party_id,
        )

    @staticmethod
    def _same_resolution(
        current: ContractRoleResolution,
        *,
        decision: ContractRoleDecision,
        evidence_fp: str,
    ) -> bool:
        return (
            current.ruleset_version == CONTRACT_ROLE_RULESET_V1
            and current.resolution_status == decision.status
            and current.buyer_party_id == decision.buyer_party_id
            and current.seller_party_id == decision.seller_party_id
            and current.evidence_set_fingerprint == evidence_fp
            and current.reason_code == decision.reason_code
            and current.reason_detail == decision.reason_detail
        )

    def _persist_resolution(
        self,
        *,
        fact_id: int,
        receipt_id: int,
        current: ContractRoleResolution | None,
        decision: ContractRoleDecision,
        evidence_fp: str,
    ) -> tuple[ContractRoleResolution, str]:
        if (
            current is not None
            and self._same_resolution(
                current,
                decision=decision,
                evidence_fp=evidence_fp,
            )
        ):
            return current, "NOOP"

        next_seq = int(current.resolution_seq) + 1 if current is not None else 1
        supersedes_id = int(current.id) if current is not None else None

        if current is not None:
            current.is_current = False
            self.db.flush()

        row = ContractRoleResolution(
            fact_id=int(fact_id),
            receipt_id=int(receipt_id),
            resolution_seq=next_seq,
            is_current=True,
            supersedes_resolution_id=supersedes_id,
            ruleset_version=CONTRACT_ROLE_RULESET_V1,
            resolution_status=decision.status,
            buyer_party_id=decision.buyer_party_id,
            seller_party_id=decision.seller_party_id,
            evidence_set_fingerprint=evidence_fp,
            reason_code=decision.reason_code,
            reason_detail=decision.reason_detail,
        )

        self.db.add(row)
        self.db.flush()

        return row, ("SUPERSEDED" if current is not None else "CREATED")

    def _complete(
        self,
        request: ContractRoleCompletionRequest,
    ) -> ContractRoleCompletionResult:
        receipt = self._receipt(request)
        self._lock_fact(int(receipt.fact_id))
        fact, contract = self._fact_and_contract(receipt)

        original_fact_id = int(fact.id)
        original_identity = str(fact.business_identity_key)
        original_version = int(fact.version_no)
        original_category = contract.contract_category

        participants = self._participants(receipt)
        current_resolution = self._current_resolution(int(fact.id))
        previous_state = self._previous_state(current_resolution)

        submitted_rows: list[ContractRoleEvidence] = []
        evidence_created = False

        for evidence in request.evidences:
            row, created = self._persist_evidence(
                request=request,
                receipt=receipt,
                fact_id=int(fact.id),
                participants=participants,
                evidence=evidence,
            )
            submitted_rows.append(row)
            evidence_created = evidence_created or created

        all_evidence = self._all_evidence(int(fact.id))

        try:
            decision = resolve_contract_roles(all_evidence)
        except ContractRoleResolutionError as exc:
            raise ContractRoleServiceError(exc.code, exc.detail) from exc

        evidence_fp = evidence_set_fingerprint(all_evidence)

        projection_changed = apply_contract_role_projection(
            fact=fact,
            contract=contract,
            decision=decision,
            previous_resolution=previous_state,
        )

        resolution, resolution_outcome = self._persist_resolution(
            fact_id=int(fact.id),
            receipt_id=int(receipt.id),
            current=current_resolution,
            decision=decision,
            evidence_fp=evidence_fp,
        )

        self.db.flush()

        if int(fact.id) != original_fact_id:
            raise ContractRoleServiceError(
                "TASK24_FACT_ID_MUTATED",
                "Task26 changed Fact identity",
            )

        if str(fact.business_identity_key) != original_identity:
            raise ContractRoleServiceError(
                "TASK24_BUSINESS_IDENTITY_MUTATED",
                "Task26 changed Task24 business_identity_key",
            )

        if int(fact.version_no) != original_version:
            raise ContractRoleServiceError(
                "TASK24_FACT_VERSION_MUTATED",
                "Task26 changed Fact.version_no",
            )

        if fact.supersedes_fact_id is not None:
            raise ContractRoleServiceError(
                "TASK24_FACT_SUPERSESSION_MUTATED",
                "Task26 introduced Fact supersession",
            )

        if contract.contract_category != original_category:
            raise ContractRoleServiceError(
                "CONTRACT_CATEGORY_MUTATED",
                "Task26 changed contract_category",
            )

        changed = (
            evidence_created
            or projection_changed
            or resolution_outcome != "NOOP"
        )

        return ContractRoleCompletionResult(
            outcome="UPDATED" if changed else "NOOP",
            receipt_id=int(receipt.id),
            fact_id=int(fact.id),
            business_identity_key=str(fact.business_identity_key),
            version_no=int(fact.version_no),
            validation_status=str(fact.validation_status),
            resolution_id=int(resolution.id),
            resolution_seq=int(resolution.resolution_seq),
            resolution_status=resolution.resolution_status,
            buyer_party_id=resolution.buyer_party_id,
            seller_party_id=resolution.seller_party_id,
            ruleset_version=resolution.ruleset_version,
            evidence_outcome="CREATED" if evidence_created else "NOOP",
            resolution_outcome=resolution_outcome,
            evidence_ids=[int(row.id) for row in submitted_rows],
            evidence_set_fingerprint=resolution.evidence_set_fingerprint,
            reason_code=resolution.reason_code,
            reason_detail=resolution.reason_detail,
            findings=[
                ContractRoleFinding(code=code, message=message)
                for code, message in decision.findings
            ],
        )

    def complete(
        self,
        request: ContractRoleCompletionRequest,
        *,
        commit: bool = True,
    ) -> ContractRoleCompletionResult:
        try:
            with self.db.begin_nested():
                result = self._complete(request)

            if commit:
                self.db.commit()

            return result
        except ContractRoleServiceError:
            if commit:
                self.db.rollback()
            raise
        except Exception:
            if commit:
                self.db.rollback()
            raise
