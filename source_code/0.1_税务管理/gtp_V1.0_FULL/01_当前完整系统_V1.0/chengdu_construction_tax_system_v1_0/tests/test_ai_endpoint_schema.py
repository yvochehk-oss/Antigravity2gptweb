"""PostgreSQL contract tests for the AI endpoint routing pool."""
from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command

ROOT = Path(__file__).resolve().parents[1]


def test_ai_endpoint_routing_schema_and_legacy_secret_compatibility(
    seeded_app, postgres_test_database_url
):
    engine = create_engine(postgres_test_database_url, future=True)
    try:
        with engine.connect() as connection:
            columns = {
                row[0]: row
                for row in connection.execute(
                    text(
                        "SELECT column_name, data_type, character_maximum_length, "
                        "is_nullable, column_default "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'ai_model_endpoints'"
                    )
                )
            }
            assert columns["api_key_env"][3] == "NO"
            assert columns["credential_ref"][1] == "character varying"
            assert columns["credential_ref"][2] == 64
            assert columns["credential_ref"][3] == "YES"
            assert columns["priority"][1] == "integer"
            assert columns["priority"][3] == "NO"
            assert "100" in (columns["priority"][4] or "")
            assert columns["routing_group"][1] == "character varying"
            assert columns["routing_group"][2] == 40
            assert columns["routing_group"][3] == "NO"
            assert "default" in (columns["routing_group"][4] or "")

            constraints = {
                row[0]: row[1]
                for row in connection.execute(
                    text(
                        "SELECT conname, pg_get_constraintdef(oid) "
                        "FROM pg_constraint "
                        "WHERE conrelid = 'public.ai_model_endpoints'::regclass"
                    )
                )
            }
            assert "uq_ai_model_endpoints_credential_ref" in constraints
            assert "ck_ai_model_endpoints_priority_nonnegative" in constraints
            assert "ck_ai_model_endpoints_routing_group_format" in constraints

            connection.execute(
                text(
                    "INSERT INTO ai_model_endpoints "
                    "(name, adapter, base_url, chat_path, model, api_key_env, enabled, "
                    "timeout_seconds, note, credential_ref, priority, routing_group) "
                    "VALUES ('schema-contract-a', 'openai_compatible', '', '', '', "
                    ":legacy_env, true, 10, '', :credential, 10, 'primary')"
                ),
                {"legacy_env": "LEGACY_API_KEY_ENV", "credential": "cred-schema-a"},
            )
            connection.commit()

            row = connection.execute(
                text(
                    "SELECT api_key_env, credential_ref, priority, routing_group "
                    "FROM ai_model_endpoints WHERE name = 'schema-contract-a'"
                )
            ).one()
            assert tuple(row) == ("LEGACY_API_KEY_ENV", "cred-schema-a", 10, "primary")

        with pytest.raises(
            IntegrityError, match="ck_ai_model_endpoints_priority_nonnegative"
        ), engine.begin() as invalid_connection:
            invalid_connection.execute(
                text(
                    "INSERT INTO ai_model_endpoints "
                    "(name, adapter, base_url, chat_path, model, api_key_env, enabled, "
                    "timeout_seconds, note, priority, routing_group) "
                    "VALUES ('schema-contract-b', 'mock', '', '', '', '', true, 10, '', -1, 'default')"
                )
            )

        with pytest.raises(
            IntegrityError, match="ck_ai_model_endpoints_routing_group_format"
        ), engine.begin() as invalid_connection:
            invalid_connection.execute(
                text(
                    "INSERT INTO ai_model_endpoints "
                    "(name, adapter, base_url, chat_path, model, api_key_env, enabled, "
                    "timeout_seconds, note, priority, routing_group) "
                    "VALUES ('schema-contract-c', 'mock', '', '', '', '', true, 10, '', 100, 'Bad Group')"
                )
            )

        with pytest.raises(
            IntegrityError, match="uq_ai_model_endpoints_credential_ref"
        ), engine.begin() as invalid_connection:
            invalid_connection.execute(
                text(
                    "INSERT INTO ai_model_endpoints "
                    "(name, adapter, base_url, chat_path, model, api_key_env, enabled, "
                    "timeout_seconds, note, credential_ref, priority, routing_group) "
                    "VALUES ('schema-contract-d', 'mock', '', '', '', '', true, 10, '', "
                    "'cred-schema-a', 100, 'default')"
                )
            )
    finally:
        with engine.begin() as cleanup:
            cleanup.execute(
                text(
                    "DELETE FROM ai_model_endpoints "
                    "WHERE name IN ('schema-contract-a', 'schema-contract-b', "
                    "'schema-contract-c', 'schema-contract-d')"
                )
            )
        engine.dispose()


def test_ai_endpoint_routing_order_is_priority_then_id(seeded_app, postgres_test_database_url):
    engine = create_engine(postgres_test_database_url, future=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ai_model_endpoints "
                    "(name, adapter, base_url, chat_path, model, api_key_env, enabled, "
                    "timeout_seconds, note, priority, routing_group) "
                    "VALUES ('order-high-id', 'mock', '', '', '', '', true, 10, '', 20, 'default'), "
                    "('order-low-priority', 'mock', '', '', '', '', true, 10, '', 5, 'default')"
                )
            )
            rows = connection.execute(
                text(
                    "SELECT name, priority FROM ai_model_endpoints "
                    "WHERE name LIKE 'order-%' ORDER BY priority ASC, id ASC"
                )
            ).all()
        assert [row[0] for row in rows] == ["order-low-priority", "order-high-id"]
    finally:
        with engine.begin() as cleanup:
            cleanup.execute(
                text(
                    "DELETE FROM ai_model_endpoints "
                    "WHERE name IN ('order-high-id', 'order-low-priority')"
                )
            )
        engine.dispose()


def test_ai_endpoint_migration_round_trip_preserves_legacy_endpoint(
    seeded_app, postgres_test_database_url
):
    """The new revision can be downgraded and reapplied without losing legacy data."""
    engine = create_engine(postgres_test_database_url, future=True)
    try:
        with engine.connect() as connection:
            before = connection.execute(
                text(
                    "SELECT name, api_key_env FROM ai_model_endpoints "
                    "WHERE name = 'DeepSeek·V4-Flash智能体审查器'"
                )
            ).one()
    finally:
        engine.dispose()

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", postgres_test_database_url)
    command.downgrade(config, "62_ai_review_batch_status")

    engine = create_engine(postgres_test_database_url, future=True)
    try:
        with engine.connect() as connection:
            old_columns = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'ai_model_endpoints'"
                    )
                )
            }
            assert {"credential_ref", "priority", "routing_group"}.isdisjoint(old_columns)
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(postgres_test_database_url, future=True)
    try:
        with engine.connect() as connection:
            after = connection.execute(
                text(
                    "SELECT name, api_key_env, priority, routing_group "
                    "FROM ai_model_endpoints "
                    "WHERE name = 'DeepSeek·V4-Flash智能体审查器'"
                )
            ).one()
            assert tuple(after) == (before[0], before[1], 100, "default")
            assert connection.execute(
                text("SELECT version_num FROM alembic_version_tax")
            ).scalar_one() == "63_ai_endpoint_routing_pool"
    finally:
        engine.dispose()
