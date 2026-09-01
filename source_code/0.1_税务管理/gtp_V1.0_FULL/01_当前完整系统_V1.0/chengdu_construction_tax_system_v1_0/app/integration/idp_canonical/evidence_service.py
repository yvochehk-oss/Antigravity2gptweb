"""Task25 IDP Source Evidence Bridge & Canonical Evidence Completion service.

Task25 responsibilities:

* resolve a successful Task24 receipt;
* register/reuse exactly one Canonical SourceDocument;
* attach a real integer SourceDocument FK to FactProvenance;
* create InvoiceLine evidence once;
* reject conflicting later line evidence;
* optionally establish the Invoice document status;
* build a real database evidence snapshot;
* call the existing Task09 ``evaluate_invoice_evidence`` evaluator;
* apply Task09's decision to DRAFT/NEEDS_REVIEW Facts.

Task25 does NOT:

* create a new Fact;
* alter invoice identity;
* supersede an Invoice;
* invent tax-rate rules;
* bypass Task09;
* touch legacy writers;
* alter cutover state or Production Seal.
"""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
import json
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.invoice.validation import (
    InvoiceEvidenceSnapshot,
    ValidationDecision,
    evaluate_invoice_evidence,
)
from app.v3_evidence_integration_models import (
    IDPSourceDocumentBinding,
)
from app.v3_fact_models import (
    Fact,
    FactProvenance,
    FactRelationship,
    InvoiceFact,
    InvoiceLine,
)
from app.v3_integration_models import CanonicalIngestReceipt
from app.v3_party_models import (
    PartyIdentifier,
    SourceDocument,
)

from .evidence_schemas import (
    InvoiceEvidenceCompletionRequest,
    InvoiceEvidenceCompletionResult,
)
from .invoice_line_normalizer import (
    InvoiceLineEvidenceError,
    plan_invoice_line_write,
)
from .source_document_bridge import (
    SourceDocumentBindingError,
    SourceDocumentBridge,
)


