#!/usr/bin/env bash
# Runs the backend test suite. Starts demo_site automatically for the
# integration tests if it isn't already running.
set -euo pipefail
cd "$(dirname "$0")/.."

STARTED_DEMO=0
if ! curl -s -o /dev/null -m 1 http://127.0.0.1:8001/api/status; then
  echo "== Starting demo site for integration tests =="
  python3 -m uvicorn demo_site.main:app --port 8001 --host 127.0.0.1 &
  DEMO_PID=$!
  STARTED_DEMO=1
  sleep 2
fi

cleanup() {
  if [ "$STARTED_DEMO" = "1" ]; then
    kill "$DEMO_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

cd backend
PYTHONPATH=. python3 -m pytest tests/ -v --timeout=120
