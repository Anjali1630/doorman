#!/usr/bin/env bash
# Runs demo_site, backend, and frontend together for local development.
# Ctrl-C stops all three.
set -euo pipefail
cd "$(dirname "$0")/.."

cleanup() {
  echo
  echo "Stopping..."
  kill "${DEMO_PID:-}" "${BACKEND_PID:-}" "${FRONTEND_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT

echo "== Starting demo site on :8001 =="
python3 -m uvicorn demo_site.main:app --port 8001 --host 127.0.0.1 &
DEMO_PID=$!

echo "== Starting backend on :8000 =="
(cd backend && PYTHONPATH=. python3 -m uvicorn app.main:app --port 8000 --host 127.0.0.1) &
BACKEND_PID=$!

sleep 2

echo "== Starting frontend on :5173 =="
(cd frontend && npm run dev -- --port 5173) &
FRONTEND_PID=$!

echo
echo "Demo site:  http://127.0.0.1:8001  (demo / demo1234)"
echo "Backend:    http://127.0.0.1:8000  (docs at /docs)"
echo "Frontend:   http://127.0.0.1:5173"
echo
echo "Press Ctrl-C to stop all three."
wait
