#!/usr/bin/env bash
# Stop the playground server.
set -euo pipefail
cd "$(dirname "$0")"

PID_FILE=".server.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "Not running (no $PID_FILE)."
  exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  # Give uvicorn a moment to shut down cleanly before reporting.
  for _ in $(seq 1 10); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.3
  done
  echo "Stopped (pid $PID)."
else
  echo "Stale pid file (pid $PID not running); cleaning up."
fi
rm -f "$PID_FILE"
