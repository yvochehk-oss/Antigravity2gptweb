"""Integration assertions for a disposable shared PostgreSQL test database."""
from sqlalchemy import create_engine, inspect, text

def test_shared_postgresql_schema(postgres_test_database_url):
    engine=create_engine(postgres_test_database_url, future=True)
    insp=inspect(engine)
    required={"projects","entities","external_parties","documents","chunks","facts_snapshots"}
    missing=required-set(insp.get_table_names())
    assert not missing, f"apply Tax migrations first, then RAG migrations; missing={sorted(missing)}"
    views=set(insp.get_view_names())
    assert {"analytics_project_summary","analytics_project_profit","analytics_cost","analytics_eac","analytics_cashflow","analytics_tax","analytics_project_full"} <= views
    with engine.connect() as conn:
        assert conn.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar_one_or_none()
    engine.dispose()
