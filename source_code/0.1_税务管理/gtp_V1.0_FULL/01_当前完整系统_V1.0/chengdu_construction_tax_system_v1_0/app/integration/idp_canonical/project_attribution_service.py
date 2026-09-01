"""Task29 — Explicit Project Attribution & FactProjectAllocation completion.

This service is intentionally narrow:
- it resolves exact existing project_code values only;
- it writes Task17's FactProjectAllocation model only;
- it never changes Fact identity/version/validation state;
- it never creates a Project, Fact, relationship, or tax-analysis row;
- it never auto-supersedes pre-existing project allocations.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ...v3_contract_models import ContractFact
from ...v3_fact_models import Fact, InvoiceFact
from ...v3_party_models import SourceDocument
from ...v3_payment_models import PaymentFact
from ...v3_project_analysis_models import FactProjectAllocation
from .project_allocation_basis import (
    TASK29_PROJECT_ATTRIBUTION_RULESET_V1,
    ProjectAllocationBasis,
    ProjectAllocationPlan,
    ProjectAllocationValidationError,
    build_project_allocation_basis,
    build_project_allocation_plans,
)
from .project_attribution_resolver import ProjectResolutionError, resolve_projects_exact
from .project_attribution_schemas import (
    ProjectAttributionOutcome,
    ProjectAttributionReason,
    ProjectAttributionRequest,
    ProjectAttributionResult,
)


class ProjectAttributionServiceError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class ProjectAttributionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _advisory_lock(self, fact_id: int) -> None:
        bind = self.session.get_bind()
        if bind is None or bind.dialect.name != "postgresql":
            return
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"TASK29:FACT:{fact_id}"},
        )

    def _fact(self, fact_id: int) -> Fact:
        fact = self.session.scalar(select(Fact).where(Fact.id == fact_id).with_for_update())
        if fact is None:
            raise ProjectAttributionServiceError("FACT_NOT_FOUND", f"Fact {fact_id} does not exist")
        if not fact.is_current or fact.validation_status == "SUPERSEDED":
            raise ProjectAttributionServiceError("FACT_NOT_CURRENT", f"Fact {fact_id} is not current")
        if fact.fact_type not in {"INVOICE", "PAYMENT", "CONTRACT"}:
            raise ProjectAttributionServiceError(
                "UNSUPPORTED_FACT_TYPE",
                f"Task29 supports INVOICE/PAYMENT/CONTRACT; got {fact.fact_type}",
            )
        return fact

    def _specialized_fact(self, fact: Fact) -> object:
        model: type[Any]
        if fact.fact_type == "INVOICE":
            model = InvoiceFact
        elif fact.fact_type == "PAYMENT":
            model = PaymentFact
        else:
            model = ContractFact
        row = self.session.get(model, fact.id)
        if row is None:
            raise ProjectAttributionServiceError(
                "SPECIALIZED_FACT_MISSING",
                f"{fact.fact_type} Fact {fact.id} has no specialized domain row",
            )
        return row

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

    def _source_document(self, source_document_id: int | None) -> SourceDocument | None:
        if source_document_id is None:
            return None
        document = self.session.scalar(
            select(SourceDocument).where(SourceDocument.id == source_document_id).with_for_update(read=True)
        )
        if document is None:
            raise ProjectResolutionError(
                "SOURCE_DOCUMENT_NOT_FOUND",
                f"SourceDocument {source_document_id} does not exist",
            )
        if document.status != "VALIDATED":
            raise ProjectResolutionError(
                "SOURCE_DOCUMENT_NOT_VALIDATED",
                f"SourceDocument {source_document_id} status must be VALIDATED, got {document.status}",
            )
        return document

    @staticmethod
    def _reason_result(
        *,
        fact: Fact,
        basis_version: str | None,
        project_codes: list[str],
        code: str,
        detail: str,
    ) -> ProjectAttributionResult:
        return ProjectAttributionResult(
            outcome=ProjectAttributionOutcome.NEEDS_REVIEW,
            fact_id=fact.id,
            fact_type=fact.fact_type,
            business_identity_key=fact.business_identity_key,
            version_no=fact.version_no,
            validation_status=fact.validation_status,
            ruleset_version=TASK29_PROJECT_ATTRIBUTION_RULESET_V1,
            basis_version=basis_version,
            project_codes=project_codes,
            reasons=[ProjectAttributionReason(code=code, detail=detail)],
        )

    @staticmethod
    def _amount_equal(left: Decimal, right: Decimal) -> bool:
        return Decimal(left).quantize(Decimal("0.01")) == Decimal(right).quantize(Decimal("0.01"))

    def _existing_allocations(self, fact_id: int) -> list[FactProjectAllocation]:
        return list(
            self.session.scalars(
                select(FactProjectAllocation)
                .where(FactProjectAllocation.fact_id == fact_id)
                .order_by(FactProjectAllocation.id)
                .with_for_update()
            ).all()
        )

    def _matches_managed_retry(
        self,
        *,
        existing: list[FactProjectAllocation],
        plans: list[ProjectAllocationPlan],
        projects_by_code: dict[str, Any],
        source_document_id: int | None,
    ) -> bool:
        if len(existing) != len(plans):
            return False
        expected_method = "SOURCE_DOCUMENT" if source_document_id is not None else "EXPLICIT"
        expected_source = "DOCUMENT" if source_document_id is not None else "HUMAN"
        by_project_id = {row.project_id: row for row in existing}
        if len(by_project_id) != len(existing):
            return False
        for plan in plans:
            project = projects_by_code[plan.project_code]
            row = by_project_id.get(project.id)
            if row is None:
                return False
            if not row.is_current:
                return False
            if row.allocation_version != 1 or row.supersedes_allocation_id is not None:
                return False
            if row.status != "CONFIRMED" or row.confidence != "HIGH":
                return False
            if row.rule_version != TASK29_PROJECT_ATTRIBUTION_RULESET_V1:
                return False
            if row.allocation_method != expected_method or row.proposal_source != expected_source:
                return False
            if row.source_document_id != source_document_id:
                return False
            if not self._amount_equal(row.allocated_net, plan.net):
                return False
            if not self._amount_equal(row.allocated_vat, plan.vat):
                return False
            if not self._amount_equal(row.allocated_gross, plan.gross):
                return False
        return True

    @staticmethod
    def _audit_note(
        *,
        basis: ProjectAllocationBasis,
        request_note: str | None,
        item_note: str | None,
    ) -> str:
        parts = [
            "TASK29_EXPLICIT_PROJECT_ATTRIBUTION",
            f"RULESET={TASK29_PROJECT_ATTRIBUTION_RULESET_V1}",
            f"BASIS={basis.basis_version}",
            basis.audit_note,
        ]
        if request_note:
            parts.append(f"REQUEST_NOTE={request_note}")
        if item_note:
            parts.append(f"ITEM_NOTE={item_note}")
        return ";".join(parts)

    def _complete(self, request: ProjectAttributionRequest) -> ProjectAttributionResult:
        self._advisory_lock(request.fact_id)
        fact = self._fact(request.fact_id)
        original = self._snapshot(fact)
        specialized = self._specialized_fact(fact)
        raw_codes = [item.project_code for item in request.allocations]

        try:
            basis = build_project_allocation_basis(fact_type=fact.fact_type, specialized_fact=specialized)
            plans = build_project_allocation_plans(basis=basis, allocations=request.allocations)
        except ProjectAllocationValidationError as exc:
            return self._reason_result(
                fact=fact,
                basis_version=None,
                project_codes=raw_codes,
                code=exc.code,
                detail=exc.detail,
            )

        project_codes = [plan.project_code for plan in plans]
        try:
            projects_by_code = resolve_projects_exact(self.session, project_codes)
            source_document = self._source_document(request.source_document_id)
        except (ProjectResolutionError, ProjectAllocationValidationError) as exc:
            return self._reason_result(
                fact=fact,
                basis_version=basis.basis_version,
                project_codes=project_codes,
                code=exc.code,
                detail=exc.detail,
            )

        existing = self._existing_allocations(fact.id)
        if existing:
            if self._matches_managed_retry(
                existing=existing,
                plans=plans,
                projects_by_code=projects_by_code,
                source_document_id=source_document.id if source_document is not None else None,
            ):
                if self._snapshot(fact) != original:
                    raise ProjectAttributionServiceError("FACT_MUTATED", "Task29 changed immutable Fact state")
                return ProjectAttributionResult(
                    outcome=ProjectAttributionOutcome.NOOP,
                    fact_id=fact.id,
                    fact_type=fact.fact_type,
                    business_identity_key=fact.business_identity_key,
                    version_no=fact.version_no,
                    validation_status=fact.validation_status,
                    ruleset_version=TASK29_PROJECT_ATTRIBUTION_RULESET_V1,
                    basis_version=basis.basis_version,
                    allocation_ids=[row.id for row in existing],
                    project_codes=project_codes,
                )
            current = [row for row in existing if row.is_current]
            code = "EXISTING_PROJECT_ALLOCATION_CONFLICT" if current else "PROJECT_ALLOCATION_HISTORY_PRESENT"
            detail = (
                "Fact already has project-allocation audit history that does not exactly match this Task29 request; "
                "automatic mutation/supersession is forbidden"
            )
            return self._reason_result(
                fact=fact,
                basis_version=basis.basis_version,
                project_codes=project_codes,
                code=code,
                detail=detail,
            )

        now = datetime.now(timezone.utc)
        allocation_method = "SOURCE_DOCUMENT" if source_document is not None else "EXPLICIT"
        proposal_source = "DOCUMENT" if source_document is not None else "HUMAN"
        rows: list[FactProjectAllocation] = []
        for plan in plans:
            project = projects_by_code[plan.project_code]
            row = FactProjectAllocation(
                fact_id=fact.id,
                project_id=project.id,
                allocation_version=1,
                is_current=True,
                supersedes_allocation_id=None,
                allocated_net=plan.net,
                allocated_vat=plan.vat,
                allocated_gross=plan.gross,
                allocation_method=allocation_method,
                confidence="HIGH",
                status="CONFIRMED",
                proposal_source=proposal_source,
                source_document_id=source_document.id if source_document is not None else None,
                rule_version=TASK29_PROJECT_ATTRIBUTION_RULESET_V1,
                reviewed_by=request.reviewed_by,
                reviewed_at=now,
                note=self._audit_note(basis=basis, request_note=request.note, item_note=plan.note),
            )
            self.session.add(row)
            rows.append(row)
        self.session.flush()

        if self._snapshot(fact) != original:
            raise ProjectAttributionServiceError("FACT_MUTATED", "Task29 changed immutable Fact state")

        return ProjectAttributionResult(
            outcome=ProjectAttributionOutcome.CREATED,
            fact_id=fact.id,
            fact_type=fact.fact_type,
            business_identity_key=fact.business_identity_key,
            version_no=fact.version_no,
            validation_status=fact.validation_status,
            ruleset_version=TASK29_PROJECT_ATTRIBUTION_RULESET_V1,
            basis_version=basis.basis_version,
            allocation_ids=[row.id for row in rows],
            project_codes=project_codes,
        )

    def complete(self, request: ProjectAttributionRequest, *, commit: bool = True) -> ProjectAttributionResult:
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
