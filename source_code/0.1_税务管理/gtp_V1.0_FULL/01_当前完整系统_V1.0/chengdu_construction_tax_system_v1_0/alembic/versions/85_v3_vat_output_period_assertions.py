"""Add reviewed monthly Output VAT completeness assertions.

Revision ID: 85_v3_vat_output_period_assertions
Revises: 84_v3_entity_vat_ledgers

A VAT ledger must not interpret an empty set of Output VAT events as zero without
reviewed evidence that the month is complete. This additive table records the
reviewed monthly Output VAT total, including an explicit zero total when supported.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "85_v3_vat_output_period_assertions"
down_revision = "84_v3_entity_vat_ledgers"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    op.create_table(
        "vat_output_period_assertions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reporting_party_id",
            sa.Integer(),
            sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tax_period", sa.Date(), nullable=False),
        sa.Column("asserted_output_vat_total", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("source", sa.String(160), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reviewed_by", sa.String(80), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM tax_period) = 1",
            name="ck_vat_output_assertions_period_month_start",
        ),
        sa.CheckConstraint(
            "btrim(source) <> ''",
            name="ck_vat_output_assertions_source_nonblank",
        ),
        sa.CheckConstraint(
            "NOT reviewed OR (reviewed_by IS NOT NULL AND btrim(reviewed_by)<>'' AND reviewed_at IS NOT NULL)",
            name="ck_vat_output_assertions_reviewed_evidence",
        ),
        sa.UniqueConstraint(
            "reporting_party_id",
            "tax_period",
            name="uq_vat_output_assertions_scope",
        ),
    )
    op.create_index(
        "ix_vat_output_assertions_source_document",
        "vat_output_period_assertions",
        ["source_document_id"],
    )

    op.execute(
        """
        CREATE FUNCTION v3_guard_entity_vat_ledger_output_completeness()
        RETURNS trigger AS $$
        DECLARE
            asserted_total numeric(18,2);
            event_total numeric(18,2);
        BEGIN
            SELECT a.asserted_output_vat_total
              INTO asserted_total
              FROM vat_output_period_assertions a
             WHERE a.reporting_party_id = NEW.reporting_party_id
               AND a.tax_period = NEW.tax_period
               AND a.reviewed = true;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'reviewed Output VAT completeness assertion required for party % period %',
                    NEW.reporting_party_id, NEW.tax_period;
            END IF;

            SELECT COALESCE(SUM(e.vat_amount), 0)::numeric(18,2)
              INTO event_total
              FROM output_vat_events e
             WHERE e.reporting_party_id = NEW.reporting_party_id
               AND e.output_vat_period = NEW.tax_period
               AND e.event_status = 'CONFIRMED';

            IF event_total IS DISTINCT FROM asserted_total THEN
                RAISE EXCEPTION 'confirmed Output VAT events total % does not match reviewed assertion %',
                    event_total, asserted_total;
            END IF;

            IF NEW.output_vat IS DISTINCT FROM asserted_total THEN
                RAISE EXCEPTION 'Entity VAT Ledger output_vat % does not match reviewed assertion %',
                    NEW.output_vat, asserted_total;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_v3_guard_entity_vat_ledger_output_completeness
        BEFORE INSERT OR UPDATE OF reporting_party_id, tax_period, output_vat
        ON entity_vat_ledgers
        FOR EACH ROW EXECUTE FUNCTION v3_guard_entity_vat_ledger_output_completeness()
        """
    )


def downgrade() -> None:
    _require_postgresql()
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v3_guard_entity_vat_ledger_output_completeness ON entity_vat_ledgers"
    )
    op.execute("DROP FUNCTION IF EXISTS v3_guard_entity_vat_ledger_output_completeness()")
    op.drop_index(
        "ix_vat_output_assertions_source_document",
        table_name="vat_output_period_assertions",
    )
    op.drop_table("vat_output_period_assertions")
