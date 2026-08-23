"""PostgreSQL-only pytest safety rails for ProjectRAG."""
from __future__ import annotations
import os,sys
from pathlib import Path
import pytest
from sqlalchemy.engine import make_url
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
TEST_DATABASE_URL=os.getenv('TEST_DATABASE_URL','').strip()
os.environ['PROJECT_RAG_DB_URL']=TEST_DATABASE_URL or 'postgresql+psycopg://invalid:invalid@127.0.0.1:1/projectrag_invalid_test'
os.environ.setdefault('PROJECT_RAG_DATA_DIR',str(ROOT/'.pytest-data')); os.environ.setdefault('PROJECT_RAG_IMPORT_ROOT',str(ROOT/'.pytest-data/imports'))
os.environ.setdefault('PROJECT_RAG_EMBEDDING_BACKEND','hash_v1'); os.environ.setdefault('PROJECT_RAG_RERANKER_BACKEND','off'); os.environ.setdefault('PROJECT_RAG_AUTO_START_WORKER','0'); os.environ.setdefault('APP_ENV','test'); os.environ.setdefault('RAG_SHARED_API_KEY','rag-test-secret-at-least-32-characters')
def require_test_database():
 if not TEST_DATABASE_URL: pytest.skip('PostgreSQL integration test requires TEST_DATABASE_URL')
 u=make_url(TEST_DATABASE_URL)
 if u.get_backend_name() not in {'postgresql','postgres'} or 'test' not in (u.database or '').lower(): pytest.fail("TEST_DATABASE_URL must be disposable PostgreSQL and database name must contain 'test'")
 return TEST_DATABASE_URL
@pytest.fixture(scope='session')
def postgres_test_database_url(): return require_test_database()
