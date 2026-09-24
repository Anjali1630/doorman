#!/usr/bin/env bash
# One-time setup: Python deps, Playwright browser, Node deps, .env file.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Backend: installing Python dependencies =="
pip install -r backend/requirements.txt

echo "== Installing Playwright's Chromium browser =="
python3 -m playwright install chromium

echo "== Frontend: installing Node dependencies =="
(cd frontend && npm install)

if [ ! -f .env ]; then
  echo "== Creating .env from .env.example =="
  cp .env.example .env
else
  echo "== .env already exists, leaving it alone =="
fi

echo
echo "Setup complete. Next: ./scripts/run_dev.sh"
