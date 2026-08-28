"""PostgreSQL-only schema integration checks.

Set TEST_DATABASE_URL to a disposable database whose name contains ``test``.
The fixture skips safely when no PostgreSQL test service is configured.
"""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text


def test_postgresql_shared_master_contract(postgres_test_database_url):
    engine=create_engine(postgres_test_database_url, future=True)
    insp=inspect(engine)
    # This test is intended to run after ``alembic upgrade head`` in CI/local setup.
    required={"projects","entities","external_parties","progress","real_costs","budgets","invoices","cashflows"}
    missing=required-set(insp.get_table_names())
    assert not missing, f"run Tax alembic upgrade head before test; missing={sorted(missing)}"
    project_cols={c["name"] for c in insp.get_columns("projects")}
    entity_cols={c["name"] for c in insp.get_columns("entities")}
    assert {"code","project_code","contract_total","contract_amount","city","location"} <= project_cols
    assert {"code","entity_code","business_role","entity_kind","active","status"} <= entity_cols
    with engine.connect() as conn:
        triggers={r[0] for r in conn.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}
    assert {"trg_sync_project_aliases","trg_sync_entity_aliases"} <= triggers
    engine.dispose()


def test_seeded_external_party_identifiers_keep_full_values(
    seeded_app, postgres_test_database_url
):
    """The shared table accepts the complete canonical external-party values."""
    from app.seed import EXTERNAL_PARTIES

    engine = create_engine(postgres_test_database_url, future=True)
    with engine.connect() as conn:
        lengths = {
            row.column_name: row.character_maximum_length
            for row in conn.execute(
                text(
                    """
                    SELECT column_name, character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'external_parties'
                      AND column_name IN ('code', 'kind')
                    """
                )
            )
        }
        assert lengths == {"code": 30, "kind": 30}

        expected = {code: kind for code, _name, _short_name, kind in EXTERNAL_PARTIES}
        actual = dict(
            conn.execute(
                text("SELECT code, kind FROM external_parties WHERE code LIKE 'EXT-%'")
            ).all()
        )
        assert actual == expected
        assert actual["EXT-CQ-HEAVY-CRANE"] == "construction"
    engine.dispose()
