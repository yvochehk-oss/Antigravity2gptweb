"""Repair the evidence-pack integrity checks on databases already at 013.

Revision 013 declares the status and evidence-count checks when it creates a
new table.  A database that already had an older, unconstrained
``rag_evidence_packs`` table could nevertheless reach revision 013 without
those checks (for example after an earlier out-of-band bootstrap).  This
follow-up migration closes that drift explicitly and validates existing rows
before adding the constraints.
"""

from __future__ import annotations

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from alembic import op

revision = "014_ai_review_evidence_pack_checks"
down_revision = "013_ai_review_evidence_pack_fk"
branch_labels = None
depends_on = None

_TABLE = "rag_evidence_packs"
_CHECKS = {
    "ck_rag_evidence_packs_status": "status IN ('AVAILABLE', 'EMPTY', 'DEGRADED')",
    "ck_rag_evidence_packs_evidence_count": "evidence_count >= 0",
}


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("AI Review evidence-pack checks migration is PostgreSQL-only")


def _check_names() -> set[str]:
    inspector = sa_inspect(op.get_bind())
    return {
        item.get("name")
        for item in inspector.get_check_constraints(_TABLE)
        if item.get("name")
    }


def upgrade() -> None:
    _require_postgresql()
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        raise RuntimeError(f"{_TABLE} must exist before evidence-pack checks migration")

    columns = {item["name"] for item in inspector.get_columns(_TABLE)}
    missing_columns = {"status", "evidence_count"} - columns
    if missing_columns:
        raise RuntimeError(
            f"{_TABLE} is incompatible; missing columns: {', '.join(sorted(missing_columns))}"
        )

    # Do not turn existing invalid data into a migration failure halfway
    # through DDL.  Both statements are read-only and run in Alembic's
    # transaction, before any constraint is created.
    invalid_status = bind.execute(
        text(
            "SELECT COUNT(*) FROM rag_evidence_packs "
            "WHERE status NOT IN ('AVAILABLE', 'EMPTY', 'DEGRADED')"
        )
    ).scalar_one()
    if invalid_status:
        raise RuntimeError(
            f"refusing to add status check: {_TABLE} contains {invalid_status} invalid row(s)"
        )

    invalid_count = bind.execute(
        text("SELECT COUNT(*) FROM rag_evidence_packs WHERE evidence_count < 0")
    ).scalar_one()
    if invalid_count:
        raise RuntimeError(
            f"refusing to add evidence-count check: {_TABLE} contains "
            f"{invalid_count} negative row(s)"
        )

    existing = _check_names()
    for name, expression in _CHECKS.items():
        if name not in existing:
            op.create_check_constraint(name, _TABLE, expression)


def downgrade() -> None:
    _require_postgresql()
    inspector = sa_inspect(op.get_bind())
    if _TABLE not in set(inspector.get_table_names()):
        return

    existing = _check_names()
    for name in _CHECKS:
        if name in existing:
            op.drop_constraint(name, _TABLE, type_="check")
