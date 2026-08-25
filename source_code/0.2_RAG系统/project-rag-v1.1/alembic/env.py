"""Alembic environment for a PostgreSQL-only service sharing one database."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import make_url

from alembic import context

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
from ai_review import models as _ai_review_models  # noqa: E402,F401
from app import models  # noqa: E402,F401
from app.db import Base  # noqa: E402

url = os.getenv("PROJECT_RAG_DB_URL", "").strip() or config.get_main_option("sqlalchemy.url").strip()
if not url:
    raise RuntimeError("PROJECT_RAG_DB_URL is required for Alembic")
if make_url(url).get_backend_name() not in {"postgresql", "postgres"}:
    raise RuntimeError("Alembic is PostgreSQL-only")
config.set_main_option("sqlalchemy.url", url)
target_metadata = Base.metadata


def _ensure_version_table_capacity(connection) -> None:
    """Create/upgrade the RAG Alembic version table before migrations run.

    Alembic's default version column is ``VARCHAR(32)``.  This repository has
    intentionally descriptive revision identifiers such as
    ``004_postgresql_canonical_analytics`` which exceed that limit.  Keeping
    the version-table bootstrap here means a brand-new database and an older
    database created by Alembic both follow the same safe path, without
    requiring a fake prerequisite revision or an out-of-band manual ALTER.
    The change is metadata-only and is transactional on PostgreSQL.
    """
    connection.execute(
        text('CREATE TABLE IF NOT EXISTS "alembic_version_rag" (version_num VARCHAR(128) NOT NULL PRIMARY KEY)')
    )
    connection.execute(text('ALTER TABLE "alembic_version_rag" ALTER COLUMN version_num TYPE VARCHAR(128)'))


def run_migrations_offline():
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_rag",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        _ensure_version_table_capacity(connection)
        # The bootstrap query above necessarily starts SQLAlchemy's autobegin
        # transaction.  Commit that metadata-only bootstrap before Alembic
        # creates its own migration transaction; otherwise Alembic can treat
        # the connection as externally managed and the whole upgrade is
        # rolled back when the connection closes.
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version_rag",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
