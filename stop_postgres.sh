#!/bin/bash
# 停止本地 PostgreSQL 服务
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$PROJECT_DIR/data/postgres"
PGBIN="/opt/homebrew/opt/postgresql@18/bin"

export PATH="$PGBIN:$PATH"

if ! command -v pg_ctl >/dev/null 2>&1; then
  echo "错误: 未找到 pg_ctl"
  exit 1
fi

echo "正在停止 PostgreSQL 服务..."
"$PGBIN/pg_ctl" -D "$DATA_DIR" stop -m fast
echo "PostgreSQL 已停止。"
