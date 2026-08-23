#!/bin/bash
# V0.2 一键启动（Mac/Linux 通用）。优先用 /tmp/v02venv，否则现场建 .venv。
set -e
cd "$(dirname "$0")"

# 选 python：优先 /tmp/v02venv，再退回系统 python3
if [ -x /tmp/v02venv/bin/python3.11 ]; then
  PY=/tmp/v02venv/bin/python3.11
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "需要 python3 或 python"; exit 1
fi

# 兜底：如果选了系统 python 且没有 .venv，建一个
if [ "$PY" != "/tmp/v02venv/bin/python3.11" ] && [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi

# 激活 venv（/tmp/v02venv 不用激活，site-packages 已就绪）
if [ "$PY" != "/tmp/v02venv/bin/python3.11" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PIP="pip"
else
  PIP="$PY -m pip"
fi
$PIP -q install -r requirements.txt

export DATABASE_URL=${DATABASE_URL:-sqlite:///./data/demo.db}
export LOG_LEVEL=${LOG_LEVEL:-INFO}

$PY -m app.seed
$PY -m uvicorn app.main:app --host 127.0.0.1 --port 8765
