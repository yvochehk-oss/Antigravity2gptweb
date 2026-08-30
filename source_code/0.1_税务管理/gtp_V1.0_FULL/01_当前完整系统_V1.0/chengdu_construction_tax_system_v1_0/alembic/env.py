"""Alembic environment for the PostgreSQL-only Tax service.

The service uses long descriptive revision IDs. Alembic historically creates a
VARCHAR(32) version column by default, so the environment owns the technical
version-table capacity bootstrap. This is migration infrastructure only: applied
business migrations must never be rewritten to widen ``alembic_version_tax``.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

# Import metadata in dependency order: legacy -> Party -> Fact -> tax -> contract -> project tax.
from app import models  # noqa: E402,F401
from app import v3_party_models  # noqa: E402,F401
from app import v3_fact_models  # noqa: E402,F401
from app import v3_tax_models  # noqa: E402,F401
from app import v3_contract_models  # noqa: E402,F401
from app import v3_project_tax_models  # noqa: E402,F401
from app.db import Base  # noqa: E402

url = os.getenv("DATABASE_URL", "").strip() or config.get_main_option("sqlalchemy.url").strip()
if not url:
    raise RuntimeError("DATABASE_URL is required for Alembic")
if make_url(url).get_backend_name() not in {"postgresql", "postgres"}:
    raise RuntimeError("Alembic is PostgreSQL-only")
if url.startswith("postgresql://"):
    url = "postgresql+psycopg://" + url[len("postgresql://") :]
elif url.startswith("postgres://"):
    url = "postgresql+psycopg://" + url[len("postgres://") :]
config.set_main_option("sqlalchemy.url", url)

target_metadata = Base.metadata
VERSION_TABLE = "alembic_version_tax"
VERSION_COLUMN_LENGTH = 64


def _ensure_version_column_capacity(connection) -> None:
    """Widen only an existing undersized Alembic version column."""
    length = connection.execute(
        text(
            """
            SELECT character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name
              AND column_name = 'version_num'
            """
        ),
        {"table_name": VERSION_TABLE},
    ).scalar_one_or_none()
    if length is not None and int(length) < VERSION_COLUMN_LENGTH:
        connection.execute(
            text(
                "ALTER TABLE IF EXISTS alembic_version_tax "
                "ALTER COLUMN version_num TYPE VARCHAR(64)"
            )
        )
        connection.commit()


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
        version_table_col_length=64,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _ensure_version_column_capacity(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
            version_table_col_length=64,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
