#!/bin/bash
set -euo pipefail
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew 未安装。请先安装 Homebrew 后重新运行。"
  exit 1
fi
brew install postgresql@18 pgvector
brew services start postgresql@18
PGBIN="$(brew --prefix postgresql@18)/bin"
export PATH="$PGBIN:$PATH"
if [ -z "${PROJECT_RAG_DB_PASSWORD:-}" ]; then
  echo "请先设置 PROJECT_RAG_DB_PASSWORD；脚本不会生成或保存默认数据库密码。" >&2
  exit 2
fi
# Wait briefly for local server
for i in {1..20}; do pg_isready >/dev/null 2>&1 && break; sleep 1; done
if ! psql postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='projectrag'" | grep -q 1; then
  psql postgres -v db_password="$PROJECT_RAG_DB_PASSWORD" -c "CREATE ROLE projectrag LOGIN PASSWORD :'db_password';"
fi
if ! psql postgres -tAc "SELECT 1 FROM pg_database WHERE datname='projectrag'" | grep -q 1; then
  psql postgres -c "CREATE DATABASE projectrag OWNER projectrag;"
fi
psql projectrag -c "CREATE EXTENSION IF NOT EXISTS vector;"
echo
echo "PostgreSQL + pgvector 已准备好。"
echo "DATABASE_URL 请通过 PROJECT_RAG_DB_URL 环境变量注入（不要把密码写入脚本或仓库）。"
