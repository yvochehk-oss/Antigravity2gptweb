"""Alembic environment for a PostgreSQL-only service sharing one database."""
from __future__ import annotations
import os, sys
from logging.config import fileConfig
from pathlib import Path
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url
from alembic import context
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
config=context.config
if config.config_file_name: fileConfig(config.config_file_name)
from app import models  # noqa: E402,F401
from app.db import Base  # noqa: E402
url=os.getenv("PROJECT_RAG_DB_URL","").strip() or config.get_main_option("sqlalchemy.url").strip()
if not url: raise RuntimeError("PROJECT_RAG_DB_URL is required for Alembic")
if make_url(url).get_backend_name() not in {"postgresql","postgres"}: raise RuntimeError("Alembic is PostgreSQL-only")
config.set_main_option("sqlalchemy.url",url)
target_metadata=Base.metadata

def run_migrations_offline():
    context.configure(url=url,target_metadata=target_metadata,literal_binds=True,dialect_opts={"paramstyle":"named"},version_table="alembic_version_rag",compare_type=True)
    with context.begin_transaction(): context.run_migrations()

def run_migrations_online():
    connectable=engine_from_config(config.get_section(config.config_ini_section) or {},prefix="sqlalchemy.",poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection,target_metadata=target_metadata,version_table="alembic_version_rag",compare_type=True)
        with context.begin_transaction(): context.run_migrations()
if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
