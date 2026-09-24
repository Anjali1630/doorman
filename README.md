# doorman

An intelligent browser automation agent that takes a natural-language task ("Find all unpaid
invoices above ₹10,000") and autonomously executes it - calling a real API directly when one
exists, and falling back to genuine Playwright browser automation when it doesn't. Every
action is validated against the real page before it runs, so the agent can't click or type
into something that isn't actually there.

Built for **zero-cost operation**: it runs entirely on a free OpenRouter model key, or with
no LLM key at all (deterministic fallback mode) - no OpenAI/Anthropic/Gemini paid API is
ever required for the core demo.

## Table of contents

- [Problem statement](#problem-statement)
- [Architecture](#architecture)
- [I have no API key - what do I do?](#i-have-no-api-key---what-do-i-do)
- [Installation](#installation)
- [Running the demo](#running-the-demo)
- [Environment variables](#environment-variables)
- [Running tests](#running-tests)
- [Evaluation](#evaluation)
- [Docker](#docker)
- [Known limitations](#known-limitations)
- [Future improvements](#future-improvements)

## Problem statement

Most "AI browser agents" either (a) always drive a real browser, even when a much faster,
cheaper, more reliable API exists for the same task, or (b) trust the LLM's plan blindly,
clicking on elements it *thinks* exist rather than elements that actually do. This project
addresses both: an **API-first router** that prefers a real REST call whenever one is
registered and available, with Playwright as the fallback; and a **pre-execution validator**
that checks every planned element-targeting action against a live DOM snapshot before it's
allowed to run.

## Architecture

Full detail in [`docs/architecture.md`](docs/architecture.md),
[`docs/planning.md`](docs/planning.md), [`docs/execution.md`](docs/execution.md),
[`docs/guardrails.md`](docs/guardrails.md), and [`docs/evaluation.md`](docs/evaluation.md).
Short version:

```
backend/      FastAPI app: planner, API router, Playwright tool registry, validator,
              executor, agent state machine, evaluator, SQLite persistence
demo_site/    A small real "Business Portal" (login/invoices/customers) + a JSON
              Invoice API - the automation target for both API-first and browser paths
frontend/     React + TypeScript + Vite + Tailwind control center UI
```

### API-first + browser fallback

For 4 of the 5 demo tasks, the agent checks a **live** `/api/status` endpoint on the demo
site before every run. If the Invoice API is up, it's called directly. If it's down (you can
toggle it off from the demo site's Settings page to see this happen), the agent transparently
re-plans to Playwright - logged in the execution trace, and covered by an automated test.

## I have no API key - what do I do?

**Nothing is required.** Leave `OPENROUTER_API_KEY` blank in `.env` and the app runs in
**Local Fallback** mode: a deterministic keyword matcher handles task understanding for the
5 demo tasks (and close variants) with no LLM call at all. The UI's "LLM Mode" badge will
show "Local Fallback" and the `/api/health` endpoint reports
`"llm_mode": "deterministic_fallback"`.

If you *do* want the OpenRouter-backed path (optional, still free):

1. Go to <https://openrouter.ai> and sign up (free, no card required for the free model tier).
2. Go to **Keys** in your OpenRouter dashboard → **Create Key**. Copy it.
3. In this project's root, copy `.env.example` to `.env` (if you haven't already) and set:
   ```
   OPENROUTER_API_KEY=sk-or-v1-...your key...
   OPENROUTER_MODEL=openrouter/free
   ```
4. Restart the backend. `/api/health` should now report `"llm_mode": "openrouter"` and the
   UI badge should show "OpenRouter".
5. Run the agent as normal - task understanding now goes through the real model. If the
   free tier rate-limits or errors, the app detects this and **transparently falls back to
   the deterministic matcher** for that request rather than crashing; this is visible in the
   execution trace ("LLM task understanding failed (...); falling back to deterministic
   matcher").

No paid OpenAI/Anthropic/Gemini key is used anywhere in this project.

## Installation

Requires Python 3.11+, Node.js 18+, and internet access for the one-time Playwright browser
download.

```bash
git clone <this repo>
cd BrowserAutomationAgent
./scripts/setup.sh
```

This installs backend Python deps, the Playwright Chromium browser, frontend Node deps, and
creates `.env` from `.env.example` if it doesn't already exist. (You can also do each step
manually - see below.)

<details>
<summary>Manual setup (if you'd rather not use the script)</summary>

```bash
# Backend
pip install -r backend/requirements.txt
python3 -m playwright install chromium

# Frontend
cd frontend && npm install && cd ..

# Environment
cp .env.example .env
```
</details>

## Running the demo

```bash
./scripts/run_dev.sh
```

This starts all three services and prints their URLs:

| Service | URL |
|---|---|
| Demo site (Business Portal) | http://127.0.0.1:8001 |
| Backend API | http://127.0.0.1:8000 |
| Backend Swagger/OpenAPI docs | http://127.0.0.1:8000/docs |
| Frontend control center | http://127.0.0.1:5173 |

**Demo credentials** (for the demo site, if you want to log in manually): `demo` / `demo1234`

### Exact demo steps

1. Open http://127.0.0.1:5173 (Control Center).
2. Click one of the 5 sample task buttons, or type your own close variant, e.g.
   *"Find the latest invoice and tell me its invoice number and amount."*
3. Watch the **Agent Reasoning** panel (what it understood and decided), the **API-First
   Routing** diagram (which path lit up - teal for API, amber for browser), and the
   **Execution Trace** (every validated step, in order) update with the real result.
4. To see the *fallback* happen live: open http://127.0.0.1:8001/settings, log in with
   `demo`/`demo1234`, click **Disable API**, then go back and run *"Open the latest unpaid
   invoice and download it."* again - the routing diagram will show the browser path
   activating and the trace will show a `REPLANNING` step explaining why.
5. Visit **Browser Inspector** to see the real DOM snapshot the validator used.
6. Visit **Executions** to browse every past run's full trace.
7. Visit **Integrations** and click **Test Connection** on any of the three.
8. Visit **Evaluation** and click **Run Evaluation Suite** to see real, freshly-computed
   success/false-action/validation-rejection metrics.
9. To see the agent discover data it has never seen before: open
   http://127.0.0.1:8001/invoices, click **+ Add New Invoice**, and create one with a date
   later than the existing rows (e.g. tomorrow) and status "Unpaid". Go back to the Control
   Center and run *"Find the latest invoice and tell me its invoice number and amount."* -
   the result will be the invoice you just created, not one of the 6 seed invoices. Try it
   again with the Invoice API disabled (step 4) to see the same discovery happen purely
   through Playwright reading the live invoices table.
10. To see payment tracking: click **Record a payment of ₹500 for invoice INV-1005** (or
    type your own, e.g. *"Mark the remaining amount of INV-1002 as paid"*). Then open
    http://127.0.0.1:8001/invoices/inv_1005 to see the same payment reflected in the Paid/
    Remaining tiles and the payment history table. Try *"Show me all partially paid
    invoices"* to see it picked up by a list query too - and try it again with the Invoice
    API disabled to see the same payment get recorded by Playwright filling in and
    submitting the real "Record Payment" form.

## Payment tracking

Every invoice now has `total_amount`, `paid_amount`, and `remaining_amount`
(`= total_amount - paid_amount`), plus a `status` that's automatically recomputed after
every payment: `unpaid` (nothing paid) → `partially_paid` (some but not all paid) → `paid`
(fully paid). A payment greater than the current remaining balance is rejected - by the data
layer itself (`demo_site/data.py: record_payment`), so both the browser form and the JSON
API enforce it identically, and the invoice is left completely untouched by a rejected
attempt.

**Demo site** (the automation target): every invoice's detail page
(`/invoices/<id>`) shows Total/Paid/Remaining tiles, a Payment History table, and - while
`remaining_amount > 0` - a "Record Payment" form (amount + date). `POST
/api/invoices/{id}/payments` and `GET /api/invoices/{id}/payments` provide the same
capability and history via JSON.

**Agent**: four new goals - `record_payment_for_invoice`, `get_invoice_remaining_balance`,
`list_partially_paid_invoices`, `pay_remaining_balance_for_invoice` - work through the same
API-first → browser-fallback architecture as the original 5, including genuine LLM-driven
planning/re-planning when a key is configured. Natural-language invoice ids like `INV-1005`
are normalized to the internal `inv_1005` form automatically. See
[`docs/execution.md`](docs/execution.md) for exactly how the browser fallback fills in the
Record Payment form, and [`docs/architecture.md`](docs/architecture.md) for the full design
notes including a subtle ARIA-role bug this feature caught and fixed in the DOM inspector.

## Environment variables

See [`.env.example`](.env.example) for the full list with defaults. The important ones:

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `OPENROUTER_API_KEY` | No | *(blank)* | Enables the OpenRouter LLM path. Blank = deterministic fallback. |
| `OPENROUTER_MODEL` | No | `openrouter/free` | Which OpenRouter model to use. |
| `DEMO_SITE_BASE_URL` | No | `http://127.0.0.1:8001` | Where the agent looks for the demo site. |
| `INVOICE_API_KEY` | No | `demo-invoice-api-key` | Shared secret between backend and demo site's Invoice API. |
| `ALLOWED_DOMAINS` | No | `localhost,127.0.0.1` | Domains Playwright is allowed to navigate to. |
| `MAX_RETRIES_PER_ACTION` | No | `2` | Bounded retry budget per validated action. |
| `PLAYWRIGHT_HEADLESS` | No | `true` | Set to `false` to watch the browser while it runs. |

## Running tests

```bash
./scripts/run_tests.sh
```

Starts the demo site automatically if it isn't already running, then runs the full backend
suite (`backend/tests/`) with pytest. **156 tests**, covering: Pydantic schema validation, the
pre-execution validator (the hallucination guardrail) in isolation, the tool registry, API
error classification, the deterministic and OpenRouter-aware planner, all documented API
endpoints via `TestClient`, genuinely LLM-driven plan generation and re-planning (prompt
construction + response validation, mocked LLM client - see `test_llm_planning.py`), and -
critically - live integration tests that start the real demo site and run the real agent
through real Playwright: login+download, an API-first fetch, a genuine API→browser fallback
with the Invoice API actually disabled, an unknown task correctly asking for clarification,
a hallucinated element being blocked end-to-end instead of hanging, a genuinely LLM-produced
plan (mocked model, real execution) completing via a real API call, a hallucinated LLM plan
triggering real re-planning that is proven to have received the real DOM snapshot and real
error text, and an LLM path that gives up correctly falling back to the deterministic handler.
`test_new_invoice_feature.py` additionally covers the "Add New Invoice" feature: the HTML
form, the JSON API, validation, customer lookup-or-create by email, demo-data reset, and -
the important one - the existing agent (both API-first and real-browser-fallback paths)
correctly discovering and interacting with an invoice created at runtime, not just the seed
data. `test_payment_tracking.py` covers payment tracking end to end: status transitions
(unpaid → partially_paid → paid) across single and multiple payments, overpayment rejection
(both right at the boundary and after a prior partial payment), invalid-input validation, the
browser form and payment-history table, the 4 new natural-language task phrasings, and - for
every one of the 4 new agent goals - both the API-first path and the real-Playwright
browser-fallback path (including a payment recorded against an invoice created at runtime,
proving the two features compose). `test_list_unpaid_invoices.py` covers the general
"who/what is unpaid" query fix: 6 natural-language phrasings (including ones with no literal
"unpaid" or "invoice" word, like "Who hasn't paid the amount yet?") that used to incorrectly
return WAITING_FOR_USER now completing correctly with customer names attached, through both
the API-first and real-browser-fallback paths, correctly reflecting a payment recorded
mid-test, and a battery of regression checks proving the amount-threshold goal and every
other existing goal are completely unaffected.

**Last verified run: 156 passed, 0 failed.**

For stronger clean-machine evidence than a single run in a possibly-warm environment, see
[`scripts/verify_clean_env.sh`](scripts/verify_clean_env.sh) - it builds a brand-new Python
virtualenv from scratch and re-runs the whole suite there. Last verified run: also 156 passed,
0 failed, and this is the process that originally caught and fixed a real Playwright
version-pinning bug (see [Docker](#docker) below).

## Evaluation

See [`docs/evaluation.md`](docs/evaluation.md). Short version: `POST /api/evaluations/run`
(or the Evaluation page's button) runs the 5 demo tasks and 6 adversarial hallucination
probes for real and computes every metric from the results - nothing is hardcoded.

## Docker

`docker-compose.yml` and per-service `Dockerfile`s are included. The backend and demo site
images are built on **Microsoft's official Playwright Python image**
(`mcr.microsoft.com/playwright/python:v1.56.0-noble`), which ships Chromium and all required
OS-level dependencies pre-installed at a version matched to the pinned `playwright==1.56.0`
pip package - this avoids the common failure mode of `playwright install --with-deps` on a
generic slim image (missing apt packages, or a version mismatch between the pip package and
the downloaded browser build). Services also have real healthchecks now, and
`depends_on: condition: service_healthy` means backend won't start until the demo site
actually answers `/api/status`, and frontend won't start until backend answers `/api/health`.

**They have not been runtime-built with `docker build` itself** - no Docker daemon was
available in the sandbox this project was built in, and `mcr.microsoft.com` is outside that
sandbox's network allow-list. What WAS actually run and verified as the closest available
substitute is [`scripts/verify_clean_env.sh`](scripts/verify_clean_env.sh): it creates a
brand-new Python virtualenv (no reused site-packages), installs `backend/requirements.txt`
from scratch, and runs the full 83-test suite (including live Playwright integration tests
against a freshly-started demo site) - **last verified run: 83 passed, 0 failed.** This
verification is genuinely how a real bug was found and fixed during this project: the
`playwright` dependency was originally a loose range (`>=1.45,<1.60`), which a fresh install
resolved to `1.59.0` - a version whose bundled browser build wasn't yet cached, so
`playwright install chromium` tried to download it and failed in this sandbox's restricted
network. `playwright` is now pinned to the exact version (`==1.56.0`) that the browser cache
and the Docker image tag both match, which is the correct practice for Playwright
specifically (its browser binaries are version-coupled to the pip package in a way most
libraries' dependencies aren't). If you hit a Docker issue, the manually-verified path
(`./scripts/run_dev.sh` or `./scripts/verify_clean_env.sh`) is the fallback known to work;
please treat the Docker files as reviewed and internally consistent, but not a guarantee.

```bash
docker compose up --build
# demo site  → http://localhost:8001
# backend    → http://localhost:8000
# frontend   → http://localhost:5173
```

## Known limitations

Documented in detail in [`docs/architecture.md`](docs/architecture.md), summarized here:

1. **Synchronous execution.** Each `/agent/run` call executes to completion within one
   request rather than as a pollable/streamable background job. Fine for this demo's
   sub-few-second tasks; `/agent/stop` says plainly that there's nothing in-flight to
   cancel rather than pretending to.
2. **Two execution paths, by design.** With an OpenRouter key configured, the agent runs a
   genuinely LLM-produced plan (`executor.execute_llm_plan`) and genuinely LLM-driven
   re-planning on failure (`planner.generate_next_step`, given the real DOM/error at the
   moment of failure). Without a key - or if the LLM path fails or gives up - the same 9
   tasks (the original 5, plus the 4 payment-tracking ones) run through dedicated
   deterministic goal handlers instead. Both paths share the same tool registry, validator,
   retry logic, and execution tracing; the LLM path is a safe addition on top of the
   deterministic one, never a replacement for it. An unrecognized task gets an honest
   clarification request on either path, never a guess - the canned clarification list
   deliberately still names only the original 5 tasks (see `agent.py:CLARIFICATION_OPTIONS`),
   so the existing "asks for clarification" test didn't need to change; the 4 payment goals
   are just as real, they're simply not offered as one of the 5 suggested examples.
3. **The LLM-driven path's result shape is best-effort**, extracting whatever structured
   data the executed steps happened to produce rather than the precisely-typed fields each
   deterministic goal handler promises.
4. **Docker's images were rebuilt on Microsoft's official Playwright image and still not
   runtime-built with `docker build` itself** (see "Docker" above for what WAS actually run
   and verified as a substitute, and a real dependency bug that verification caught and fixed).
5. **Frontend has no automated test suite.** The backend has 156 real tests including live
   Playwright integration tests (both the deterministic and the LLM-driven-with-mocked-model
   paths); the frontend was verified manually via real screenshots against the running
   backend, but has no Vitest/Playwright-for-frontend suite in this build.
6. **`/api/browser/state` shows the last recorded observation**, not a live view of an
   in-progress execution, per the synchronous execution model above.
7. **The evaluation suite's fixed demo-task list wasn't extended with a payment task.**
   `POST /api/evaluations/run` still runs exactly the original 5 read-only-ish demo tasks;
   a payment-recording task would *mutate* the shared demo data on every run (permanently
   turning an invoice partially-paid), making repeated evaluation runs non-reproducible
   without an explicit reset first. Payment tracking is instead covered by the dedicated,
   self-resetting `test_payment_tracking.py` suite (see "Running tests" above), which is the
   right tool for correctness testing; the evaluation suite's job is a stable, repeatable
   metrics snapshot, which a stateful task would compromise.


- Support payment refunds/voids (the current model only supports adding payments, never
  reversing one) and multi-currency amounts.
