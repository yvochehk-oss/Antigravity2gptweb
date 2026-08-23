#!/usr/bin/env python3
"""Migrate the Tax schema in shared PostgreSQL without touching SQLite."""
from __future__ import annotations
import argparse, os
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url
ROOT=Path(__file__).resolve().parents[1]; VERSION='alembic_version_tax'
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--existing-shared-db',action='store_true'); args=ap.parse_args()
    url=os.getenv('DATABASE_URL','').strip()
    if not url or make_url(url).get_backend_name() not in {'postgresql','postgres'}: raise SystemExit('DATABASE_URL must point to PostgreSQL')
    cfg=Config(str(ROOT/'alembic.ini')); cfg.set_main_option('script_location',str(ROOT/'alembic')); cfg.set_main_option('sqlalchemy.url',url)
    engine=create_engine(url,future=True,pool_pre_ping=True)
    try:
      tables=set(inspect(engine).get_table_names())
      if args.existing_shared_db and VERSION not in tables:
        required={'projects','entities','invoices','real_costs','progress','budgets','cashflows'}
        missing=sorted(required-tables)
        if missing: raise SystemExit('Refusing to stamp incomplete Tax schema: '+', '.join(missing))
        command.stamp(cfg,'57eaaaffc6eb')
      command.upgrade(cfg,'head')
    finally: engine.dispose()
if __name__=='__main__': main()
