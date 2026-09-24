# Architecture

## Overview

```
NATURAL LANGUAGE TASK
        │
        ▼
TASK UNDERSTANDING (planner.py)  ── OpenRouter (if key set) or deterministic keyword matcher
        │
        ▼
COARSE PLAN (agent.py: _build_coarse_plan)  ── persisted to plans/plan_steps tables
        │
        ▼
GOAL EXECUTION (executor.py: execute_<goal>)
        │
        ├── API-FIRST: api_router.is_api_available() → tool_api_request → validate response
        │
        └── BROWSER FALLBACK: tool registry (navigate/click/type/.../download)
                    each element action goes through:
                    inspect_page() → validator.validate_step() → (retarget?) → tool call → log
        │
        ▼
RESULT + EXECUTION TRACE (persisted: executions, execution_steps, browser_observations)
```

## Components

| Layer | File | Responsibility |
|---|---|---|
| Config | `app/core/config.py` | Reads all environment variables, no hardcoded secrets |
| Schemas | `app/schemas/schemas.py` | Pydantic models for task understanding, plan steps, tools |
| ORM | `app/models/orm.py` | All 9 required tables |
| LLM client | `app/services/llm.py` | OpenRouter chat-completions wrapper, raises `LLMError` on any failure |
| Planner | `app/services/planner.py` | Task text → `TaskUnderstanding`, via OpenRouter or deterministic matcher |
| API router | `app/services/api_router.py` | Registry of API capabilities + live availability check against the demo site |
| Browser | `app/services/browser.py` | Playwright lifecycle (async API) + domain allow-list enforcement |
| DOM inspector | `app/services/dom_inspector.py` | Extracts a structured snapshot of the real page |
| Tool registry | `app/tools/registry.py` | Every action (browser or API) as a named, schema'd tool |
| Validator | `app/services/validator.py` | Pre-execution validation - the hallucination guardrail |
| Executor | `app/services/executor.py` | Goal-specific control flow: validation, retry, logging |
| Agent orchestrator | `app/services/agent.py` | State machine, ties everything together, persists to DB |
| Evaluator | `app/services/evaluator.py` | Runs demo tasks + adversarial cases, computes real metrics |

## API-first strategy

For 4 of the 5 demo tasks, `api_router.is_api_available()` makes a live HTTP call to the
demo site's `/api/status` endpoint before every execution. If the Invoice API is reachable,
the agent calls it directly (`tool_api_request`) instead of touching the browser. This is a
genuine live check, not a config flag baked in at plan time - toggling the Invoice API off in
the demo site's Settings page (or via `POST /settings/toggle-api`) makes the very next
execution fall back to Playwright automatically, and this is verified by an automated test
(`tests/test_integration_live.py::test_api_disabled_triggers_real_browser_fallback`).

## Browser fallback

When no API can satisfy the goal (or the API call fails/is unavailable), the executor logs
a `replanning` step and proceeds through Playwright: login, navigate, inspect, validate,
click/type/download. Every element-targeting action is preceded by a real DOM inspection and
a validator check - see `docs/guardrails.md`.

## Demo site data: mutable at runtime, discoverable by the agent

`demo_site/data.py`'s `CUSTOMERS`/`INVOICES` lists are no longer fixed for a server's
lifetime: the portal's **Add New Invoice** feature (`GET`/`POST /invoices/new`, and the JSON
`POST /api/invoices`) appends to these same module-level lists that every other route reads
from - the invoice list page, the invoice detail page, and every read endpoint in the Invoice
API. There is no separate cache or sync step, which is what makes new invoices immediately
visible to the agent through both paths:

- **API-first**: `GET /api/invoices`, `/api/invoices/latest`, and `/api/invoices/{id}` all
  read `data.INVOICES` live, so a newly-created invoice is included the next time any of
  those are called - no restart, no cache invalidation.
- **Browser fallback**: the invoices table (`/invoices`) is rendered from the same list on
  every request, so a new row appears with the exact same `data-invoice-id` attribute and
  `Open`/`Download Invoice` structure as a seed invoice - the executor's DOM-based discovery
  (`inspect_page`, `table_rows`) and the validator don't need to know or care whether a given
  invoice was in the original seed data or created five seconds ago.

`find_or_create_customer(name, email)` looks up an existing customer by email
(case-insensitive) before creating a new one, so invoices for a known customer don't create
duplicate customer records. `POST /api/demo/reset` calls `data.reset_data()`, restoring the
original 6-invoice/4-customer seed set - this is what keeps "Load Demo"/"Reset Demo" giving a
known, reproducible starting state even after invoices have been added during a session, and
is also what test isolation in `tests/test_new_invoice_feature.py` relies on.

