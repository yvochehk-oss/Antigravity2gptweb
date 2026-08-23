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
# Wait briefly for local server
for i in {1..20}; do pg_isready >/dev/null 2>&1 && break; sleep 1; done
if ! psql postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='projectrag'" | grep -q 1; then
  psql postgres -c "CREATE ROLE projectrag LOGIN PASSWORD 'projectrag';"
fi
if ! psql postgres -tAc "SELECT 1 FROM pg_database WHERE datname='projectrag'" | grep -q 1; then
  psql postgres -c "CREATE DATABASE projectrag OWNER projectrag;"
fi
psql projectrag -c "CREATE EXTENSION IF NOT EXISTS vector;"
echo
echo "PostgreSQL + pgvector 已准备好。"
echo "DATABASE_URL: postgresql+psycopg://projectrag:projectrag@127.0.0.1:5432/projectrag"
echo "本机开发密码 projectrag 仅用于本地V0.2；正式环境请修改。"
