#!/usr/bin/env bash
# Start the playground: one server for the landing page and every exercise.
# Creates the venv / installs deps on first run, then launches uvicorn.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
PID_FILE=".server.pid"
LOG_FILE="server.log"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Already running (pid $(cat "$PID_FILE")) — http://localhost:$PORT"
  exit 0
fi

if [ ! -f .env ] && [ "${MOCK_MODELS:-}" != "true" ]; then
  echo "No .env found. Copy .env.example to .env and set your models + API keys first,"
  echo "or try the UIs without keys: MOCK_MODELS=true ./start.sh"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Creating venv and installing dependencies..."
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv -q
    uv pip install -q -r requirements.txt --python .venv/bin/python
  else
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
  fi
fi

nohup .venv/bin/uvicorn app.main:app --port "$PORT" > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

# Wait until the API answers so "started" actually means usable.
for _ in $(seq 1 40); do
  if curl -s --max-time 1 "http://localhost:$PORT/api/exercises" > /dev/null 2>&1; then
    echo "Started (pid $(cat "$PID_FILE")) — http://localhost:$PORT  (logs: $LOG_FILE)"
    exit 0
  fi
  sleep 0.5
done
echo "Server did not come up — check $LOG_FILE"
exit 1
