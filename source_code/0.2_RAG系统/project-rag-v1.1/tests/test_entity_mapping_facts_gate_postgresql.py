"""Disposable PostgreSQL checks for the project/entity authenticity gate."""

from __future__ import annotations

from sqlalchemy import create_engine, text

from facts_provider.facts_provider import FactsProvider


def test_missing_invalid_and_valid_entity_mapping_gate_facts(
    postgres_test_database_url: str,
):
    """Keep project rows visible, but trust Facts only for a real master row."""

    engine = create_engine(postgres_test_database_url, future=True)
    project_code = "ENTITY-GATE-20260823"
    try:
        with engine.begin() as connection:
            # Use a test-owned row so this test remains valid even when the
            # executive integration fixture has already cleared the copied
            # project table.  The test URL is explicitly disposable.
            connection.execute(
                text(
                    "DELETE FROM progress WHERE project_id IN "
                    "(SELECT id FROM projects WHERE project_code = :project_code)"
                ),
                {"project_code": project_code},
            )
            connection.execute(
                text(
                    "DELETE FROM real_costs WHERE project_id IN "
                    "(SELECT id FROM projects WHERE project_code = :project_code)"
                ),
                {"project_code": project_code},
            )
            connection.execute(
                text(
                    "DELETE FROM projects WHERE project_code = :project_code"
                ),
                {"project_code": project_code},
            )
            project_id = connection.execute(
                text(
                    "INSERT INTO projects "
                    "(code, name, city, contract_total, tax_method, project_code, "
                    "contract_amount, location, entity_code, status) "
                    "VALUES (:code, :name, :city, 100, :tax_method, :project_code, "
                    "100, :location, NULL, 'ACTIVE') RETURNING id"
                ),
                {
                    "code": project_code,
                    "name": "Entity gate disposable project",
                    "city": "成都",
                    "tax_method": "standard",
                    "project_code": project_code,
                    "location": "成都",
                },
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO progress "
                    "(project_id, period, output_value, settlement, recognized_revenue, collection) "
                    "VALUES (:project_id, '2026-08', 100, 100, 100, 20)"
                ),
                {"project_id": project_id},
            )
            connection.execute(
                text(
                    "INSERT INTO real_costs "
                    "(project_id, entity_code, counterparty_code, category, subcategory, "
                    "period, amount, external_cash, note) "
                    "VALUES (:project_id, 'A01', '', 'direct', 'direct', '2026-08', 60, false, '')"
                ),
                {"project_id": project_id},
            )

            def set_code(value: str | None) -> dict:
                connection.execute(
                    text(
                        "UPDATE projects SET entity_code = :entity_code "
                        "WHERE project_code = :project_code"
                    ),
                    {"entity_code": value, "project_code": project_code},
                )
                row = connection.execute(
                    text(
                        "SELECT entity_mapping_status, entity_mapping_reason, "
                        "entity_mapping_valid, facts_available "
                        "FROM analytics_project_full "
                        "WHERE project_code = :project_code"
                    ),
                    {"project_code": project_code},
                ).mappings().one()
                return dict(row)

            missing = set_code(None)
            assert missing["entity_mapping_status"] == "MISSING"
            assert missing["entity_mapping_valid"] is False
            assert missing["facts_available"] is False

            invalid = set_code("A")
            assert invalid["entity_mapping_status"] == "INVALID"
            assert invalid["entity_mapping_valid"] is False
            assert invalid["facts_available"] is False

            valid = set_code("A01")
            assert valid["entity_mapping_status"] == "VALID"
            assert valid["entity_mapping_valid"] is True
            assert valid["facts_available"] is True

            provider = FactsProvider(connection)
            available = provider.get_facts(project_code, require_fresh=True)
            assert available.status == "AVAILABLE"
            assert available.facts_available is True
            assert available.entity_code == "A01"

            set_code(None)
            degraded = provider.get_facts(project_code, require_fresh=True)
            assert degraded.status == "DEGRADED"
            assert degraded.facts_available is False
            assert degraded.metrics == {}
            assert "entity mapping gap" in (degraded.reason or "")

            # This assertion proves that the source row stayed visible even
            # while its mapping is invalid.
            assert connection.execute(
                text(
                    "SELECT 1 FROM analytics_project_full "
                    "WHERE project_code = :project_code"
                ),
                {"project_code": project_code},
            ).scalar_one() == 1

            connection.execute(
                text(
                    "DELETE FROM progress WHERE project_id = :project_id"
                ),
                {"project_id": project_id},
            )
            connection.execute(
                text("DELETE FROM real_costs WHERE project_id = :project_id"),
                {"project_id": project_id},
            )
            connection.execute(
                text("DELETE FROM projects WHERE id = :project_id"),
                {"project_id": project_id},
            )
    finally:
        engine.dispose()
