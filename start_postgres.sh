#!/bin/bash
# 启动本地 PostgreSQL 服务 (数据目录: data/postgres)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$PROJECT_DIR/data/postgres"
LOG_FILE="$DATA_DIR/server.log"
PGBIN="/opt/homebrew/opt/postgresql@18/bin"

export PATH="$PGBIN:$PATH"

if ! command -v pg_ctl >/dev/null 2>&1; then
  echo "错误: 未找到 pg_ctl，请检查 PostgreSQL 18 是否安装在 /opt/homebrew/opt/postgresql@18"
  exit 1
fi

if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
  echo "PostgreSQL 服务已在运行中 (127.0.0.1:5432)"
  exit 0
fi

echo "正在启动 PostgreSQL (数据目录: $DATA_DIR)..."
"$PGBIN/pg_ctl" -D "$DATA_DIR" -l "$LOG_FILE" start

# 等待启动就绪
for i in {1..15}; do
  if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    echo "PostgreSQL 启动成功！"
    echo "PostgreSQL 已就绪；请通过 PROJECT_RAG_DB_URL 注入连接信息。"
    exit 0
  fi
  sleep 1
done

echo "启动可能遇到问题，请检查日志: $LOG_FILE"
exit 1
