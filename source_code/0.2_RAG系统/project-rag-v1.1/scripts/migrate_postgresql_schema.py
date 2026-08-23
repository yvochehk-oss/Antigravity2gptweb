#!/usr/bin/env python3
"""Migrate RAG after Tax in the shared PostgreSQL database."""
from __future__ import annotations
import argparse, os
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url
ROOT=Path(__file__).resolve().parents[1]; VERSION='alembic_version_rag'
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--existing-shared-db',action='store_true'); args=ap.parse_args()
 url=os.getenv('PROJECT_RAG_DB_URL','').strip()
 if not url or make_url(url).get_backend_name() not in {'postgresql','postgres'}: raise SystemExit('PROJECT_RAG_DB_URL must point to PostgreSQL')
 cfg=Config(str(ROOT/'alembic.ini')); cfg.set_main_option('script_location',str(ROOT/'alembic')); cfg.set_main_option('sqlalchemy.url',url)
 engine=create_engine(url,future=True,pool_pre_ping=True)
 try:
  tables=set(inspect(engine).get_table_names())
  if args.existing_shared_db and VERSION not in tables:
   required={'projects','entities','external_parties','documents','chunks','progress','real_costs','budgets','invoices','cashflows'}
   missing=sorted(required-tables)
   if missing: raise SystemExit('Refusing to stamp incomplete shared schema: '+', '.join(missing))
   command.stamp(cfg,'003_analytics_project_full')
  command.upgrade(cfg,'head')
 finally: engine.dispose()
if __name__=='__main__': main()
