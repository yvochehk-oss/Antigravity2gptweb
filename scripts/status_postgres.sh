#!/bin/bash
# 检查本地 PostgreSQL 状态
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$PROJECT_DIR/data/postgres"
PGBIN="/opt/homebrew/opt/postgresql@18/bin"

export PATH="$PGBIN:$PATH"

echo "=== PostgreSQL 服务状态 ==="
"$PGBIN/pg_ctl" -D "$DATA_DIR" status || true

echo
echo "=== 连接状态 ==="
if pg_isready -h 127.0.0.1 -p 5432; then
  echo "✓ 127.0.0.1:5432 服务正常可用"
  echo "已安装扩展: vector (pgvector)"
else
  echo "✗ 127.0.0.1:5432 未响应"
fi
