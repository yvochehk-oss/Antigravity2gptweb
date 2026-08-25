"""Persistence boundary for AI Review documentary Evidence Packs."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .models import RAGEvidencePack

_PACK_STATUSES = frozenset({"AVAILABLE", "EMPTY", "DEGRADED"})


def _json_safe(value: Any, *, label: str) -> Any:
    """Round-trip a payload through JSON before it is stored.

    Retrieval adapters may return dataclasses or Decimal-like values.  The
    database record must contain only JSON-safe data so replay never depends
    on an in-process Python object.  ``default=str`` is intentionally limited
    to adapter metadata; the resulting payload is still validated as JSON.
    """

    try:
        normalized = json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON serializable") from exc
    return normalized


class RAGEvidencePackService:
    """Create and read immutable documentary evidence snapshots.

    ``create_pack`` flushes but does not commit.  The AI Review service can
    therefore create the pack and its ``AIReviewRun`` in one transaction; the
    run's foreign key is valid before the transaction is committed.
    """

    def __init__(self, db: Session):
        self._db = db

    def create_pack(
        self,
        *,
        project_id: int,
        project_code: str,
        query: str,
        evidence: Sequence[Mapping[str, Any]] | None = None,
        status: str | None = None,
        extra_metadata: Mapping[str, Any] | None = None,
        created_by: str = "ai_review",
    ) -> RAGEvidencePack:
        """Persist one retrieval result, including an explicit empty/degraded state."""

        normalized_evidence = _json_safe(list(evidence or []), label="evidence")
        if not isinstance(normalized_evidence, list):  # defensive after round-trip
            raise ValueError("evidence must be a JSON array")

        normalized_status = str(status or ("AVAILABLE" if normalized_evidence else "EMPTY")).upper()
        if normalized_status not in _PACK_STATUSES:
            raise ValueError(f"unsupported evidence pack status: {normalized_status}")
        if normalized_status == "AVAILABLE" and not normalized_evidence:
            normalized_status = "EMPTY"

        normalized_metadata = _json_safe(dict(extra_metadata or {}), label="extra_metadata")
        if not isinstance(normalized_metadata, dict):
            raise ValueError("extra_metadata must be a JSON object")

        pack = RAGEvidencePack(
            id=str(uuid.uuid4()),
            project_id=int(project_id),
            project_code=str(project_code),
            query=str(query),
            evidence_data=normalized_evidence,
            evidence_count=len(normalized_evidence),
            status=normalized_status,
            extra_metadata=normalized_metadata,
            created_at=datetime.now(timezone.utc).isoformat(),
            created_by=str(created_by),
        )
        self._db.add(pack)
        self._db.flush()
        return pack

    def get_pack(self, pack_id: str) -> RAGEvidencePack | None:
        """Return a stored pack by its canonical identifier."""

        return self._db.query(RAGEvidencePack).filter(RAGEvidencePack.id == str(pack_id)).first()
