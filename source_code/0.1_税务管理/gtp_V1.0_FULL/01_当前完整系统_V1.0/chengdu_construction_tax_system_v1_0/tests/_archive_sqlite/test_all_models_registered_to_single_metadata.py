"""Test that Tax system models are all registered to a single shared Base.metadata."""


def _tax_db(seeded_app):
    """Return the session-fixture-bound Tax metadata and engine.

    Keep imports inside the tests.  Importing ``app.db`` at module collection
    time happens before the session ``_isolated_db`` fixture runs and freezes
    a different ``DATABASE_URL`` into the SQLAlchemy engine.  That left the
    rest of the suite using an unseeded database after the E2E namespace
    switch.  ``seeded_app`` establishes the canonical test engine first.
    """
    from app.db import Base, engine

    return Base, engine


def test_tax_models_share_same_base(seeded_app):
    """All Tax models must register to a single shared Base.metadata."""
    Base, _engine = _tax_db(seeded_app)
    # Import models to register them
    from app import models  # noqa: F401

    table_names = set(Base.metadata.tables.keys())

    # Verify all expected Tax tables are present
    expected_tables = {
        "entities", "external_parties", "projects", "contracts", "invoices",
        "cashflows", "fulfillment", "real_costs", "progress", "budgets",
        "cost_accounts", "tax_rules", "tax_ledgers", "entity_bank_accounts",
        "tax_payment_records", "risk_events", "audit_logs", "risk_thresholds",
        "ai_model_endpoints", "ai_review_jobs", "ai_review_results",
        "ai_prompt_templates", "ai_review_batches", "ai_consensus_reports",
        "remediation_tasks", "project_rag_map", "sync_logs", "sync_pending",
        "facts_snapshots", "facts_request_logs", "users",
    }

    missing = expected_tables - table_names
    assert not missing, f"Missing Tax tables: {missing}"


def test_fresh_tax_db_has_all_tables(seeded_app):
    """A fresh database created via Base.metadata.create_all has all expected tables."""
    from pathlib import Path
    import tempfile

    Base, _engine = _tax_db(seeded_app)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "fresh_test.db"
        from sqlalchemy import create_engine

        fresh_engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(fresh_engine)

        # Verify tables exist
        from sqlalchemy import inspect
        inspector = inspect(fresh_engine)
        actual_tables = set(inspector.get_table_names())

        expected_tables = {
            "entities", "external_parties", "projects", "contracts", "invoices",
            "cashflows", "fulfillment", "real_costs", "progress", "budgets",
            "cost_accounts", "tax_rules", "tax_ledgers", "entity_bank_accounts",
            "tax_payment_records", "risk_events", "audit_logs", "risk_thresholds",
            "ai_model_endpoints", "ai_review_jobs", "ai_review_results",
            "ai_prompt_templates", "ai_review_batches", "ai_consensus_reports",
            "remediation_tasks", "project_rag_map", "sync_logs", "sync_pending",
            "facts_snapshots", "facts_request_logs", "users",
        }

        missing = expected_tables - actual_tables
        assert not missing, f"Missing tables after fresh create_all: {missing}"
