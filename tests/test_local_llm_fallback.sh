#!/usr/bin/env bash
# Contract and optional real-runtime test for the local llama.cpp fallback.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="$ROOT_DIR/models/local-llm/Qwen3.5-2B-Q4_K_M.gguf"
SHA_FILE="$MODEL.sha256"

bash -n "$ROOT_DIR/start_all.sh" "$ROOT_DIR/stop_all.sh" \
  "$ROOT_DIR/models/local-llm/download_qwen35_2b.sh"
test -f "$MODEL" || { echo "missing model: $MODEL" >&2; exit 1; }
test -f "$SHA_FILE" || { echo "missing checksum: $SHA_FILE" >&2; exit 1; }

if command -v shasum >/dev/null 2>&1; then
  (cd "$ROOT_DIR" && shasum -a 256 -c "models/local-llm/Qwen3.5-2B-Q4_K_M.gguf.sha256")
else
  (cd "$ROOT_DIR" && sha256sum -c "models/local-llm/Qwen3.5-2B-Q4_K_M.gguf.sha256")
fi

rg -q 'LOCAL_LLM_PORT.*8930' "$ROOT_DIR/start_all.sh" "$ROOT_DIR/.env.example"
rg -q 'Qwen3\.5-2B-Q4_K_M\.gguf' "$ROOT_DIR/start_all.sh" "$ROOT_DIR/.env.example"
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
  --alias local-qwen3.5-2b --ctx-size 8192 --threads 4 --threads-batch 4 \
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
  -d '{"model":"local-qwen3.5-2b","messages":[{"role":"user","content":"只回答：本地模型正常"}],"temperature":0,"max_tokens":64}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); c=d["choices"][0]; assert c["message"].get("content"), d; print("local llama.cpp chat: PASS -> "+c["message"]["content"])'
