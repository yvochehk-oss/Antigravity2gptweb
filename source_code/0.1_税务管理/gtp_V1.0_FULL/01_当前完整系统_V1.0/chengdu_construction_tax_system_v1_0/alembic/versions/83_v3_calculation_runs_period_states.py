"""Add Calculation Runs and Tax Period States with closed-period restatement guard.

Revision ID: 83_v3_calculation_runs_period_states
Revises: 82_v3_project_tax_prepayment
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "83_v3_calculation_runs_period_states"
down_revision = "82_v3_project_tax_prepayment"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()

    op.create_table(
        "calculation_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reporting_party_id",
            sa.Integer(),
            sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tax_type", sa.String(32), nullable=False),
        sa.Column("tax_period", sa.Date(), nullable=False),
        sa.Column("run_kind", sa.String(16), nullable=False),
        sa.Column("run_status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("ruleset_version", sa.String(64), nullable=False),
        sa.Column("input_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=True),
        sa.Column(
            "supersedes_run_id",
            sa.Integer(),
            sa.ForeignKey("calculation_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM tax_period) = 1",
            name="ck_calculation_runs_period_month_start",
        ),
        sa.CheckConstraint(
            "run_kind IN ('STANDARD','RESTATEMENT')",
            name="ck_calculation_runs_kind",
        ),
        sa.CheckConstraint(
            "run_status IN ('DRAFT','SUCCEEDED','FAILED')",
            name="ck_calculation_runs_status",
        ),
        sa.CheckConstraint(
            "(run_kind='STANDARD' AND supersedes_run_id IS NULL) OR "
            "(run_kind='RESTATEMENT' AND supersedes_run_id IS NOT NULL)",
            name="ck_calculation_runs_restatement_link",
        ),
        sa.CheckConstraint(
            "supersedes_run_id IS NULL OR supersedes_run_id <> id",
            name="ck_calculation_runs_not_self_supersede",
        ),
        sa.CheckConstraint(
            "run_status='DRAFT' OR completed_at IS NOT NULL",
            name="ck_calculation_runs_terminal_completed_at",
        ),
        sa.CheckConstraint(
            "run_status<>'SUCCEEDED' OR (result_sha256 IS NOT NULL AND length(result_sha256)=64)",
            name="ck_calculation_runs_success_result_hash",
        ),
        sa.CheckConstraint(
            "length(input_snapshot_sha256)=64",
            name="ck_calculation_runs_input_hash_length",
        ),
    )
    op.create_index(
        "ix_calculation_runs_scope",
        "calculation_runs",
        ["reporting_party_id", "tax_type", "tax_period"],
    )
    op.create_index("ix_calculation_runs_supersedes", "calculation_runs", ["supersedes_run_id"])
    op.create_index("ix_calculation_runs_status", "calculation_runs", ["run_status"])

    op.create_table(
        "tax_period_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reporting_party_id",
            sa.Integer(),
            sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tax_type", sa.String(32), nullable=False),
        sa.Column("tax_period", sa.Date(), nullable=False),
        sa.Column("state", sa.String(12), nullable=False, server_default="OPEN"),
        sa.Column(
            "current_run_id",
            sa.Integer(),
            sa.ForeignKey("calculation_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "closed_run_id",
            sa.Integer(),
            sa.ForeignKey("calculation_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("closed_by", sa.String(80), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM tax_period) = 1",
            name="ck_tax_period_states_period_month_start",
        ),
        sa.CheckConstraint(
            "state IN ('OPEN','CLOSED')",
            name="ck_tax_period_states_state",
        ),
        sa.CheckConstraint("state_version >= 1", name="ck_tax_period_states_version_positive"),
        sa.CheckConstraint(
            "(state='OPEN' AND closed_run_id IS NULL AND closed_by IS NULL AND closed_at IS NULL) OR "
            "(state='CLOSED' AND current_run_id IS NOT NULL AND closed_run_id IS NOT NULL "
            "AND closed_by IS NOT NULL AND btrim(closed_by)<>'' AND closed_at IS NOT NULL)",
            name="ck_tax_period_states_close_fields",
        ),
        sa.UniqueConstraint(
            "reporting_party_id",
            "tax_type",
            "tax_period",
            name="uq_tax_period_states_scope",
        ),
    )
    op.create_index("ix_tax_period_states_current_run", "tax_period_states", ["current_run_id"])
    op.create_index("ix_tax_period_states_closed_run", "tax_period_states", ["closed_run_id"])
    op.create_index("ix_tax_period_states_state", "tax_period_states", ["state"])

    op.execute(
        """
        CREATE FUNCTION v3_guard_tax_period_state() RETURNS trigger AS $$
        DECLARE
            r calculation_runs%ROWTYPE;
        BEGIN
            IF NEW.current_run_id IS NOT NULL THEN
                SELECT * INTO r FROM calculation_runs WHERE id = NEW.current_run_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'current calculation run % not found', NEW.current_run_id;
                END IF;
                IF r.reporting_party_id <> NEW.reporting_party_id
                   OR r.tax_type <> NEW.tax_type
                   OR r.tax_period <> NEW.tax_period THEN
                    RAISE EXCEPTION 'calculation run scope does not match tax period state';
                END IF;
                IF r.run_status <> 'SUCCEEDED' THEN
                    RAISE EXCEPTION 'only SUCCEEDED calculation runs may become current';
                END IF;
            END IF;

            IF TG_OP = 'INSERT' THEN
                IF NEW.state = 'OPEN' AND NEW.current_run_id IS NOT NULL AND r.run_kind <> 'STANDARD' THEN
                    RAISE EXCEPTION 'OPEN period current run must be STANDARD';
                END IF;
                IF NEW.state = 'CLOSED' THEN
                    IF NEW.current_run_id IS DISTINCT FROM NEW.closed_run_id THEN
                        RAISE EXCEPTION 'initial CLOSED state must anchor closed_run_id to current_run_id';
                    END IF;
                    IF r.run_kind <> 'STANDARD' THEN
                        RAISE EXCEPTION 'initial close must use a STANDARD run';
                    END IF;
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.state = 'CLOSED' AND NEW.state <> 'CLOSED' THEN
                RAISE EXCEPTION 'CLOSED tax period cannot be reopened';
            END IF;

            IF OLD.state = 'OPEN' AND NEW.state = 'OPEN' THEN
                IF NEW.current_run_id IS DISTINCT FROM OLD.current_run_id
                   AND NEW.current_run_id IS NOT NULL
                   AND r.run_kind <> 'STANDARD' THEN
                    RAISE EXCEPTION 'OPEN period current run replacement must be STANDARD';
                END IF;
            ELSIF OLD.state = 'OPEN' AND NEW.state = 'CLOSED' THEN
                IF NEW.current_run_id IS NULL OR r.run_kind <> 'STANDARD' THEN
                    RAISE EXCEPTION 'closing an OPEN period requires a SUCCEEDED STANDARD run';
                END IF;
                IF NEW.closed_run_id IS DISTINCT FROM NEW.current_run_id THEN
                    RAISE EXCEPTION 'closed_run_id must equal current_run_id on first close';
                END IF;
            ELSIF OLD.state = 'CLOSED' AND NEW.state = 'CLOSED' THEN
                IF NEW.closed_run_id IS DISTINCT FROM OLD.closed_run_id
                   OR NEW.closed_at IS DISTINCT FROM OLD.closed_at
                   OR NEW.closed_by IS DISTINCT FROM OLD.closed_by THEN
                    RAISE EXCEPTION 'closed anchor metadata is immutable';
                END IF;
                IF NEW.current_run_id IS DISTINCT FROM OLD.current_run_id THEN
                    IF r.run_kind <> 'RESTATEMENT' OR r.supersedes_run_id IS DISTINCT FROM OLD.current_run_id THEN
                        RAISE EXCEPTION 'closed-period replacement requires RESTATEMENT superseding current run';
                    END IF;
                END IF;
            END IF;

            NEW.state_version := OLD.state_version + 1;
            NEW.updated_at := CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_v3_guard_tax_period_state
        BEFORE INSERT OR UPDATE ON tax_period_states
        FOR EACH ROW EXECUTE FUNCTION v3_guard_tax_period_state()
        """
    )


def downgrade() -> None:
    _require_postgresql()
    op.execute("DROP TRIGGER IF EXISTS trg_v3_guard_tax_period_state ON tax_period_states")
    op.execute("DROP FUNCTION IF EXISTS v3_guard_tax_period_state()")
    op.drop_index("ix_tax_period_states_state", table_name="tax_period_states")
    op.drop_index("ix_tax_period_states_closed_run", table_name="tax_period_states")
    op.drop_index("ix_tax_period_states_current_run", table_name="tax_period_states")
    op.drop_table("tax_period_states")
    op.drop_index("ix_calculation_runs_status", table_name="calculation_runs")
    op.drop_index("ix_calculation_runs_supersedes", table_name="calculation_runs")
    op.drop_index("ix_calculation_runs_scope", table_name="calculation_runs")
    op.drop_table("calculation_runs")
