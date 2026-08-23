#!/bin/bash
cd "$(dirname "$0")"
./run_demo.sh &
sleep 4
open http://127.0.0.1:8765
wait
