"""Add reviewed, deterministic entity-tax management ledgers.

This revision is additive.  Management inputs carry an explicit legal-entity
and month scope, while the ledger is an immutable result of a SUCCEEDED
``ENTITY_TAX`` calculation run paired with the official VAT ledger for the
same scope.  No source-period inference is performed by this schema.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "87_v3_entity_tax_ledgers"
down_revision = "86_v3_input_vat_claim_review_resolution"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()

    op.create_table(
        "entity_tax_management_inputs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reporting_party_id",
            sa.Integer(),
            sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tax_period", sa.Date(), nullable=False),
        sa.Column("input_type", sa.String(16), nullable=False),
        sa.Column(
            "input_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
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
            name="ck_entity_tax_management_inputs_period_month_start",
        ),
        sa.CheckConstraint(
            "input_type IN ('REVENUE','REAL_COST')",
            name="ck_entity_tax_management_inputs_type",
        ),
        sa.CheckConstraint(
            "input_version >= 1",
            name="ck_entity_tax_management_inputs_version_positive",
        ),
        sa.CheckConstraint(
            "amount >= 0",
            name="ck_entity_tax_management_inputs_amount_nonnegative",
        ),
        sa.CheckConstraint(
            "btrim(source) <> ''",
            name="ck_entity_tax_management_inputs_source_nonblank",
        ),
        sa.CheckConstraint(
            "reviewed AND reviewed_by IS NOT NULL AND btrim(reviewed_by)<>'' AND reviewed_at IS NOT NULL",
            name="ck_entity_tax_management_inputs_reviewed",
        ),
        sa.UniqueConstraint(
            "reporting_party_id",
            "tax_period",
            "input_type",
            "input_version",
            name="uq_entity_tax_management_inputs_scope_type_version",
        ),
    )
    op.create_index(
        "ix_entity_tax_management_inputs_scope",
        "entity_tax_management_inputs",
        ["reporting_party_id", "tax_period"],
    )
    op.create_index(
        "ix_entity_tax_management_inputs_source_document",
        "entity_tax_management_inputs",
        ["source_document_id"],
    )

    op.create_table(
        "entity_tax_ledgers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "calculation_run_id",
            sa.Integer(),
            sa.ForeignKey("calculation_runs.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "reporting_party_id",
            sa.Integer(),
            sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tax_period", sa.Date(), nullable=False),
        sa.Column(
            "entity_vat_ledger_id",
            sa.Integer(),
            sa.ForeignKey("entity_vat_ledgers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revenue", sa.Numeric(18, 2), nullable=False),
        sa.Column("real_cost", sa.Numeric(18, 2), nullable=False),
        sa.Column("estimated_profit", sa.Numeric(18, 2), nullable=False),
        sa.Column("estimated_cit", sa.Numeric(18, 2), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("input_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM tax_period) = 1",
            name="ck_entity_tax_ledgers_period_month_start",
        ),
        sa.CheckConstraint(
            "revenue >= 0 AND real_cost >= 0",
            name="ck_entity_tax_ledgers_amounts_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_profit = revenue - real_cost",
            name="ck_entity_tax_ledgers_profit_formula",
        ),
        sa.CheckConstraint(
            "estimated_cit >= 0",
            name="ck_entity_tax_ledgers_cit_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_profit > 0 OR estimated_cit = 0",
            name="ck_entity_tax_ledgers_loss_zero_cit",
        ),
        sa.CheckConstraint(
            "btrim(rule_version) <> ''",
            name="ck_entity_tax_ledgers_rule_version_nonblank",
        ),
        sa.CheckConstraint(
            "length(input_snapshot_sha256) = 64",
            name="ck_entity_tax_ledgers_input_hash_length",
        ),
        sa.CheckConstraint(
            "length(result_sha256) = 64",
            name="ck_entity_tax_ledgers_result_hash_length",
        ),
        sa.UniqueConstraint(
            "reporting_party_id",
            "tax_period",
            "calculation_run_id",
            name="uq_entity_tax_ledgers_scope_run",
        ),
    )
    op.create_index(
        "ix_entity_tax_ledgers_scope",
        "entity_tax_ledgers",
        ["reporting_party_id", "tax_period"],
    )

    op.create_table(
        "entity_tax_ledger_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ledger_id",
            sa.Integer(),
            sa.ForeignKey("entity_tax_ledgers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("component_type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "management_input_id",
            sa.Integer(),
            sa.ForeignKey("entity_tax_management_inputs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "component_type IN ('REVENUE','REAL_COST','ESTIMATED_CIT')",
            name="ck_entity_tax_ledger_components_type",
        ),
        sa.CheckConstraint(
            "amount >= 0",
            name="ck_entity_tax_ledger_components_amount_nonnegative",
        ),
        sa.CheckConstraint(
            "(component_type IN ('REVENUE','REAL_COST') AND management_input_id IS NOT NULL) OR "
            "(component_type='ESTIMATED_CIT' AND management_input_id IS NULL)",
            name="ck_entity_tax_ledger_components_typed_source",
        ),
        sa.UniqueConstraint(
            "ledger_id",
            "component_type",
            name="uq_entity_tax_ledger_components_type",
        ),
        sa.UniqueConstraint(
            "ledger_id",
            "management_input_id",
            name="uq_entity_tax_ledger_components_input",
        ),
    )
    op.create_index(
        "ix_entity_tax_ledger_components_ledger",
        "entity_tax_ledger_components",
        ["ledger_id"],
    )
    op.create_index(
        "ix_entity_tax_ledger_components_input",
        "entity_tax_ledger_components",
        ["management_input_id"],
    )

    op.execute(
        """
        CREATE FUNCTION v3_guard_entity_tax_ledger_scope()
        RETURNS trigger AS $$
        DECLARE
            r calculation_runs%ROWTYPE;
            v entity_vat_ledgers%ROWTYPE;
            vr calculation_runs%ROWTYPE;
            e internal_entities%ROWTYPE;
        BEGIN
            SELECT * INTO e
              FROM internal_entities
             WHERE party_id = NEW.reporting_party_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'entity tax reporting party % not found', NEW.reporting_party_id;
            END IF;
            IF e.legal_entity IS NOT TRUE THEN
                RAISE EXCEPTION 'entity tax ledger requires a legal entity reporting party';
            END IF;

            SELECT * INTO r
              FROM calculation_runs
             WHERE id = NEW.calculation_run_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'entity tax calculation run % not found', NEW.calculation_run_id;
            END IF;
            IF r.run_status <> 'SUCCEEDED' THEN
                RAISE EXCEPTION 'entity tax ledger requires a SUCCEEDED calculation run';
            END IF;
            IF r.tax_type <> 'ENTITY_TAX' THEN
                RAISE EXCEPTION 'entity tax ledger requires tax_type ENTITY_TAX';
            END IF;
            IF r.reporting_party_id <> NEW.reporting_party_id
               OR r.tax_period <> NEW.tax_period THEN
                RAISE EXCEPTION 'entity tax ledger scope does not match calculation run';
            END IF;
            IF NEW.rule_version IS DISTINCT FROM r.ruleset_version THEN
                RAISE EXCEPTION 'entity tax ledger rule version does not match calculation run';
            END IF;
            IF NEW.input_snapshot_sha256 IS DISTINCT FROM r.input_snapshot_sha256 THEN
                RAISE EXCEPTION 'entity tax ledger input hash does not match calculation run';
            END IF;
            IF NEW.result_sha256 IS DISTINCT FROM r.result_sha256 THEN
                RAISE EXCEPTION 'entity tax ledger result hash does not match calculation run';
            END IF;

            SELECT * INTO v
              FROM entity_vat_ledgers
             WHERE id = NEW.entity_vat_ledger_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'entity VAT ledger % not found', NEW.entity_vat_ledger_id;
            END IF;
            IF v.reporting_party_id <> NEW.reporting_party_id
               OR v.tax_period <> NEW.tax_period THEN
                RAISE EXCEPTION 'entity VAT ledger scope does not match entity tax ledger';
            END IF;

            SELECT * INTO vr
              FROM calculation_runs
             WHERE id = v.calculation_run_id;
            IF NOT FOUND OR vr.run_status <> 'SUCCEEDED' OR vr.tax_type <> 'VAT' THEN
                RAISE EXCEPTION 'entity tax ledger requires a SUCCEEDED VAT calculation ledger';
            END IF;
            IF vr.reporting_party_id <> v.reporting_party_id
               OR vr.tax_period <> v.tax_period THEN
                RAISE EXCEPTION 'entity VAT ledger scope does not match its calculation run';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_v3_guard_entity_tax_ledger_scope
        BEFORE INSERT OR UPDATE OF calculation_run_id, reporting_party_id,
            tax_period, entity_vat_ledger_id, rule_version,
            input_snapshot_sha256, result_sha256
        ON entity_tax_ledgers
        FOR EACH ROW EXECUTE FUNCTION v3_guard_entity_tax_ledger_scope()
        """
    )

    op.execute(
        """
        CREATE FUNCTION v3_guard_entity_tax_ledger_period_state()
        RETURNS trigger AS $$
        DECLARE
            r calculation_runs%ROWTYPE;
            s tax_period_states%ROWTYPE;
        BEGIN
            SELECT * INTO r
              FROM calculation_runs
             WHERE id = NEW.calculation_run_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'entity tax calculation run % not found', NEW.calculation_run_id;
            END IF;
            SELECT * INTO s
              FROM tax_period_states
             WHERE reporting_party_id = NEW.reporting_party_id
               AND tax_type = 'ENTITY_TAX'
               AND tax_period = NEW.tax_period;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'ENTITY_TAX TaxPeriodState is required for party % period %',
                    NEW.reporting_party_id, NEW.tax_period;
            END IF;
            IF s.current_run_id IS DISTINCT FROM NEW.calculation_run_id THEN
                RAISE EXCEPTION 'entity tax ledger run is not the current TaxPeriodState run';
            END IF;
            IF s.state = 'OPEN' AND r.run_kind <> 'STANDARD' THEN
                RAISE EXCEPTION 'OPEN ENTITY_TAX period requires a STANDARD run';
            END IF;
            IF s.state = 'CLOSED' THEN
                IF r.run_kind = 'STANDARD' AND s.closed_run_id IS DISTINCT FROM NEW.calculation_run_id THEN
                    RAISE EXCEPTION 'CLOSED ENTITY_TAX period STANDARD ledger must be the initial close';
                END IF;
                IF r.run_kind = 'RESTATEMENT' AND r.supersedes_run_id IS NULL THEN
                    RAISE EXCEPTION 'CLOSED ENTITY_TAX replacement requires RESTATEMENT supersession';
                END IF;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_v3_guard_entity_tax_ledger_period_state
        AFTER INSERT OR UPDATE ON entity_tax_ledgers
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION v3_guard_entity_tax_ledger_period_state()
        """
    )

    op.execute(
        """
        CREATE FUNCTION v3_guard_entity_tax_ledger_component_source()
        RETURNS trigger AS $$
        DECLARE
            l entity_tax_ledgers%ROWTYPE;
            i entity_tax_management_inputs%ROWTYPE;
        BEGIN
            SELECT * INTO l
              FROM entity_tax_ledgers
             WHERE id = NEW.ledger_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'entity tax ledger % not found', NEW.ledger_id;
            END IF;

            IF NEW.component_type = 'ESTIMATED_CIT' THEN
                IF NEW.amount IS DISTINCT FROM l.estimated_cit THEN
                    RAISE EXCEPTION 'ESTIMATED_CIT component does not match ledger result';
                END IF;
                RETURN NEW;
            END IF;

            IF NEW.component_type NOT IN ('REVENUE','REAL_COST')
               OR NEW.management_input_id IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT * INTO i
              FROM entity_tax_management_inputs
             WHERE id = NEW.management_input_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'entity tax management input % not found', NEW.management_input_id;
            END IF;
            IF NOT i.reviewed THEN
                RAISE EXCEPTION 'entity tax component requires reviewed management input';
            END IF;
            IF i.reporting_party_id <> l.reporting_party_id
               OR i.tax_period <> l.tax_period
               OR i.input_type <> NEW.component_type THEN
                RAISE EXCEPTION 'management input scope/type does not match entity tax component';
            END IF;
            IF i.amount IS DISTINCT FROM NEW.amount THEN
                RAISE EXCEPTION 'entity tax component amount does not match management input';
            END IF;
            IF (NEW.component_type = 'REVENUE' AND l.revenue IS DISTINCT FROM NEW.amount)
               OR (NEW.component_type = 'REAL_COST' AND l.real_cost IS DISTINCT FROM NEW.amount) THEN
                RAISE EXCEPTION 'entity tax component amount does not match ledger result';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_v3_guard_entity_tax_ledger_component_source
        BEFORE INSERT OR UPDATE ON entity_tax_ledger_components
        FOR EACH ROW EXECUTE FUNCTION v3_guard_entity_tax_ledger_component_source()
        """
    )

    op.execute(
        """
        CREATE FUNCTION v3_guard_entity_tax_ledger_component_reconciliation()
        RETURNS trigger AS $$
        DECLARE
            target_ledger_id integer;
            l entity_tax_ledgers%ROWTYPE;
            component_count integer;
            revenue_total numeric(18,2);
            cost_total numeric(18,2);
            cit_total numeric(18,2);
        BEGIN
            IF TG_TABLE_NAME = 'entity_tax_ledgers' THEN
                target_ledger_id := NEW.id;
            ELSIF TG_OP = 'DELETE' THEN
                target_ledger_id := OLD.ledger_id;
            ELSE
                target_ledger_id := NEW.ledger_id;
            END IF;

            SELECT * INTO l FROM entity_tax_ledgers WHERE id = target_ledger_id;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            SELECT count(*) INTO component_count
              FROM entity_tax_ledger_components c
             WHERE c.ledger_id = target_ledger_id;
            IF component_count <> 3 THEN
                RAISE EXCEPTION 'entity tax ledger % requires exactly three typed components', target_ledger_id;
            END IF;
            SELECT
                COALESCE(SUM(amount) FILTER (WHERE component_type='REVENUE'), 0)::numeric(18,2),
                COALESCE(SUM(amount) FILTER (WHERE component_type='REAL_COST'), 0)::numeric(18,2),
                COALESCE(SUM(amount) FILTER (WHERE component_type='ESTIMATED_CIT'), 0)::numeric(18,2)
              INTO revenue_total, cost_total, cit_total
              FROM entity_tax_ledger_components c
             WHERE c.ledger_id = target_ledger_id;
            IF revenue_total IS DISTINCT FROM l.revenue
               OR cost_total IS DISTINCT FROM l.real_cost
               OR cit_total IS DISTINCT FROM l.estimated_cit THEN
                RAISE EXCEPTION 'entity tax ledger % components do not reconcile to ledger totals', target_ledger_id;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_v3_guard_entity_tax_ledger_reconciliation
        AFTER INSERT OR UPDATE ON entity_tax_ledgers
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION v3_guard_entity_tax_ledger_component_reconciliation()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_v3_guard_entity_tax_ledger_component_reconciliation
        AFTER INSERT OR UPDATE OR DELETE ON entity_tax_ledger_components
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION v3_guard_entity_tax_ledger_component_reconciliation()
        """
    )

    op.execute(
        """
        CREATE FUNCTION v3_guard_entity_tax_ledger_immutable()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'terminal entity tax ledger is immutable';
            END IF;
            RAISE EXCEPTION 'terminal entity tax ledger is immutable';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_v3_guard_entity_tax_ledger_immutable
        BEFORE UPDATE OR DELETE ON entity_tax_ledgers
        FOR EACH ROW EXECUTE FUNCTION v3_guard_entity_tax_ledger_immutable()
        """
    )

    op.execute(
        """
        CREATE FUNCTION v3_guard_entity_tax_ledger_component_immutable()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'terminal entity tax ledger components are immutable';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_v3_guard_entity_tax_ledger_component_immutable
        BEFORE UPDATE OR DELETE ON entity_tax_ledger_components
        FOR EACH ROW EXECUTE FUNCTION v3_guard_entity_tax_ledger_component_immutable()
        """
    )

    op.execute(
        """
        CREATE FUNCTION v3_guard_entity_tax_management_input_mutation()
        RETURNS trigger AS $$
        DECLARE
            e internal_entities%ROWTYPE;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                SELECT * INTO e
                  FROM internal_entities
                 WHERE party_id = NEW.reporting_party_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'entity tax management input reporting party % not found',
                        NEW.reporting_party_id;
                END IF;
                IF e.legal_entity IS NOT TRUE THEN
                    RAISE EXCEPTION 'entity tax management input requires a legal entity reporting party';
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'UPDATE' THEN
                SELECT * INTO e
                  FROM internal_entities
                 WHERE party_id = NEW.reporting_party_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'entity tax management input reporting party % not found',
                        NEW.reporting_party_id;
                END IF;
                IF e.legal_entity IS NOT TRUE THEN
                    RAISE EXCEPTION 'entity tax management input requires a legal entity reporting party';
                END IF;
            END IF;

            IF EXISTS (
                SELECT 1
                  FROM entity_tax_ledger_components
                 WHERE management_input_id = OLD.id
            ) THEN
                RAISE EXCEPTION 'management input referenced by a terminal entity tax ledger is immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_v3_guard_entity_tax_management_input_mutation
        BEFORE INSERT OR UPDATE OR DELETE ON entity_tax_management_inputs
        FOR EACH ROW EXECUTE FUNCTION v3_guard_entity_tax_management_input_mutation()
        """
    )


def downgrade() -> None:
    _require_postgresql()

    op.execute(
        "DROP TRIGGER IF EXISTS trg_v3_guard_entity_tax_management_input_mutation ON entity_tax_management_inputs"
    )
    op.execute("DROP FUNCTION IF EXISTS v3_guard_entity_tax_management_input_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v3_guard_entity_tax_ledger_component_immutable ON entity_tax_ledger_components"
    )
    op.execute("DROP FUNCTION IF EXISTS v3_guard_entity_tax_ledger_component_immutable()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v3_guard_entity_tax_ledger_immutable ON entity_tax_ledgers"
    )
    op.execute("DROP FUNCTION IF EXISTS v3_guard_entity_tax_ledger_immutable()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v3_guard_entity_tax_ledger_component_reconciliation ON entity_tax_ledger_components"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v3_guard_entity_tax_ledger_reconciliation ON entity_tax_ledgers"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS v3_guard_entity_tax_ledger_component_reconciliation()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v3_guard_entity_tax_ledger_component_source ON entity_tax_ledger_components"
    )
    op.execute("DROP FUNCTION IF EXISTS v3_guard_entity_tax_ledger_component_source()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v3_guard_entity_tax_ledger_period_state ON entity_tax_ledgers"
    )
    op.execute("DROP FUNCTION IF EXISTS v3_guard_entity_tax_ledger_period_state()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v3_guard_entity_tax_ledger_scope ON entity_tax_ledgers"
    )
    op.execute("DROP FUNCTION IF EXISTS v3_guard_entity_tax_ledger_scope()")

    op.drop_index(
        "ix_entity_tax_ledger_components_input",
        table_name="entity_tax_ledger_components",
    )
    op.drop_index(
        "ix_entity_tax_ledger_components_ledger",
        table_name="entity_tax_ledger_components",
    )
    op.drop_table("entity_tax_ledger_components")
    op.drop_index("ix_entity_tax_ledgers_scope", table_name="entity_tax_ledgers")
    op.drop_table("entity_tax_ledgers")
    op.drop_index(
        "ix_entity_tax_management_inputs_source_document",
        table_name="entity_tax_management_inputs",
    )
    op.drop_index(
        "ix_entity_tax_management_inputs_scope",
        table_name="entity_tax_management_inputs",
    )
    op.drop_table("entity_tax_management_inputs")