See `tests/test_new_invoice_feature.py` for end-to-end proof of agent discovery: one test
adds an invoice via the API and confirms the API-first goal handlers pick it up as "the
latest invoice"; another disables the Invoice API first (forcing the real browser path) and
confirms Playwright discovers and downloads that same runtime-created invoice using nothing
but the existing goal handler and the existing invoices-table template.

## Payment tracking

Every invoice dict in `demo_site/data.py` carries `total_amount`, `paid_amount`,
`remaining_amount`, `status` (`"unpaid"` | `"partially_paid"` | `"paid"`), and `payments`
(oldest-first history). The legacy `amount` key is kept and always kept equal to
`total_amount` - existing code/tests reading `inv["amount"]` (there was a lot of it) were
never touched; `total_amount` is simply the new canonical name going forward.
`record_payment(invoice_id, amount, date)` is the single place status is ever changed: it
validates the invoice exists, the amount is positive, the date is valid, and the amount
doesn't exceed the remaining balance (with a 1-cent floating-point tolerance) - raising
`InvoiceNotFoundError` / `PaymentValidationError` for the HTML route and the JSON API to
translate into a 404/422/400 respectively, identically on both surfaces since both call the
exact same function.

**Two bugs this feature's own testing caught and fixed, both in shared infrastructure other
features also depend on:**

1. `dom_inspector.py`'s browser-fallback status detection checked cell text for `"unpaid"`
   then `"paid"` - but `"partially paid"` / `"partially_paid"` both contain `"paid"` as a
   substring, so every partially-paid row would have been misread as fully paid. Fixed by
   checking the more specific `"partially paid"` substring first.
2. The Payment Amount field was originally `<input type="number">`, matching the "Add New
   Invoice" form's Amount field. Empirically verified during development that Chromium
   computes `type="number"` inputs to ARIA role **`spinbutton`**, not `textbox` - meaning
   `page.get_by_role("textbox", name="Payment Amount")` (the targeting strategy every other
   form field in this codebase uses) would never find it, even though `dom_inspector.py`
   could be made to list it. Rather than teach the validator/executor a second role category
   for one field, the Payment Amount input uses `type="text" inputmode="decimal"` instead -
   ARIA role `textbox`, same targeting strategy as every other field, HTML5 numeric keyboard
   hint on mobile without changing the accessible role. Server-side validation (already
   required for overpayment checking) is authoritative regardless.

The invoice detail page's Record Payment form is only rendered while `remaining_amount > 0`;
once an invoice reaches `remaining_amount == 0` the form is replaced with a plain "fully
paid" message, and the (existing, unmodified) `_validated_action` pre-execution check means
an agent attempting to record a payment against an unpayable invoice fails cleanly (no such
form found) rather than the browser silently doing nothing.

`tests/test_payment_tracking.py` covers the data layer, both HTTP surfaces, status
transitions across single and multiple payments, overpayment rejection at and past the
boundary, and - for all 4 new agent goals - both the API-first and real-browser-fallback
paths, including composing with the "Add New Invoice" feature (a payment recorded against an
invoice created at runtime in the same test).

## LLM architecture (OpenRouter)

- `OPENROUTER_API_KEY` unset → `LLM_MODE = deterministic_fallback`. `planner.understand_task()`
  uses a keyword/regex matcher (`_deterministic_understand`) that reliably classifies the
  documented demo task phrasings (and close variants) into one of the known goals, or
  `unknown`, and `agent.py` persists and executes a deterministic goal-template plan.
