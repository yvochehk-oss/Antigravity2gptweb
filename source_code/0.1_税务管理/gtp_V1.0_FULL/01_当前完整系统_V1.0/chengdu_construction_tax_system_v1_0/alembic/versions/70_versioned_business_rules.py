"""Move deterministic business parameters out of Python constants.

Revision ID: 70_versioned_business_rules
Revises: 69_project_entity_code
"""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "70_versioned_business_rules"
down_revision = "69_project_entity_code"
branch_labels = None
depends_on = None

RULE_VERSION = "business_rules_v1"
RISK_SOURCE = "deterministic_risk_scan"
LEGACY_RISK_RULE_VERSION = "risk_rules_v1"


def upgrade() -> None:
    op.create_table(
        "business_rule_parameters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rule_set", sa.String(length=64), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(length=300), nullable=False, server_default=""),
        sa.UniqueConstraint(
            "rule_set", "rule_version", "code",
            name="uq_business_rule_parameter_version_code",
        ),
    )
    op.create_index(
        "ix_business_rule_parameters_active",
        "business_rule_parameters",
        ["rule_set", "rule_version", "enabled"],
        unique=False,
    )

    rows = [
        ("matching", "invoice_over_contract", "1.05"),
        ("matching", "paid_over_invoice", "1.05"),
        ("matching", "fulfilled_over_contract", "1.10"),
        ("risk", "equipment_allowed_invoice_rates", json.dumps([0, 0.03, 0.09, 0.13])),
    ]
    bind = op.get_bind()
    for rule_set, code, value_json in rows:
        bind.execute(
            text(
                "INSERT INTO business_rule_parameters "
                "(rule_set, rule_version, code, value_json, enabled, source) "
                "VALUES (:rule_set, :rule_version, :code, :value_json, true, :source)"
            ),
            {
                "rule_set": rule_set,
                "rule_version": RULE_VERSION,
                "code": code,
                "value_json": value_json,
                "source": "migrated_from_pre_p2_python_constants",
            },
        )

    bind.execute(
        text(
            "UPDATE risk_events SET rule_version = :new_version "
            "WHERE source = :source AND rule_version = :old_version"
        ),
        {
            "new_version": RULE_VERSION,
            "source": RISK_SOURCE,
            "old_version": LEGACY_RISK_RULE_VERSION,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            "UPDATE risk_events SET rule_version = :old_version "
            "WHERE source = :source AND rule_version = :new_version"
        ),
        {
            "old_version": LEGACY_RISK_RULE_VERSION,
            "source": RISK_SOURCE,
            "new_version": RULE_VERSION,
        },
    )
    op.drop_index(
        "ix_business_rule_parameters_active",
        table_name="business_rule_parameters",
    )
    op.drop_table("business_rule_parameters")
