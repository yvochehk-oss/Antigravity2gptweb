"""Create the runtime mount registry used by the safe scanner/browser.

Mounts are RAG-owned operational metadata.  This revision deliberately does
not touch the Tax-owned shared tables or any Facts/AI Review schema.
"""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "007_mount_configs"
down_revision = "006_shared_schema_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")
    inspector = inspect(bind)
    if "mount_configs" not in inspector.get_table_names():
        op.create_table(
            "mount_configs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("path", sa.String(length=500), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_scan_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("path", name="uq_mount_configs_path"),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes("mount_configs")}
    if "ix_mount_configs_active" not in indexes:
        op.create_index("ix_mount_configs_active", "mount_configs", ["active"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")
    inspector = inspect(bind)
    if "mount_configs" in inspector.get_table_names():
        indexes = {item["name"] for item in inspector.get_indexes("mount_configs")}
        if "ix_mount_configs_active" in indexes:
            op.drop_index("ix_mount_configs_active", table_name="mount_configs")
        op.drop_table("mount_configs")
