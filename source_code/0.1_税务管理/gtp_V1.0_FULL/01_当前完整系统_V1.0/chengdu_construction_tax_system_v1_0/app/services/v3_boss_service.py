"""Task32 Native V3 Boss read service.

This module is an aggregation boundary only.  Financial, relationship, tax,
and RAG semantics stay owned by Tasks29-31/17.  Production routing is verified
read-only before any result is exposed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.canonical_context import build_canonical_context
from app.domain.finance.canonical_four_flow import CanonicalFourFlow
from app.domain.finance.canonical_project_finance import CanonicalProjectFinance
from app.domain.tax.canonical_project_tax import CanonicalProjectTax
from app.integration.idp_canonical.production_route_guard import ProductionRouteGuard
from app.models import Project

EXPECTED_ALEMBIC_HEAD = "98_v3_explicit_fact_relationship_graph"


class V3BossReadError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class _ReadBundle:
    project: Project
    finance: dict[str, Any]
    four_flow: dict[str, Any]


class V3BossService:
    """Thin read orchestrator over approved canonical V3 read models."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.route_guard = ProductionRouteGuard(db)

    def _guard(self):
        return self.route_guard.require(scope="GLOBAL")

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

    def _bundle(self, project_id: int) -> _ReadBundle:
        project = self._project(project_id)
        finance = CanonicalProjectFinance(self.db).read(int(project_id))
        four_flow = CanonicalFourFlow(self.db).read(
            int(project_id), eligible_fact_ids=set(finance.get("eligible_fact_ids", []))
        )
        return _ReadBundle(project=project, finance=finance, four_flow=four_flow)

    def finance(self, project_id: int) -> dict[str, Any]:
        self._guard()
        self._project(project_id)
        return CanonicalProjectFinance(self.db).read(int(project_id))

    def four_flow(self, project_id: int) -> dict[str, Any]:
        self._guard()
        return self._bundle(project_id).four_flow

    def tax(
        self,
        project_id: int,
        *,
        reporting_party_id: int | None = None,
        tax_period: date | None = None,
    ) -> dict[str, Any]:
        self._guard()
        self._project(project_id)
        return CanonicalProjectTax(self.db).read(
            int(project_id),
            reporting_party_id=reporting_party_id,
            tax_period=tax_period,
        )

    def evidence_quality(self, project_id: int) -> dict[str, Any]:
        self._guard()
        bundle = self._bundle(project_id)
        return {
            "project_id": int(project_id),
            "finance": bundle.finance.get("evidence_quality", []),
            "four_flow": bundle.four_flow.get("evidence_quality", []),
            "relationship_coverage": bundle.four_flow.get("coverage", {}),
            "invoice_payment_amount_allocation": bundle.four_flow.get(
                "invoice_payment_amount_allocation", {}
            ),
        }

    def rag_context(self, project_id: int, *, scope: str = "whole_project") -> dict[str, Any]:
        self._guard()
        self._project(project_id)
        return {
            "data_source": "CANONICAL_FACTS",
            "context": build_canonical_context(self.db, int(project_id), scope),
        }

    def snapshot(
        self,
        project_id: int,
        *,
        reporting_party_id: int | None = None,
        tax_period: date | None = None,
        rag_scope: str = "whole_project",
    ) -> dict[str, Any]:
        self._guard()
        bundle = self._bundle(project_id)
        tax = CanonicalProjectTax(self.db).read(
            int(project_id),
            reporting_party_id=reporting_party_id,
            tax_period=tax_period,
        )
        rag = build_canonical_context(self.db, int(project_id), rag_scope)
        quality = {
            "finance": bundle.finance.get("evidence_quality", []),
            "four_flow": bundle.four_flow.get("evidence_quality", []),
            "relationship_coverage": bundle.four_flow.get("coverage", {}),
            "invoice_payment_amount_allocation": bundle.four_flow.get(
                "invoice_payment_amount_allocation", {}
            ),
        }
        return {
            "data_source": "CANONICAL_FACTS",
            "project": self._project_payload(bundle.project),
            "finance": bundle.finance,
            "four_flow": bundle.four_flow,
            "tax": tax,
            "evidence_quality": quality,
            "rag_context": rag,
            "availability": rag.get("availability"),
        }

    def system_status(self) -> dict[str, Any]:
        route = self._guard()
        heads = sorted(
            str(row[0])
            for row in self.db.execute(text("SELECT version_num FROM alembic_version_tax")).all()
        )
        head = heads[0] if len(heads) == 1 else None
        ready = head == EXPECTED_ALEMBIC_HEAD
        return {
            "ready": ready,
            "alembic_head": head,
            "expected_alembic_head": EXPECTED_ALEMBIC_HEAD,
            "writer": route.writer_mode,
            "reader": route.new_fact_read_mode,
            "rag": route.rag_source,
            "legacy_write_enabled": route.legacy_write_enabled,
            "new_fact_write_enabled": route.new_fact_write_enabled,
            "legacy_frozen": route.legacy_frozen,
            "production_seal": "ACTIVE",
            "seal_finalized_by": route.seal_finalized_by,
            "seal_finalized_at": route.seal_finalized_at,
        }