class EvidenceCompletionError(ValueError):
    def __init__(
        self,
        code: str,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def apply_task09_decision(
    fact: Any,
    decision: ValidationDecision,
) -> bool:
    """Apply Task09 decision without inventing a second validator.

    Returns True when ``fact.validation_status`` changed.

    An INVALID Fact is never rehabilitated by Task25.
    A VALID Fact is never downgraded merely because a later retry omits
    evidence, but deterministic contradictory evidence is rejected.
    """

    if not fact.is_current or fact.validation_status == "SUPERSEDED":
        raise EvidenceCompletionError(
            "FACT_NOT_EVIDENCE_PROMOTABLE",
            f"Fact {fact.id} is not current/promotable",
        )

    current = str(fact.validation_status)
    desired = str(decision.desired_status)

    if current == "INVALID":
        return False

    if current == "VALID":
        if desired == "INVALID":
            raise EvidenceCompletionError(
                "VALID_FACT_EVIDENCE_REGRESSION",
                "new evidence would make an already VALID Fact INVALID; "
                "a reviewed correction workflow is required",
            )
        return False

    if current not in {
        "DRAFT",
        "NEEDS_REVIEW",
    }:
        raise EvidenceCompletionError(
            "FACT_STATUS_UNSUPPORTED",
            f"Task25 cannot update Fact status {current!r}",
        )

    if current != desired:
        fact.validation_status = desired
        return True

    return False


class InvoiceEvidenceCompletionService:
    def __init__(
        self,
        db: Session,
        *,
        source_bridge: SourceDocumentBridge | None = None,
    ) -> None:
        self.db = db
        self.source_bridge = (
            source_bridge
            or SourceDocumentBridge(db)
        )

    def _lock_fact(
        self,
        fact_id: int,
    ) -> None:
        self.db.execute(
            text(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(:lock_key, 0)
                )
                """
            ),
            {
                "lock_key": (
                    "TASK25_FACT_EVIDENCE|"
                    f"{int(fact_id)}"
                )
            },
        )

    def _receipt(
        self,
        request: InvoiceEvidenceCompletionRequest,
    ) -> CanonicalIngestReceipt:
        receipt = self.db.execute(
            select(CanonicalIngestReceipt)
            .where(
                CanonicalIngestReceipt.source_system
                == request.source_system,
                CanonicalIngestReceipt.source_extraction_id
                == request.source_extraction_id,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if receipt is None:
            raise EvidenceCompletionError(
                "TASK24_RECEIPT_NOT_FOUND",
                "no Task24 receipt exists for the source extraction",
            )

        if receipt.outcome not in {
            "CREATED",
            "NOOP",
        }:
            raise EvidenceCompletionError(
                "TASK24_RECEIPT_NOT_ELIGIBLE",
                f"Task24 receipt outcome {receipt.outcome!r} "
                "is not eligible for evidence completion",
            )

        if receipt.document_type != "invoice":
            raise EvidenceCompletionError(
                "UNSUPPORTED_DOCUMENT_TYPE",
                "Task25 currently completes Invoice evidence only",
            )

        if receipt.source_document_id != request.source_document_id:
            raise EvidenceCompletionError(
                "SOURCE_DOCUMENT_ID_CONFLICT",
                "Task25 source_document_id differs from Task24 receipt",
            )

        if (
            receipt.document_sha256.lower()
            != request.document_sha256.lower()
        ):
            raise EvidenceCompletionError(
                "SOURCE_DOCUMENT_SHA_CONFLICT",
                "Task25 SHA256 differs from Task24 receipt",
            )

        return receipt

    def _fact_and_invoice(
        self,
        receipt: CanonicalIngestReceipt,
    ) -> tuple[Fact, InvoiceFact]:
        fact = self.db.execute(
            select(Fact)
            .where(
                Fact.id == int(receipt.fact_id)
            )
            .with_for_update()
        ).scalar_one_or_none()

        invoice = self.db.execute(
            select(InvoiceFact)
            .where(
                InvoiceFact.fact_id
                == int(receipt.fact_id)
            )
            .with_for_update()
        ).scalar_one_or_none()

        if fact is None or invoice is None:
            raise EvidenceCompletionError(
                "CANONICAL_INVOICE_NOT_FOUND",
                f"Task24 receipt {receipt.id} references no Invoice Fact",
            )

        if fact.fact_type != "INVOICE":
            raise EvidenceCompletionError(
                "FACT_TYPE_MISMATCH",
                f"Fact {fact.id} is not INVOICE",
            )

        if (
            not fact.is_current
            or fact.validation_status == "SUPERSEDED"
        ):
            raise EvidenceCompletionError(
                "FACT_NOT_EVIDENCE_PROMOTABLE",
                f"Fact {fact.id} is not current/promotable",
            )

        return fact, invoice

    @staticmethod
    def _chunk_id(
        source_extraction_id: str,
    ) -> str:
        return (
            f"IDP:{source_extraction_id}"
        )[:120]

    def _complete_provenance(
        self,
        *,
        request: InvoiceEvidenceCompletionRequest,
        receipt: CanonicalIngestReceipt,
        fact_id: int,
        source_document_pk: int,
    ) -> str:
        chunk_id = self._chunk_id(
            request.source_extraction_id
        )

        rows = self.db.execute(
            select(FactProvenance)
            .where(
                FactProvenance.fact_id
                == int(fact_id),
                FactProvenance.chunk_id
                == chunk_id,
            )
            .order_by(FactProvenance.id)
            .with_for_update()
        ).scalars().all()

        if len(rows) > 1:
            raise EvidenceCompletionError(
                "PROVENANCE_AMBIGUOUS",
                "multiple FactProvenance rows exist for the same "
                "Task25 extraction key",
            )

        confidence = (
            Decimal(str(request.confidence)).quantize(
                Decimal("0.00001")
            )
            if request.confidence is not None
            else None
        )

        verification_reason = (
            (
                "TASK25_SOURCE_DOCUMENT_VALIDATED;"
                f"{request.document.validation_reason}"
            )
            if request.document.validation_status
            == "VALIDATED"
            else "TASK25_SOURCE_DOCUMENT_BOUND"
        )

        if not rows:
            provenance = FactProvenance(
                fact_id=int(fact_id),
                document_id=int(source_document_pk),
                chunk_id=chunk_id,
                page_start=request.page_start,
                page_end=request.page_end,
                confidence=confidence,
                extraction_model=request.extraction_model,
                extraction_model_version=(
                    request.extraction_model_version
                ),
                original_extracted_value=json.dumps(
                    receipt.canonical_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                verified_value=None,
                verifier_user_id=(
                    request.document.validated_by
                    if request.document.validation_status
                    == "VALIDATED"
                    else None
                ),
                verification_reason=verification_reason,
            )
            self.db.add(provenance)
            self.db.flush()
            return "CREATED"

        provenance = rows[0]

        if (
            provenance.document_id is not None
            and int(provenance.document_id)
            != int(source_document_pk)
        ):
            raise EvidenceCompletionError(
                "PROVENANCE_DOCUMENT_CONFLICT",
                "FactProvenance is already attached to another SourceDocument",
            )

        changed = False

        if provenance.document_id is None:
            provenance.document_id = int(source_document_pk)
            changed = True

        if request.page_start is not None:
            if (
                provenance.page_start is not None
                and int(provenance.page_start)
                != int(request.page_start)
            ):
                raise EvidenceCompletionError(
                    "PROVENANCE_PAGE_CONFLICT",
                    "page_start differs from established provenance",
                )
            if provenance.page_start is None:
                provenance.page_start = request.page_start
                changed = True

        if request.page_end is not None:
            if (
                provenance.page_end is not None
                and int(provenance.page_end)
                != int(request.page_end)
            ):
                raise EvidenceCompletionError(
                    "PROVENANCE_PAGE_CONFLICT",
                    "page_end differs from established provenance",
                )
            if provenance.page_end is None:
                provenance.page_end = request.page_end
                changed = True

        if (
            provenance.confidence is None
            and confidence is not None
        ):
            provenance.confidence = confidence
            changed = True

        if (
            provenance.extraction_model is None
            and request.extraction_model
        ):
            provenance.extraction_model = (
                request.extraction_model
            )
            changed = True

        if (
            provenance.extraction_model_version is None
            and request.extraction_model_version
        ):
            provenance.extraction_model_version = (
                request.extraction_model_version
            )
            changed = True

        if (
            request.document.validation_status == "VALIDATED"
            and provenance.verifier_user_id is None
        ):
            provenance.verifier_user_id = (
                request.document.validated_by
            )
            provenance.verification_reason = (
                verification_reason
            )
            changed = True

        if changed:
            self.db.flush()
            return "UPDATED"

        return "NOOP"

    def _set_invoice_status(
        self,
        invoice: InvoiceFact,
        requested_status: str | None,
    ) -> str:
        if requested_status is None:
            return "NONE"

        if invoice.invoice_status is None:
            invoice.invoice_status = requested_status
            self.db.flush()
            return "UPDATED"

        if invoice.invoice_status != requested_status:
            raise EvidenceCompletionError(
                "INVOICE_STATUS_CONFLICT",
                f"Invoice status already established as "
                f"{invoice.invoice_status!r}, got {requested_status!r}",
            )

        return "NOOP"

    def _complete_lines(
        self,
        *,
        fact_id: int,
        binding: IDPSourceDocumentBinding,
        request: InvoiceEvidenceCompletionRequest,
    ) -> tuple[str, str | None]:
        existing = self.db.execute(
            select(InvoiceLine)
            .where(
                InvoiceLine.invoice_fact_id
                == int(fact_id)
            )
            .order_by(
                InvoiceLine.line_no,
                InvoiceLine.id,
            )
            .with_for_update()
        ).scalars().all()

        try:
            plan = plan_invoice_line_write(
                existing_rows=existing,
                incoming_lines=request.lines,
                established_fingerprint=(
                    binding.line_payload_fingerprint
                ),
            )
        except InvoiceLineEvidenceError as exc:
            raise EvidenceCompletionError(
                exc.code,
                exc.detail,
            ) from exc

        if plan.action == "CREATED":
            for values in plan.normalized_lines:
                self.db.add(
                    InvoiceLine(
                        invoice_fact_id=int(fact_id),
                        **values,
                    )
                )

            self.db.flush()

        if (
            plan.fingerprint is not None
            and binding.line_payload_fingerprint is None
            and request.lines
        ):
            binding.line_payload_fingerprint = (
                plan.fingerprint
            )
            self.db.flush()

        return (
            plan.action,
            plan.fingerprint,
        )

    def _snapshot(
        self,
        *,
        fact: Fact,
        invoice: InvoiceFact,
    ) -> InvoiceEvidenceSnapshot:
        fact_id = int(fact.id)

        lines = self.db.execute(
            select(InvoiceLine)
            .where(
                InvoiceLine.invoice_fact_id
                == fact_id
            )
            .order_by(
                InvoiceLine.line_no,
                InvoiceLine.id,
            )
        ).scalars().all()

        provenances = self.db.execute(
            select(FactProvenance)
            .where(
                FactProvenance.fact_id
                == fact_id
            )
            .order_by(FactProvenance.id)
        ).scalars().all()

        document_ids = {
            int(item.document_id)
            for item in provenances
            if item.document_id is not None
        }

        validated_document_ids: set[int] = set()

        if document_ids:
            documents = self.db.execute(
                select(SourceDocument).where(
                    SourceDocument.id.in_(
                        document_ids
                    )
                )
            ).scalars().all()

            validated_document_ids = {
                int(document.id)
                for document in documents
                if document.status == "VALIDATED"
            }

        seller_identifiers = []

        if invoice.seller_party_id is not None:
            seller_identifiers = self.db.execute(
                select(PartyIdentifier)
                .where(
                    PartyIdentifier.party_id
                    == int(invoice.seller_party_id),
                    PartyIdentifier.active.is_(True),
                    PartyIdentifier.identifier_type
                    == "TAX_REGISTRATION_ID",
                )
                .order_by(PartyIdentifier.id)
            ).scalars().all()

        relations = self.db.execute(
            select(FactRelationship)
            .where(
                FactRelationship.source_fact_id
                == fact_id
            )
        ).scalars().all()

        line_net_values = [
            Decimal(row.net_amount)
            for row in lines
            if row.net_amount is not None
        ]
        line_vat_values = [
            Decimal(row.vat_amount)
            for row in lines
            if row.vat_amount is not None
        ]

        incomplete_line_count = sum(
            1
            for row in lines
            if (
                row.net_amount is None
                or row.vat_amount is None
            )
        )

        missing_tax_rate_count = sum(
            1
            for row in lines
            if row.tax_rate is None
        )

        return InvoiceEvidenceSnapshot(
            fact_id=fact_id,
            current_validation_status=str(
                fact.validation_status
            ),
            business_identity_key=str(
                fact.business_identity_key
            ),
            invoice_identity_key=str(
                invoice.invoice_identity_key
            ),
            invoice_identity_version=str(
                invoice.invoice_identity_version
            ),
            invoice_number=str(
                invoice.invoice_number
            ),
            invoice_code=invoice.invoice_code,
            invoice_date=invoice.invoice_date,
            invoice_status=invoice.invoice_status,
            seller_party_id=invoice.seller_party_id,
            buyer_party_id=invoice.buyer_party_id,
            gross_amount=invoice.gross_amount,
            net_amount=invoice.net_amount,
            vat_amount=invoice.vat_amount,
            currency=str(invoice.currency),
            line_count=len(lines),
            incomplete_line_count=(
                incomplete_line_count
            ),
            line_missing_tax_rate_count=(
                missing_tax_rate_count
            ),
            line_tax_rates=tuple(
                Decimal(row.tax_rate)
                for row in lines
                if row.tax_rate is not None
            ),
            line_net_sum=(
                sum(
                    line_net_values,
                    Decimal("0"),
                )
                if lines
                else None
            ),
            line_vat_sum=(
                sum(
                    line_vat_values,
                    Decimal("0"),
                )
                if lines
                else None
            ),
            provenance_count=len(
                provenances
            ),
            document_provenance_count=sum(
                1
                for row in provenances
                if row.document_id is not None
            ),
            validated_document_provenance_count=sum(
                1
                for row in provenances
                if (
                    row.document_id is not None
                    and int(row.document_id)
                    in validated_document_ids
                )
            ),
            seller_tax_identifier_count=len(
                seller_identifiers
            ),
            seller_tax_identifier_value=(
                str(
                    seller_identifiers[0]
                    .identifier_value
                )
                if len(seller_identifiers) == 1
                else None
            ),
            reversal_relation_count=sum(
                1
                for row in relations
                if row.relationship_type
                == "REVERSAL_OF"
            ),
            void_relation_count=sum(
                1
                for row in relations
                if row.relationship_type
                == "VOID_RELATION"
            ),
        )

    def _complete(
        self,
        request: InvoiceEvidenceCompletionRequest,
    ) -> InvoiceEvidenceCompletionResult:
        receipt = self._receipt(request)

        self._lock_fact(
            int(receipt.fact_id)
        )

        fact, invoice = self._fact_and_invoice(
            receipt
        )

        try:
            bridge_result = self.source_bridge.bind(
                source_system=request.source_system,
                source_document_id=(
                    request.source_document_id
                ),
                source_extraction_id=(
                    request.source_extraction_id
                ),
                document_sha256=(
                    request.document_sha256
                ),
                fact_id=int(fact.id),
                receipt_id=int(receipt.id),
                evidence=request.document,
            )
        except SourceDocumentBindingError as exc:
            raise EvidenceCompletionError(
                exc.code,
                exc.detail,
            ) from exc

        provenance_outcome = (
            self._complete_provenance(
                request=request,
                receipt=receipt,
                fact_id=int(fact.id),
                source_document_pk=int(
                    bridge_result.source_document.id
                ),
            )
        )

        invoice_status_outcome = (
            self._set_invoice_status(
                invoice,
                request.invoice_status,
            )
        )

        line_outcome, line_fingerprint = (
            self._complete_lines(
                fact_id=int(fact.id),
                binding=bridge_result.binding,
                request=request,
            )
        )

        self.db.flush()

        snapshot = self._snapshot(
            fact=fact,
            invoice=invoice,
        )

        allowed_tax_rates = (
            tuple(
                request.tax_rules.allowed_tax_rates
            )
            if request.tax_rules is not None
            else None
        )

        decision = evaluate_invoice_evidence(
            snapshot,
            allowed_tax_rates=allowed_tax_rates,
        )

        status_changed = apply_task09_decision(
            fact,
            decision,
        )

        if status_changed:
            self.db.flush()

        changed = any(
            value
            not in {
                "NOOP",
                "NONE",
            }
            for value in (
                bridge_result.outcome,
                provenance_outcome,
                invoice_status_outcome,
                line_outcome,
            )
        ) or status_changed

        return InvoiceEvidenceCompletionResult(
            outcome=(
                "COMPLETED"
                if changed
                else "NOOP"
            ),
            receipt_id=int(receipt.id),
            fact_id=int(fact.id),
            source_document_pk=int(
                bridge_result.source_document.id
            ),
            binding_id=int(
                bridge_result.binding.id
            ),
            document_outcome=(
                bridge_result.outcome
            ),
            provenance_outcome=(
                provenance_outcome
            ),
            line_outcome=line_outcome,
            invoice_status_outcome=(
                invoice_status_outcome
            ),
            line_payload_fingerprint=(
                line_fingerprint
            ),
            tax_rule_version=(
                request.tax_rules.rule_version
                if request.tax_rules is not None
                else None
            ),
            task09_ruleset_version=(
                decision.ruleset_version
            ),
            task09_desired_status=(
                decision.desired_status
            ),
            validation_status=str(
                fact.validation_status
            ),
            findings=[
                {
                    "severity": item.severity,
                    "code": item.code,
                    "message": item.message,
                }
                for item in decision.findings
            ],
        )

    def complete(
        self,
        request: InvoiceEvidenceCompletionRequest,
        *,
        commit: bool = True,
    ) -> InvoiceEvidenceCompletionResult:
        try:
            with self.db.begin_nested():
                result = self._complete(
                    request
                )

            if commit:
                self.db.commit()

            return result

        except EvidenceCompletionError:
            if commit:
                self.db.rollback()
            raise

        except Exception:
            if commit:
                self.db.rollback()
            raise
