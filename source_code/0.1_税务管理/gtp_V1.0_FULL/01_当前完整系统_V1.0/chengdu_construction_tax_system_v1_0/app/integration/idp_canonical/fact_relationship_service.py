"""Task30 explicit Canonical FactRelationship completion service.

All reference resolution, direction checks, and source-event conflict checks run
before the first graph write. NEEDS_REVIEW therefore has zero graph/evidence
writes by construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ...v3_fact_models import Fact, FactRelationship
from ...v3_fact_relationship_evidence_models import FactRelationshipEvidence
from ...v3_party_models import SourceDocument
from .fact_relationship_resolver import (
    FactRelationshipResolutionError,
    FactRelationshipResolver,
    ResolvedFactTarget,
)
from .fact_relationship_rules import (
    TASK30_FACT_RELATIONSHIP_RULESET_V1,
    FactRelationshipRuleError,
    evidence_fingerprint,
    reference_descriptor,
    validate_relationship_direction,
)
from .fact_relationship_schemas import (
    FactRelationshipCompletionRequest,
    FactRelationshipCompletionResult,
    FactRelationshipReason,
    RelationshipCompletionOutcome,
)


class FactRelationshipServiceError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PreparedEvidence:
    resolved: ResolvedFactTarget
    fingerprint: str
    input: Any


class FactRelationshipService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.resolver = FactRelationshipResolver(session)

    def _advisory_lock(self, request: FactRelationshipCompletionRequest) -> None:
        bind = self.session.get_bind()
        if bind is None or bind.dialect.name != "postgresql":
            return
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {
                "lock_key": (
                    f"TASK30|{request.source_fact_id}|{request.relationship_type.value}|"
                    f"{request.source_document_id}|{request.source_extraction_id}"
                )
            },
        )

    def _source_fact(self, fact_id: int) -> Fact:
        fact = self.session.scalar(
            select(Fact).where(Fact.id == fact_id).with_for_update()
        )
        if fact is None:
            raise FactRelationshipServiceError("SOURCE_FACT_NOT_FOUND", f"Fact {fact_id} does not exist")
        if not fact.is_current or fact.validation_status == "SUPERSEDED":
            raise FactRelationshipServiceError("SOURCE_FACT_NOT_CURRENT", f"Fact {fact_id} is not current")
        if fact.fact_type not in {"INVOICE", "PAYMENT"}:
            raise FactRelationshipServiceError(
                "SOURCE_FACT_TYPE_UNSUPPORTED",
                f"Task30 source must be INVOICE or PAYMENT; got {fact.fact_type}",
            )
        return fact

    def _source_document(self, source_document_id: int) -> SourceDocument:
        document = self.session.scalar(
            select(SourceDocument)
            .where(SourceDocument.id == int(source_document_id))
            .with_for_update(read=True)
        )
        if document is None:
            raise FactRelationshipResolutionError(
                "SOURCE_DOCUMENT_NOT_FOUND",
                f"SourceDocument {source_document_id} does not exist",
            )
        if document.status != "VALIDATED":
            raise FactRelationshipResolutionError(
                "SOURCE_DOCUMENT_NOT_VALIDATED",
                f"SourceDocument {source_document_id} status must be VALIDATED, got {document.status}",
            )
        return document

    @staticmethod
    def _snapshot(fact: Fact) -> tuple[Any, ...]:
        return (
            fact.id,
            fact.fact_type,
            fact.business_identity_key,
            fact.version_no,
            fact.is_current,
            fact.supersedes_fact_id,
            fact.validation_status,
        )

    @staticmethod
    def _needs_review(
        *,
        source: Fact,
        request: FactRelationshipCompletionRequest,
        semantic_version: str,
        code: str,
        detail: str,
    ) -> FactRelationshipCompletionResult:
        return FactRelationshipCompletionResult(
            outcome=RelationshipCompletionOutcome.NEEDS_REVIEW,
            source_fact_id=source.id,
            source_fact_type=source.fact_type,
            relationship_type=request.relationship_type,
            ruleset_version=TASK30_FACT_RELATIONSHIP_RULESET_V1,
            semantic_version=semantic_version,
            source_business_identity_key=source.business_identity_key,
            source_version_no=source.version_no,
            source_validation_status=source.validation_status,
            reasons=[FactRelationshipReason(code=code, detail=detail)],
        )

    def _prepare(
        self,
        *,
        source: Fact,
        request: FactRelationshipCompletionRequest,
    ) -> tuple[list[PreparedEvidence], Fact, str] | FactRelationshipCompletionResult:
        prepared: list[PreparedEvidence] = []
        semantic_version = "UNRESOLVED"
        targets: dict[int, Fact] = {}

        for item in request.evidences:
            try:
                resolved = self.resolver.resolve(item)
                semantic_version = validate_relationship_direction(
                    source_fact_id=source.id,
                    source_fact_type=source.fact_type,
                    target_fact_id=resolved.fact.id,
                    target_fact_type=resolved.fact.fact_type,
                    relationship_type=request.relationship_type.value,
                )
            except (FactRelationshipResolutionError, FactRelationshipRuleError) as exc:
                return self._needs_review(
                    source=source,
                    request=request,
                    semantic_version=semantic_version,
                    code=exc.code,
                    detail=exc.detail,
                )
            targets[resolved.fact.id] = resolved.fact
            prepared.append(
                PreparedEvidence(
                    resolved=resolved,
                    fingerprint=evidence_fingerprint(
                        source_fact_id=source.id,
                        relationship_type=request.relationship_type.value,
                        source_document_id=request.source_document_id,
                        source_extraction_id=request.source_extraction_id,
                        evidence=item,
                    ),
                    input=item,
                )
            )

        if len(targets) != 1:
            return self._needs_review(
                source=source,
                request=request,
                semantic_version=semantic_version,
                code="CONFLICTING_TARGET_EVIDENCE",
                detail="explicit evidence items resolve to different target Facts",
            )
        deduplicated: dict[str, PreparedEvidence] = {}
        for item in prepared:
            deduplicated.setdefault(item.fingerprint, item)
        target = next(iter(targets.values()))
        return list(deduplicated.values()), target, semantic_version

    def _source_event_rows(
        self,
        *,
        source_fact_id: int,
        relationship_type: str,
        source_document_id: int,
        source_extraction_id: str,
    ) -> list[tuple[FactRelationshipEvidence, FactRelationship]]:
        rows = self.session.execute(
            select(FactRelationshipEvidence, FactRelationship)
            .join(FactRelationship, FactRelationship.id == FactRelationshipEvidence.relationship_id)
            .where(
                FactRelationship.source_fact_id == source_fact_id,
                FactRelationship.relationship_type == relationship_type,
                FactRelationshipEvidence.source_document_id == source_document_id,
                FactRelationshipEvidence.source_extraction_id == source_extraction_id,
            )
            .order_by(FactRelationshipEvidence.id)
            .with_for_update()
        ).all()
        return list(rows)

    def _complete(
        self,
        request: FactRelationshipCompletionRequest,
    ) -> FactRelationshipCompletionResult:
        self._advisory_lock(request)
        source = self._source_fact(request.source_fact_id)
        source_snapshot = self._snapshot(source)

        try:
            self._source_document(request.source_document_id)
        except FactRelationshipResolutionError as exc:
            return self._needs_review(
                source=source,
                request=request,
                semantic_version="UNRESOLVED",
                code=exc.code,
                detail=exc.detail,
            )

        prepared_result = self._prepare(source=source, request=request)
        if isinstance(prepared_result, FactRelationshipCompletionResult):
            return prepared_result
        prepared, target, semantic_version = prepared_result
        target_snapshot = self._snapshot(target)

        event_rows = self._source_event_rows(
            source_fact_id=source.id,
            relationship_type=request.relationship_type.value,
            source_document_id=request.source_document_id,
            source_extraction_id=request.source_extraction_id,
        )
        if event_rows:
            event_target_ids = {relationship.target_fact_id for _, relationship in event_rows}
            if event_target_ids != {target.id}:
                return self._needs_review(
                    source=source,
                    request=request,
                    semantic_version=semantic_version,
                    code="SOURCE_EVENT_TARGET_CONFLICT",
                    detail="the same source document/extraction is already audited against another target Fact",
                )

        relationship = self.session.scalar(
            select(FactRelationship)
            .where(
                FactRelationship.source_fact_id == source.id,
                FactRelationship.target_fact_id == target.id,
                FactRelationship.relationship_type == request.relationship_type.value,
            )
            .with_for_update()
        )

        existing_by_fp: dict[str, FactRelationshipEvidence] = {}
        if relationship is not None:
            existing_by_fp = {
                row.evidence_fingerprint: row
                for row in self.session.scalars(
                    select(FactRelationshipEvidence)
                    .where(FactRelationshipEvidence.relationship_id == relationship.id)
                    .with_for_update()
                ).all()
            }

        requested_fps = {item.fingerprint for item in prepared}
        if relationship is not None and requested_fps.issubset(existing_by_fp):
            if self._snapshot(source) != source_snapshot or self._snapshot(target) != target_snapshot:
                raise FactRelationshipServiceError("FACT_MUTATED", "Task30 changed immutable Fact state")
            return FactRelationshipCompletionResult(
                outcome=RelationshipCompletionOutcome.NOOP,
                source_fact_id=source.id,
                source_fact_type=source.fact_type,
                target_fact_id=target.id,
                target_fact_type=target.fact_type,
                relationship_id=relationship.id,
                relationship_type=request.relationship_type,
                evidence_ids=[existing_by_fp[fp].id for fp in sorted(requested_fps)],
                ruleset_version=TASK30_FACT_RELATIONSHIP_RULESET_V1,
                semantic_version=semantic_version,
                source_business_identity_key=source.business_identity_key,
                source_version_no=source.version_no,
                source_validation_status=source.validation_status,
            )

        # All fail-closed validation is complete. Graph writes start here.
        if relationship is None:
            relationship = FactRelationship(
                source_fact_id=source.id,
                target_fact_id=target.id,
                relationship_type=request.relationship_type.value,
                reason=(
                    "TASK30_EXPLICIT_CANONICAL_RELATIONSHIP;"
                    f"RULESET={TASK30_FACT_RELATIONSHIP_RULESET_V1};"
                    f"SEMANTIC={semantic_version}"
                ),
            )
            self.session.add(relationship)
            self.session.flush()

        evidence_rows: list[FactRelationshipEvidence] = []
        for item in prepared:
            existing = existing_by_fp.get(item.fingerprint)
            if existing is not None:
                evidence_rows.append(existing)
                continue

            reference_type, reference_value = reference_descriptor(item.input)
            collision = self.session.scalar(
                select(FactRelationshipEvidence)
                .where(
                    FactRelationshipEvidence.source_document_id == request.source_document_id,
                    FactRelationshipEvidence.source_extraction_id == request.source_extraction_id,
                    FactRelationshipEvidence.evidence_fingerprint == item.fingerprint,
                )
                .with_for_update()
            )
            if collision is not None and collision.relationship_id != relationship.id:
                raise FactRelationshipServiceError(
                    "EVIDENCE_FINGERPRINT_COLLISION",
                    "identical source evidence is already attached to another relationship",
                )

            row = FactRelationshipEvidence(
                relationship_id=relationship.id,
                source_document_id=request.source_document_id,
                source_extraction_id=request.source_extraction_id,
                evidence_type="EXPLICIT_REFERENCE",
                reference_type=reference_type,
                reference_value=reference_value,
                evidence_text=item.input.evidence_text,
                page_no=item.input.page_no,
                confidence=(Decimal(str(item.input.confidence)) if item.input.confidence is not None else None),
                ruleset_version=TASK30_FACT_RELATIONSHIP_RULESET_V1,
                evidence_fingerprint=item.fingerprint,
                submitted_by=request.submitted_by,
            )
            self.session.add(row)
            evidence_rows.append(row)

        self.session.flush()

        if self._snapshot(source) != source_snapshot or self._snapshot(target) != target_snapshot:
            raise FactRelationshipServiceError("FACT_MUTATED", "Task30 changed immutable Fact state")

        return FactRelationshipCompletionResult(
            outcome=RelationshipCompletionOutcome.CREATED,
            source_fact_id=source.id,
            source_fact_type=source.fact_type,
            target_fact_id=target.id,
            target_fact_type=target.fact_type,
            relationship_id=relationship.id,
            relationship_type=request.relationship_type,
            evidence_ids=[row.id for row in evidence_rows],
            ruleset_version=TASK30_FACT_RELATIONSHIP_RULESET_V1,
            semantic_version=semantic_version,
            source_business_identity_key=source.business_identity_key,
            source_version_no=source.version_no,
            source_validation_status=source.validation_status,
        )

    def complete(
        self,
        request: FactRelationshipCompletionRequest,
        *,
        commit: bool = True,
    ) -> FactRelationshipCompletionResult:
        nested = self.session.begin_nested()
        try:
            result = self._complete(request)
            nested.commit()
            if commit:
                self.session.commit()
            return result
        except Exception:
            if nested.is_active:
                nested.rollback()
            if commit:
                self.session.rollback()
            raise
