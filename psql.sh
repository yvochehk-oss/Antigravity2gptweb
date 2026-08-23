#!/bin/bash
# 进入 PostgreSQL 交互式命令行控制台
PGBIN="/opt/homebrew/opt/postgresql@18/bin"
export PATH="$PGBIN:$PATH"

DB_NAME="${1:-projectrag}"
USER_NAME="${2:-projectrag}"

if [ -z "${PROJECT_RAG_DB_PASSWORD:-}" ]; then
  echo "请先通过环境变量 PROJECT_RAG_DB_PASSWORD 注入数据库密码。" >&2
  exit 2
fi
echo "正在连接到本地 PostgreSQL 数据库: $DB_NAME (用户: $USER_NAME)..."
PGPASSWORD="$PROJECT_RAG_DB_PASSWORD" psql -h 127.0.0.1 -p 5432 -U "$USER_NAME" -d "$DB_NAME"
