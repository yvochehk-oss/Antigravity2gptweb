"""Phase 3/4 V3 Boss compatibility service backed only by Canonical Facts.

The historical V3 ``facts`` family is frozen as read-only audit data. All
production Boss reads are projections of ``canonical_facts`` plus deterministic
Tax calculations so there is exactly one business fact source.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Project
from app.services.canonical_v3_bridge import CanonicalV3Bridge
from app.services.period_rollforward import build_period_rollforward
from app.services.phase4_accounting import build_project_accounting

EXPECTED_ALEMBIC_HEAD = "99_phase4_accounting_snapshots"


class V3BossReadError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class V3BossService:
    """Read-only V3 API facade over the unified Canonical Facts SSOT."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.bridge = CanonicalV3Bridge(db)

    def _project(self, project_id: int) -> Project:
        project = self.db.get(Project, int(project_id))
        if project is None:
            raise V3BossReadError("PROJECT_NOT_FOUND", f"Project {project_id} does not exist")
        return project

    @staticmethod
    def _project_payload(project: Project) -> dict[str, Any]:
        return {
            "id": int(project.id),
            "code": str(project.code),
            "name": str(project.name),
            "city": project.city,
        }

    def finance(self, project_id: int) -> dict[str, Any]:
        self._project(project_id)
        return self.bridge.finance(int(project_id))

    def four_flow(self, project_id: int) -> dict[str, Any]:
        self._project(project_id)
        return self.bridge.four_flow(int(project_id))

    def tax(
        self,
        project_id: int,
        *,
        reporting_party_id: int | None = None,
        tax_period: date | None = None,
    ) -> dict[str, Any]:
        self._project(project_id)
        return self.bridge.tax(
            int(project_id),
            reporting_party_id=reporting_party_id,
            tax_period=tax_period,
        )

    def evidence_quality(self, project_id: int) -> dict[str, Any]:
        self._project(project_id)
        return self.bridge.evidence_quality(int(project_id))

    def rag_context(self, project_id: int, *, scope: str = "whole_project") -> dict[str, Any]:
        self._project(project_id)
        return self.bridge.rag_context(int(project_id), scope=scope)

    def accounting_rollforward(self, project_id: int, *, period: str) -> dict[str, Any]:
        self._project(project_id)
        return build_period_rollforward(self.db, int(project_id), period)

    def snapshot(
        self,
        project_id: int,
        *,
        reporting_party_id: int | None = None,
        tax_period: date | None = None,
        rag_scope: str = "whole_project",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        finance = self.bridge.finance(int(project_id))
        four_flow = self.bridge.four_flow(int(project_id))
        tax = self.bridge.tax(
            int(project_id),
            reporting_party_id=reporting_party_id,
            tax_period=tax_period,
        )
        rag = self.bridge.rag_context(int(project_id), scope=rag_scope)
        accounting = build_project_accounting(self.db, int(project_id))
        return {
            "data_source": "CANONICAL_FACTS",
            "source_of_truth": "canonical_facts",
            "legacy_v3_facts_used": False,
            "project": self._project_payload(project),
            "finance": finance,
            "four_flow": four_flow,
            "tax": tax,
            "accounting": accounting,
            "evidence_quality": self.bridge.evidence_quality(int(project_id)),
            "rag_context": rag,
            "availability": rag.get("availability"),
            "lineage": accounting["lineage"],
        }

    def system_status(self) -> dict[str, Any]:
        heads = sorted(
            str(row[0])
            for row in self.db.execute(text("SELECT version_num FROM alembic_version_tax")).all()
        )
        head = heads[0] if len(heads) == 1 else None
        return {
            "ready": head == EXPECTED_ALEMBIC_HEAD,
            "phase": 4,
            "alembic_head": head,
            "expected_alembic_head": EXPECTED_ALEMBIC_HEAD,
            "source_of_truth": "canonical_facts",
            "writer": "RAG_CANONICAL_FACTS",
            "reader": "CANONICAL_DIRECT",
            "rag": "canonical_facts",
            "legacy_write_enabled": False,
            "new_fact_write_enabled": False,
            "legacy_frozen": True,
            "legacy_v3_mode": "READ_ONLY_AUDIT",
            "production_seal": "ACTIVE",
        }
