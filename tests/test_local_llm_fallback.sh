#!/usr/bin/env bash
# Contract and optional real-runtime test for the local llama.cpp models and runtimes.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRIMARY_MODEL="$ROOT_DIR/models/local-llm/Ling-3.0-tiny-Q4_K_M.gguf"
FALLBACK_MODEL="$ROOT_DIR/models/local-llm/Qwen3.5-2B-Q4_K_M.gguf"
PRIMARY_SHA="$PRIMARY_MODEL.sha256"
FALLBACK_SHA="$FALLBACK_MODEL.sha256"

bash -n "$ROOT_DIR/start_all.sh" "$ROOT_DIR/stop_all.sh" \
  "$ROOT_DIR/models/local-llm/download_ling3_tiny.sh" \
  "$ROOT_DIR/models/local-llm/download_qwen35_2b.sh" \
  "$ROOT_DIR/models/local-llm/download_windows_runtime.sh"

# Verify at least one model exists and its checksum is valid if provided
MODEL=""
ALIAS=""
if [ -f "$PRIMARY_MODEL" ]; then
  MODEL="$PRIMARY_MODEL"
  ALIAS="ling-3.0-tiny"
  if [ -f "$PRIMARY_SHA" ]; then
    if command -v shasum >/dev/null 2>&1; then
      (cd "$ROOT_DIR" && shasum -a 256 -c "models/local-llm/Ling-3.0-tiny-Q4_K_M.gguf.sha256")
    else
      (cd "$ROOT_DIR" && sha256sum -c "models/local-llm/Ling-3.0-tiny-Q4_K_M.gguf.sha256")
    fi
  fi
elif [ -f "$FALLBACK_MODEL" ]; then
  MODEL="$FALLBACK_MODEL"
  ALIAS="local-qwen3.5-2b"
  if [ -f "$FALLBACK_SHA" ]; then
    if command -v shasum >/dev/null 2>&1; then
      (cd "$ROOT_DIR" && shasum -a 256 -c "models/local-llm/Qwen3.5-2B-Q4_K_M.gguf.sha256")
    else
      (cd "$ROOT_DIR" && sha256sum -c "models/local-llm/Qwen3.5-2B-Q4_K_M.gguf.sha256")
    fi
  fi
else
  echo "No local LLM model found (neither Ling-3.0-tiny nor Qwen3.5-2B)" >&2
  exit 1
fi

grep -q 'LOCAL_LLM_PORT.*8930' "$ROOT_DIR/start_all.sh"
grep -q 'LOCAL_LLM_PORT.*8930' "$ROOT_DIR/.env.example"
grep -q 'LOCAL_LLM_REASONING.*off' "$ROOT_DIR/start_all.sh"
grep -q 'LOCAL_LLM_REASONING.*off' "$ROOT_DIR/.env.example"
grep -q 'LOCAL_LLM_CTX_SIZE.*4096' "$ROOT_DIR/start_all.sh"
grep -q 'LOCAL_LLM_CTX_SIZE.*4096' "$ROOT_DIR/.env.example"
echo "local llama.cpp static contract: PASS"

if [ "${RUN_LOCAL_LLM_INTEGRATION:-0}" != 1 ]; then
  echo "real runtime test skipped (set RUN_LOCAL_LLM_INTEGRATION=1 to run)"
  exit 0
fi

command -v "${LOCAL_LLM_SERVER_BIN:-llama-server}" >/dev/null 2>&1 || {
  echo "llama-server not found; runtime test unavailable" >&2
  exit 2
}
SERVER_BIN="$(command -v "${LOCAL_LLM_SERVER_BIN:-llama-server}")"
PORT="${LOCAL_LLM_TEST_PORT:-8931}"
LOG_FILE="${TMPDIR:-/tmp}/chengdu-local-llm-test.$$.log"
PID=""
cleanup() {
  if [ -n "$PID" ] && kill -0 "$PID" >/dev/null 2>&1; then
    kill -TERM "$PID" >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do
      kill -0 "$PID" >/dev/null 2>&1 || break
      sleep 1
    done
  fi
  rm -f "$LOG_FILE"
}
trap cleanup EXIT

"$SERVER_BIN" --model "$MODEL" --host 127.0.0.1 --port "$PORT" \
  --alias "$ALIAS" --ctx-size 4096 --threads 4 --threads-batch 4 \
  --batch-size 512 --ubatch-size 256 --gpu-layers 0 --reasoning off \
  --parallel 1 --jinja >"$LOG_FILE" 2>&1 &
PID=$!
for _ in $(seq 1 180); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then break; fi
  if ! kill -0 "$PID" >/dev/null 2>&1; then
    cat "$LOG_FILE" >&2
    exit 1
  fi
  sleep 1
done
curl -fsS --max-time 60 "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$ALIAS\",\"messages\":[{\"role\":\"user\",\"content\":\"只回答：本地模型正常\"}],\"temperature\":0,\"max_tokens\":64}" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); c=d["choices"][0]; assert c["message"].get("content"), d; print("local llama.cpp chat: PASS -> "+c["message"]["content"])'
