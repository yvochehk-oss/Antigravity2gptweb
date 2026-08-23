#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
./run.sh &
PID=$!
sleep 3
open "http://${PROJECT_RAG_HOST:-127.0.0.1}:${PROJECT_RAG_PORT:-8922}"
wait $PID
