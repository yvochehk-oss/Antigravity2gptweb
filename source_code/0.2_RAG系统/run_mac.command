#!/bin/bash
cd "$(dirname "$0")"
./run.sh &
PID=$!
sleep 3
open "http://${PROJECT_RAG_HOST:-127.0.0.1}:${PROJECT_RAG_PORT:-8800}"
wait $PID
