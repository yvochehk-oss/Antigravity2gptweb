"""Add the RAG-owned model endpoint pool.

Revision ID: 015_rag_llm_model_pool
Revises: 014_ai_review_evidence_pack_checks
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "015_rag_llm_model_pool"
down_revision: str | None = "014_ai_review_evidence_pack_checks"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create the isolated RAG model pool without touching existing data."""
    op.create_table(
        "rag_llm_model_endpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=400), nullable=False),
        sa.Column(
            "chat_path",
            sa.String(length=240),
            nullable=False,
            server_default="/v1/chat/completions",
        ),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "routing_group",
            sa.String(length=40),
            nullable=False,
            server_default="default",
        ),
        sa.Column("note", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("last_status", sa.String(length=24), nullable=False, server_default="UNKNOWN"),
        sa.Column("last_error_class", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("last_latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("name", name="uq_rag_llm_model_endpoint_name"),
        sa.CheckConstraint(
            "priority >= 0",
            name="ck_rag_llm_model_endpoints_priority_nonnegative",
        ),
        sa.CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 600",
            name="ck_rag_llm_model_endpoints_timeout_range",
        ),
        sa.CheckConstraint(
            "routing_group ~ '^[a-z0-9][a-z0-9_/-]{0,39}$'",
            name="ck_rag_llm_model_endpoints_routing_group_format",
        ),
    )
    op.create_index(
        "ix_rag_llm_model_endpoints_name",
        "rag_llm_model_endpoints",
        ["name"],
    )
    op.create_index(
        "ix_rag_llm_model_endpoints_enabled",
        "rag_llm_model_endpoints",
        ["enabled"],
    )
    op.create_index(
        "ix_rag_llm_model_endpoints_routing_group",
        "rag_llm_model_endpoints",
        ["routing_group"],
    )
    op.create_index(
        "ix_rag_llm_model_endpoints_route_order",
        "rag_llm_model_endpoints",
        ["routing_group", "priority", "id"],
    )


def downgrade() -> None:
    """Remove only the model pool introduced by this revision."""
    op.drop_index("ix_rag_llm_model_endpoints_route_order", table_name="rag_llm_model_endpoints")
    op.drop_index("ix_rag_llm_model_endpoints_routing_group", table_name="rag_llm_model_endpoints")
    op.drop_index("ix_rag_llm_model_endpoints_enabled", table_name="rag_llm_model_endpoints")
    op.drop_index("ix_rag_llm_model_endpoints_name", table_name="rag_llm_model_endpoints")
    op.drop_table("rag_llm_model_endpoints")
