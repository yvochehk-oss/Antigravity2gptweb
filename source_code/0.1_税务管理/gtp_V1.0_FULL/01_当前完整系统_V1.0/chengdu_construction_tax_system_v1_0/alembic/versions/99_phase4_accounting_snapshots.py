"""Phase 4 accounting report snapshots and triple-lineage persistence."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "99_phase4_accounting_snapshots"
down_revision = "98_v3_explicit_fact_relationship_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounting_report_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("report_sequence", sa.Integer(), nullable=False),
        sa.Column("report_version", sa.String(80), nullable=False),
        sa.Column("engine_version", sa.String(80), nullable=False),
        sa.Column("fact_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("calculation_parameters_hash", sa.String(64), nullable=False),
        sa.Column("fact_versions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("calculation_parameters_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "project_id",
            "report_sequence",
            name="uq_accounting_report_project_sequence",
        ),
        sa.UniqueConstraint("report_version", name="uq_accounting_report_version"),
        sa.UniqueConstraint(
            "project_id",
            "engine_version",
            "fact_snapshot_hash",
            "calculation_parameters_hash",
            name="uq_accounting_report_fact_engine_params",
        ),
    )
    op.create_index(
        "ix_accounting_report_project_created",
        "accounting_report_snapshots",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_accounting_report_project_created",
        table_name="accounting_report_snapshots",
    )
    op.drop_table("accounting_report_snapshots")
