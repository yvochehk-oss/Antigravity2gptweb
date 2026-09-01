"""Task25 IDP -> Canonical SourceDocument bridge."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.v3_evidence_integration_models import (
    IDPSourceDocumentBinding,
)
from app.v3_party_models import SourceDocument

from .evidence_schemas import SourceDocumentEvidence


class SourceDocumentBindingError(ValueError):
    def __init__(
        self,
        code: str,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SourceDocumentBindingResult:
    binding: IDPSourceDocumentBinding
    source_document: SourceDocument
    outcome: str


_SOURCE_STATUS_RANK = {
    "RECEIVED": 0,
    "PARSED": 1,
    "EXTRACTED": 2,
    "VALIDATED": 3,
}


def merge_source_document_status(
    current: str,
    requested: str,
) -> str:
    """Return a monotonic SourceDocument status.

    FAILED is never silently recovered by Task25.
    VALIDATED is never downgraded by a retry carrying weaker evidence.
    """

    if current == "FAILED":
        raise SourceDocumentBindingError(
            "SOURCE_DOCUMENT_FAILED",
            "FAILED SourceDocument requires explicit recovery workflow",
        )

    if current not in _SOURCE_STATUS_RANK:
        raise SourceDocumentBindingError(
            "SOURCE_DOCUMENT_STATUS_UNKNOWN",
            f"unsupported current SourceDocument status {current!r}",
        )

    if requested not in _SOURCE_STATUS_RANK:
        raise SourceDocumentBindingError(
            "SOURCE_DOCUMENT_STATUS_UNKNOWN",
            f"unsupported requested SourceDocument status {requested!r}",
        )

    if _SOURCE_STATUS_RANK[requested] > _SOURCE_STATUS_RANK[current]:
        return requested

    return current


class SourceDocumentBridge:
    def __init__(
        self,
        db: Session,
    ) -> None:
        self.db = db

    def _lock(
        self,
        source_system: str,
        source_document_id: str,
    ) -> None:
        key = (
            "TASK25_SOURCE_DOCUMENT|"
            f"{source_system}|{source_document_id}"
        )

        self.db.execute(
            text(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(:lock_key, 0)
                )
                """
            ),
            {"lock_key": key},
        )

    def _binding(
        self,
        *,
        source_system: str,
        source_document_id: str,
    ) -> IDPSourceDocumentBinding | None:
        return self.db.execute(
            select(IDPSourceDocumentBinding)
            .where(
                IDPSourceDocumentBinding.source_system
                == source_system,
                IDPSourceDocumentBinding.source_document_id
                == source_document_id,
            )
            .with_for_update()
        ).scalar_one_or_none()

    def _source_document(
        self,
        *,
        source_system: str,
        source_document_id: str,
    ) -> SourceDocument | None:
        return self.db.execute(
            select(SourceDocument)
            .where(
                SourceDocument.source_system
                == source_system,
                SourceDocument.external_document_id
                == source_document_id,
            )
            .with_for_update()
        ).scalar_one_or_none()

    @staticmethod
    def _verify_source_document(
        document: SourceDocument,
        *,
        source_system: str,
        source_document_id: str,
        document_sha256: str,
    ) -> None:
        if document.source_system != source_system:
            raise SourceDocumentBindingError(
                "SOURCE_DOCUMENT_IDENTITY_CONFLICT",
                "SourceDocument source_system differs from IDP binding",
            )

        if document.external_document_id != source_document_id:
            raise SourceDocumentBindingError(
                "SOURCE_DOCUMENT_IDENTITY_CONFLICT",
                "SourceDocument external_document_id differs from IDP binding",
            )

        if (
            document.file_sha256 is not None
            and document.file_sha256.lower()
            != document_sha256
        ):
            raise SourceDocumentBindingError(
                "SOURCE_DOCUMENT_SHA_CONFLICT",
                "existing SourceDocument SHA256 differs from IDP document",
            )

        if (
            document.document_type is not None
            and document.document_type != "invoice"
        ):
            raise SourceDocumentBindingError(
                "SOURCE_DOCUMENT_TYPE_CONFLICT",
                "Task25 Invoice evidence cannot bind to a non-Invoice document",
            )

    def bind(
        self,
        *,
        source_system: str,
        source_document_id: str,
        source_extraction_id: str,
        document_sha256: str,
        fact_id: int,
        receipt_id: int,
        evidence: SourceDocumentEvidence,
    ) -> SourceDocumentBindingResult:
        sha = document_sha256.lower()

        self._lock(
            source_system,
            source_document_id,
        )

        binding = self._binding(
            source_system=source_system,
            source_document_id=source_document_id,
        )

        requested_source_status = (
            "VALIDATED"
            if evidence.validation_status == "VALIDATED"
            else "EXTRACTED"
        )

        if binding is not None:
            if binding.document_sha256 != sha:
                raise SourceDocumentBindingError(
                    "SOURCE_DOCUMENT_SHA_CONFLICT",
                    "same IDP document id was reused with a different SHA256",
                )

            if int(binding.fact_id) != int(fact_id):
                raise SourceDocumentBindingError(
                    "SOURCE_DOCUMENT_FACT_CONFLICT",
                    "same IDP document is already bound to another Fact",
                )

            document = self.db.get(
                SourceDocument,
                int(binding.source_document_pk),
            )
            if document is None:
                raise SourceDocumentBindingError(
                    "SOURCE_DOCUMENT_ORPHAN_BINDING",
                    "IDP binding references a missing SourceDocument",
                )

            self._verify_source_document(
                document,
                source_system=source_system,
                source_document_id=source_document_id,
                document_sha256=sha,
            )

            changed = False

            if document.file_sha256 is None:
                document.file_sha256 = sha
                changed = True

            if document.document_type is None:
                document.document_type = "invoice"
                changed = True

            desired_status = merge_source_document_status(
                str(document.status),
                requested_source_status,
            )

            if desired_status != document.status:
                document.status = desired_status
                changed = True

            if (
                evidence.validation_status == "VALIDATED"
                and binding.binding_status != "VALIDATED"
            ):
                binding.binding_status = "VALIDATED"
                binding.validated_by = evidence.validated_by
                binding.validation_reason = evidence.validation_reason
                changed = True

            if changed:
                binding.updated_at = datetime.now(timezone.utc)
                self.db.flush()

            return SourceDocumentBindingResult(
                binding=binding,
                source_document=document,
                outcome="UPGRADED" if changed else "NOOP",
            )

        document = self._source_document(
            source_system=source_system,
            source_document_id=source_document_id,
        )

        document_created = False

        if document is None:
            document = SourceDocument(
                source_system=source_system,
                external_document_id=source_document_id,
                filename=evidence.filename,
                mime_type=evidence.mime_type,
                file_sha256=sha,
                document_type="invoice",
                source_uri=evidence.source_uri,
                status=requested_source_status,
            )
            self.db.add(document)
            self.db.flush()
            document_created = True
        else:
            self._verify_source_document(
                document,
                source_system=source_system,
                source_document_id=source_document_id,
                document_sha256=sha,
            )

            if document.file_sha256 is None:
                document.file_sha256 = sha

            if document.document_type is None:
                document.document_type = "invoice"

            document.status = merge_source_document_status(
                str(document.status),
                requested_source_status,
            )

            if document.mime_type is None and evidence.mime_type:
                document.mime_type = evidence.mime_type

            if document.source_uri is None and evidence.source_uri:
                document.source_uri = evidence.source_uri

            self.db.flush()

        existing_pk_binding = self.db.execute(
            select(IDPSourceDocumentBinding)
            .where(
                IDPSourceDocumentBinding.source_document_pk
                == int(document.id)
            )
            .with_for_update()
        ).scalar_one_or_none()

        if existing_pk_binding is not None:
            raise SourceDocumentBindingError(
                "SOURCE_DOCUMENT_ALREADY_BOUND",
                "Canonical SourceDocument is already owned by another IDP binding",
            )

        binding = IDPSourceDocumentBinding(
            source_system=source_system,
            source_document_id=source_document_id,
            document_sha256=sha,
            source_document_pk=int(document.id),
            fact_id=int(fact_id),
            first_source_extraction_id=source_extraction_id,
            first_receipt_id=int(receipt_id),
            binding_status=(
                "VALIDATED"
                if evidence.validation_status == "VALIDATED"
                else "BOUND"
            ),
            validated_by=(
                evidence.validated_by
                if evidence.validation_status == "VALIDATED"
                else None
            ),
            validation_reason=(
                evidence.validation_reason
                if evidence.validation_status == "VALIDATED"
                else None
            ),
        )

        self.db.add(binding)
        self.db.flush()

        return SourceDocumentBindingResult(
            binding=binding,
            source_document=document,
            outcome="CREATED" if document_created else "UPDATED",
        )
