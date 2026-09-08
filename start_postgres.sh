#!/usr/bin/env bash
# 启动本地 PostgreSQL 服务（数据目录默认 data/postgres）。
#
# macOS 对齐 Windows fc7c5ad 的自启动/僵尸锁恢复语义，但采取更保守的
# fail-closed 策略：只有确认 postmaster.pid 中的 PID 已死亡且 pg_ctl 也判定
# 数据目录未运行时，才删除 stale postmaster.pid；绝不删除仍有存活 PID 的锁。
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${POSTGRES_DATA_DIR:-$PROJECT_DIR/data/postgres}"
LOG_FILE="${POSTGRES_LOG_FILE:-$DATA_DIR/server.log}"
PGBIN="${POSTGRES_BIN_DIR:-/opt/homebrew/opt/postgresql@18/bin}"
PGHOST="${POSTGRES_HOST:-127.0.0.1}"
PGPORT="${POSTGRES_PORT:-5432}"
START_LOCK_DIR="$DATA_DIR/.cdjg-postgres-start.lock"
LOCK_OWNER_FILE="$START_LOCK_DIR/pid"
LOCK_ACQUIRED=false

export PATH="$PGBIN:$PATH"

is_numeric_pid() {
  case "${1:-}" in
    ''|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

pid_alive() {
  is_numeric_pid "${1:-}" || return 1
  kill -0 "$1" >/dev/null 2>&1
}

pg_ready() {
  pg_isready -h "$PGHOST" -p "$PGPORT" >/dev/null 2>&1
}

wait_until_ready() {
  local seconds="${1:-30}" i
  for i in $(seq 1 "$seconds"); do
    if pg_ready; then
      return 0
    fi
    sleep 1
  done
  return 1
}

release_start_lock() {
  if [ "$LOCK_ACQUIRED" = true ]; then
    local owner
    owner="$(tr -d '[:space:]' < "$LOCK_OWNER_FILE" 2>/dev/null || :)"
    if [ "$owner" = "$$" ]; then
      rm -rf "$START_LOCK_DIR"
    fi
    LOCK_ACQUIRED=false
  fi
}
trap release_start_lock EXIT
trap 'release_start_lock; exit 130' INT
trap 'release_start_lock; exit 143' TERM

if ! command -v pg_ctl >/dev/null 2>&1 || ! command -v pg_isready >/dev/null 2>&1; then
  echo "错误: 未找到 pg_ctl/pg_isready，请检查 PostgreSQL 18 是否安装在 $PGBIN" >&2
  exit 1
fi

if pg_ready; then
  echo "PostgreSQL 服务已在运行中 ($PGHOST:$PGPORT)"
  exit 0
fi

mkdir -p "$DATA_DIR"

# 原子目录锁避免菜单栏控制台、终端脚本或重复点击同时执行 pg_ctl start。
if mkdir "$START_LOCK_DIR" 2>/dev/null; then
  printf '%s\n' "$$" > "$LOCK_OWNER_FILE"
  LOCK_ACQUIRED=true
else
  lock_owner="$(tr -d '[:space:]' < "$LOCK_OWNER_FILE" 2>/dev/null || :)"
  if pid_alive "$lock_owner"; then
    echo "检测到另一个 PostgreSQL 启动流程正在运行（PID $lock_owner），等待数据库就绪..."
    if wait_until_ready 30; then
      echo "PostgreSQL 已由并发启动流程启动成功 ($PGHOST:$PGPORT)"
      exit 0
    fi
    echo "错误: 并发 PostgreSQL 启动流程仍存活，但数据库 30 秒内未就绪；未执行第二次 pg_ctl start。" >&2
    exit 1
  fi

  echo "发现陈旧的 PostgreSQL 启动互斥锁，正在自愈..."
  rm -rf "$START_LOCK_DIR"
  if ! mkdir "$START_LOCK_DIR" 2>/dev/null; then
    echo "错误: 无法取得 PostgreSQL 启动互斥锁；可能有新的并发启动流程。" >&2
    exit 1
  fi
  printf '%s\n' "$$" > "$LOCK_OWNER_FILE"
  LOCK_ACQUIRED=true
fi

# 取得锁后再次检查，覆盖在竞争窗口中已由其他入口启动成功的情况。
if pg_ready; then
  echo "PostgreSQL 服务已在运行中 ($PGHOST:$PGPORT)"
  exit 0
fi

POSTMASTER_PID_FILE="$DATA_DIR/postmaster.pid"
if [ -f "$POSTMASTER_PID_FILE" ]; then
  postmaster_pid="$(head -n 1 "$POSTMASTER_PID_FILE" 2>/dev/null | tr -d '[:space:]' || :)"

  if pid_alive "$postmaster_pid"; then
    echo "检测到 postmaster.pid 指向存活进程 PID $postmaster_pid，等待 PostgreSQL 就绪..."
    if wait_until_ready 30; then
      echo "PostgreSQL 已就绪 ($PGHOST:$PGPORT)"
      exit 0
    fi
    echo "错误: postmaster.pid 指向存活 PID $postmaster_pid，但 PostgreSQL 未就绪；为防止误删活动锁，已停止自愈。" >&2
    exit 1
  fi

  # pg_ctl 是数据目录状态的第二份独立证据。若它认为服务仍在运行，拒绝删锁。
  if "$PGBIN/pg_ctl" -D "$DATA_DIR" status >/dev/null 2>&1; then
    echo "检测到 pg_ctl 仍认为数据目录正在运行，等待 PostgreSQL 就绪..."
    if wait_until_ready 30; then
      echo "PostgreSQL 已就绪 ($PGHOST:$PGPORT)"
      exit 0
    fi
    echo "错误: pg_ctl 报告 PostgreSQL 正在运行但端口未就绪；未删除 postmaster.pid。" >&2
    exit 1
  fi

  echo "发现 PostgreSQL 僵尸 postmaster.pid（原 PID: ${postmaster_pid:-unknown}），已确认无活动实例，正在清理..."
  rm -f "$POSTMASTER_PID_FILE"
fi

echo "正在启动 PostgreSQL (数据目录: $DATA_DIR)..."
"$PGBIN/pg_ctl" -D "$DATA_DIR" -l "$LOG_FILE" start

if wait_until_ready 30; then
  echo "PostgreSQL 启动成功！"
  echo "PostgreSQL 已就绪；请通过 PROJECT_RAG_DB_URL 注入连接信息。"
  exit 0
fi

echo "错误: PostgreSQL 在 30 秒内未就绪，请检查日志: $LOG_FILE" >&2
exit 1