- `OPENROUTER_API_KEY` set → the LLM is used for three genuinely distinct things, each with
  its own fallback:
  1. **Task understanding** (`planner.understand_task`) - classify into a known goal id. A
     valid, non-`"unknown"` LLM goal is trusted directly and never overridden. But if the LLM
     itself returns `"unknown"` (or errors, rate-limits, or returns something that doesn't
     validate), the deterministic matcher still gets a chance to resolve the task before the
     agent gives up and asks for clarification - an LLM "I don't know" is treated as "try the
     other method", not as the final word, since the deterministic matcher can reliably
     recognize phrasings the model occasionally misses (e.g. "Who hasn't paid yet?" with no
     literal "unpaid"/"invoice" wording).
  2. **Real action-plan generation** (`planner.generate_action_plan`) - the model proposes an
     actual ordered list of tool calls (`ActionPlan`/`PlanStepSchema`: which tool, which target,
     what value, which preconditions), not just a goal id. This is called once per task, before
     the browser even opens (see "Separation of planning and execution" below), and its output
     is validated against the closed tool/schema set before anything is persisted or run.
  3. **Real re-planning** (`planner.generate_next_step`) - called only when a step actually
     fails during execution, with the REAL current DOM snapshot and the REAL error message
     in the prompt. Returns exactly one next step, grounded in what's actually on the page -
     or `None` if the model can't find a safe one, in which case the whole task falls back to
     the deterministic goal handler rather than looping or guessing further.
  If any of these three calls fails, rate-limits, or returns something that doesn't validate,
  the affected piece transparently falls back to its deterministic counterpart and logs why -
  it never pretends the fallback was an LLM call, and the UI's "LLM Mode" badge always
  reflects which one actually produced the task-understanding result.
- The LLM is still **not** called per browser action - only once for planning and at most
  `MAX_REPLANS_PER_TASK` times for re-planning (default 2), keeping free-tier usage low.

### Separation of planning and execution

Planning happens entirely before the browser opens: `agent.py: _persist_plan()` calls
`generate_action_plan()` with `dom_snapshot=None` and persists the result to
`plans`/`plan_steps` FIRST, then `run_agent()` opens a `BrowserSession` and executes that
exact persisted plan (`executor.execute_llm_plan`). This means the LLM's initial plan is
necessarily a "best guess" grounded only in a textual description of the demo site (given
in the prompt), not a live DOM snapshot - and that's intentional: it's what makes the
pre-execution validator's job meaningful (see `docs/guardrails.md`) rather than redundant.
Every element-targeting step in an LLM-produced plan is checked against the real page before
it runs, exactly like a deterministic goal handler's steps; a wrong guess triggers real
re-planning (item 3 above) rather than a validation error you'd only see in a log.

### If the LLM-driven path fails entirely

`agent.py: run_agent()` wraps `executor.execute_llm_plan()` in a `try/except TaskFailed`. If
the LLM's plan can't complete (its own re-planning attempts exhausted or gave up), the
exception is caught, logged as a `replanning` step explaining why, and the task is retried
via the ORIGINAL deterministic goal handler (`_run_deterministic_goal`) for the same goal -
so a confused or unlucky LLM run degrades to the same reliable behaviour as running with no
key at all, rather than failing the whole task. This is covered by
`tests/test_integration_live.py::test_llm_driven_execution_falls_back_when_llm_gives_up`.

## Known limitations (by design, documented rather than hidden)

1. **Synchronous execution model.** `/api/agent/run` executes the entire task to completion
   within a single request/response cycle rather than as a background job you poll or stream.
   Demo tasks complete in well under a second (API-first) to a couple of seconds (browser).
   This means `/api/agent/stop` has nothing in-flight to cancel (it says so explicitly rather
   than pretending), and `/api/browser/state` reflects the *last* observation from the most
   recent execution, not a live-updating view of an in-progress one. A production version
   would move execution to a background task/queue with WebSocket or SSE streaming of trace
   events to the frontend.
2. **Two execution paths, converging on the same safety machinery.** When a key is
   configured, `execute_llm_plan` drives a genuinely LLM-produced plan; when it isn't (or
   the LLM path fails), the 5 `execute_<goal>` functions in `executor.py` provide a reliable,
   fully-deterministic path for the same 5 demo tasks. Both paths share the same tool
   registry, validator, and execution tracing - the LLM path is a safe *addition* on top of
   the deterministic one, never a replacement for it, which is what keeps "works with no key
   at all" true. A task that doesn't match one of the 5 known goals gets an honest
   clarification request rather than a guess, on either path.
3. **The LLM-driven path's result shape is best-effort.** `executor._summarize_llm_result`
   extracts whatever structured data the executed steps happened to produce (API JSON fields,
   extracted text, a download path) rather than the precisely-typed result each deterministic
   goal handler promises. This is a genuine trade-off of letting the LLM's plan shape vary.
4. **Docker's base images were changed to Microsoft's official Playwright image and were
   still not runtime-validated with `docker build`** (no Docker daemon in the sandbox, and
   its registry is outside the sandbox's network allow-list). See root `README.md#docker`
   for what WAS actually verified as a substitute (a from-scratch virtualenv install + full
   test run via `scripts/verify_clean_env.sh`), and for a real bug that verification caught
   (a loose `playwright` version range that resolved to a pip version whose browser build
   wasn't cached - now pinned exactly).
