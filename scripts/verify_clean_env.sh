#!/usr/bin/env bash
# Simulates a "clean machine" setup end to end, without Docker: a brand-new
# Python virtualenv (no reused site-packages), a fresh `pip install -r
# backend/requirements.txt`, and the full pytest suite (including live
# Playwright integration tests against a freshly-started demo site) - all
# run from scratch, not reusing anything from a previous run.
#
# HONEST CAVEAT ABOUT THE BROWSER BINARY ITSELF: `playwright install
# chromium` normally downloads the browser from Microsoft's CDN
# (cdn.playwright.dev / playwright.download.prss.microsoft.com) the first
# time it's needed on a machine. This script deliberately does NOT force a
# from-scratch re-download (it lets Playwright reuse whatever is already
# cached at the default ~/.cache/ms-playwright), because the sandbox this
# project was built in restricts network egress to a fixed domain
# allowlist that does not include Playwright's download CDN - a real
# developer machine or CI runner would not have this restriction, and a
# genuinely first-time `playwright install chromium` there will download
# the browser (a few hundred MB) rather than finding it pre-cached. If you
# want to verify that download step specifically, run this script on a
# machine with normal internet access and an empty
# ~/.cache/ms-playwright directory.
#
# Exit code is non-zero if ANY step fails, including the test run itself.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT_DIR="$(pwd)"
VENV_DIR="$(mktemp -d)/clean_venv"

echo "== Creating a brand-new virtualenv at $VENV_DIR (no reused site-packages) =="
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip --quiet

echo "== Installing backend/requirements.txt fresh into the new venv =="
pip install -r backend/requirements.txt --quiet

echo "== Verifying Playwright's Chromium is installed and launches (see caveat above re: fresh downloads) =="
python -m playwright install chromium
python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    b.close()
print('Chromium launches successfully from the fresh venv.')
"

echo "== Starting demo site =="
python -m uvicorn demo_site.main:app --port 8001 --host 127.0.0.1 &
DEMO_PID=$!
cleanup() { kill "$DEMO_PID" 2>/dev/null || true; deactivate 2>/dev/null || true; }
trap cleanup EXIT

for i in $(seq 1 20); do
  if curl -s -o /dev/null -m 2 http://127.0.0.1:8001/api/status; then break; fi
  sleep 1
done

echo "== Running the full backend test suite in the clean venv =="
cd backend
set +e
PYTHONPATH=. python -m pytest tests/ -v --timeout=120
RESULT=$?
set -e
cd "$ROOT_DIR"

echo
if [ "$RESULT" -eq 0 ]; then
  echo "CLEAN-ENVIRONMENT VERIFICATION: PASSED"
else
  echo "CLEAN-ENVIRONMENT VERIFICATION: FAILED"
fi
exit "$RESULT"
