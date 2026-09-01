"""Exact-only existing-Project resolver for Task29."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Project
from .project_allocation_basis import normalize_project_code


class ProjectResolutionError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def resolve_projects_exact(session: Session, project_codes: Iterable[str]) -> dict[str, Project]:
    """Resolve only exact ``Project.project_code`` values already in the DB.

    No Project is created here. No name/alias/similarity/AI lookup is performed.
    NFKC + trim happens before the exact equality lookup.
    """

    codes = [normalize_project_code(code) for code in project_codes]
    if len(set(codes)) != len(codes):
        raise ProjectResolutionError("DUPLICATE_PROJECT_CODE", "duplicate exact project_code in request")
    if not codes:
        raise ProjectResolutionError("PROJECT_EVIDENCE_INSUFFICIENT", "no project_code supplied")

    rows = session.scalars(
        select(Project)
        .where(Project.project_code.in_(codes))
        .with_for_update(read=True)
    ).all()
    by_code = {str(row.project_code): row for row in rows}
    missing = [code for code in codes if code not in by_code]
    if missing:
        raise ProjectResolutionError(
            "PROJECT_CODE_NOT_FOUND",
            "explicit project_code does not resolve to an existing Project: " + ", ".join(missing),
        )
    return {code: by_code[code] for code in codes}
