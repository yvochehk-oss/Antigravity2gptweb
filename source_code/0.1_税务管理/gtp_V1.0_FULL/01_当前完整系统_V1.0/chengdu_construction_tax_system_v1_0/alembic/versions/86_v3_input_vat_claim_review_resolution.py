"""Allow reviewed resolution of legacy Input VAT claim assumptions.

Revision ID: 86_v3_input_vat_claim_review_resolution
Revises: 85_v3_vat_output_period_assertions

LEGACY_ASSUMPTION claims remain permanently ineligible for CONFIRMED status, but
human review must be able to resolve them to REJECTED or SUPERSEDED. Resolution
statuses require reviewer identity and timestamp.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "86_v3_input_vat_claim_review_resolution"
down_revision = "85_v3_vat_output_period_assertions"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    op.drop_constraint(
        "ck_input_vat_claims_legacy_assumption_fail_closed",
        "input_vat_claims",
        type_="check",
    )
    op.create_check_constraint(
        "ck_input_vat_claims_legacy_assumption_fail_closed",
        "input_vat_claims",
        "evidence_type <> 'LEGACY_ASSUMPTION' OR "
        "(confidence='LOW' AND claim_status IN ('NEEDS_REVIEW','REJECTED','SUPERSEDED'))",
    )
    op.create_check_constraint(
        "ck_input_vat_claims_resolution_reviewed",
        "input_vat_claims",
        "claim_status NOT IN ('REJECTED','SUPERSEDED') OR "
        "(reviewed_by IS NOT NULL AND btrim(reviewed_by)<>'' AND reviewed_at IS NOT NULL)",
    )


def downgrade() -> None:
    _require_postgresql()
    unresolved = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM input_vat_claims "
            "WHERE evidence_type='LEGACY_ASSUMPTION' "
            "AND claim_status IN ('REJECTED','SUPERSEDED')"
        )
    ).scalar_one()
    if int(unresolved) > 0:
        raise RuntimeError(
            "refuse downgrade: reviewed LEGACY_ASSUMPTION resolutions exist"
        )
    op.drop_constraint(
        "ck_input_vat_claims_resolution_reviewed",
        "input_vat_claims",
        type_="check",
    )
    op.drop_constraint(
        "ck_input_vat_claims_legacy_assumption_fail_closed",
        "input_vat_claims",
        type_="check",
    )
    op.create_check_constraint(
        "ck_input_vat_claims_legacy_assumption_fail_closed",
        "input_vat_claims",
        "evidence_type <> 'LEGACY_ASSUMPTION' OR "
        "(confidence='LOW' AND claim_status='NEEDS_REVIEW')",
    )
