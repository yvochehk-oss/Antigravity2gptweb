#!/bin/bash
# ProjectRAG V1.1 - macOS Double-click Launcher
cd "$(dirname "$0")"
# Read the same host/port settings that run.sh will use so the browser opens
# the actual configured endpoint. Secret values remain process-injected.
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi
./run.sh &
PID=$!
sleep 3
open "http://${PROJECT_RAG_HOST:-127.0.0.1}:${PROJECT_RAG_PORT:-8922}"
wait $PID
