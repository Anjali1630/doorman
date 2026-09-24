# Execution

## Tool registry

Every action - browser or API - is a named tool in `app/tools/registry.py`:
`navigate, click, type, select, press, wait, extract, inspect_page, screenshot, download,
api_request, go_back, go_forward`. Each tool:

- takes a Playwright `Page` (or `None` for `api_request`) and an `args` dict
- returns a `ToolResult(success, data)` or raises `ToolExecutionError(message, category)`
- classifies its own failures into the error taxonomy (`element_not_found`, `timeout`,
  `download_error`, `api_unavailable`, etc.) used by retry/replanning logic

The LLM/planner never touches Playwright directly - it can only select a tool by name.

## Pre-execution validation

Before any element-targeting action (`click`, `type`, `select`, `download`),
`_validated_action()` in `executor.py`:

1. Takes a fresh `inspect_page()` snapshot of the real DOM
2. Calls `validator.validate_step()` to check the planned target actually exists, and (per
   its declared preconditions) is visible/enabled
3. If validation fails, tries `validator.attempt_retarget()` - a same-role, similar-name
   element - before giving up
4. Only calls the actual tool if validation passed (original or retargeted)
5. Logs every one of these sub-steps to `execution_steps`, including rejections

This is what prevents the agent from clicking/typing into elements that don't exist. See
`docs/guardrails.md` and the adversarial test cases in `app/services/evaluator.py`.

## Retry and error handling

`_validated_action()` retries up to `MAX_RETRIES_PER_ACTION` (default 2) on either a
validation rejection (re-inspecting the page each time) or a tool execution failure. Each
attempt is logged with its `retry_count`. After the retry budget is exhausted, the goal
handler raises `TaskFailed(message, category)`, which `agent.py` catches and turns into a
`FAILED` execution with the error recorded - never a silent success.

## Re-planning: two kinds

**Deterministic re-planning (API → browser fallback).** For the 4 API-first goals, the
deterministic executor first checks `api_router.is_api_available()` (a live HTTP call). If
the API is unavailable, or an API call raises `ToolExecutionError`, the executor logs a step
with `execution_method="replanning"` and switches to the browser control flow for that same
goal. See `tests/test_integration_live.py::test_api_disabled_triggers_real_browser_fallback`
for an automated, end-to-end proof of this (toggles the real Invoice API off, then asserts
`api_calls == 0`, `browser_actions > 0`, `replans_used >= 1`).

**Genuine LLM-driven re-planning (LLM-driven path only).** When an OpenRouter key is
configured and `agent.py` is running an LLM-produced plan (`executor.execute_llm_plan`), a
step failure doesn't just retry or fall back to a hardcoded rule - it calls
`planner.generate_next_step()` with the REAL current DOM snapshot (freshly captured at the
moment of failure) and the REAL error string, and asks the model for exactly one next step.
That step is validated against `PlanStepSchema` and then run through the same
validated-action path as everything else; if the model gives up, or its answer doesn't
validate, or the re-planning budget (`MAX_REPLANS_PER_TASK`) is exhausted, the whole task
falls back to the deterministic goal handler rather than continuing to guess. This is
covered end to end (real Playwright, real demo site, mocked LLM responses that are asserted
to have actually received the real DOM/error) by
`tests/test_integration_live.py::test_llm_driven_replanning_uses_real_dom_and_error` -
prompt-construction-only unit tests live in `tests/test_llm_planning.py`.

## Execution trace

Every step - planning, validation, tool execution, replanning - is written as an
`ExecutionStep` row by `StepLogger.log()`: `step_label, action, target, tool,
execution_method, validation_result, status, duration_ms, error, retry_count, timestamp`.
`GET /api/executions/{id}/trace` (and `/steps`) expose this directly; the Control Center and
Executions pages in the frontend render it as a scrollable timeline.

## Payment tracking goals: navigating directly to a known invoice

Unlike the original 5 goals (which act on "the latest invoice" or "invoices matching a
filter" and so have to click through the `/invoices` list to find the right row), the 4
payment goals always have a specific invoice id up front - extracted from the task text by
the planner. Their browser-fallback path (`executor._open_invoice_by_id`) takes advantage of
that by navigating straight to `/invoices/{invoice_id}` after login, rather than scanning the
list - still going through the exact same `_login()`, `tool_navigate`, and `inspect_page()`
calls as everything else, it just skips a step it doesn't need. If the id doesn't exist, the
detail page 404s, `inspect_page()` finds none of the expected elements, and pre-execution
validation rejects the next step cleanly (`TaskFailed`, category `validation_error` or
`extraction_error`) rather than clicking something that isn't there.

`execute_record_payment_for_invoice`'s browser path fills the "Payment Amount" and "Payment
Date" textboxes and clicks "Record Payment" through the normal `_validated_action` path, then
checks `observation["url"]` to tell success from failure: on success, `main.py`'s
`record_payment_submit` issues a 303 redirect to the clean `/invoices/{id}` URL; on failure
(e.g. overpayment), it re-renders the same `/invoices/{id}/payments` URL directly with a 400
and an error banner. Checking the URL after the click is what lets a successful payment be
confirmed in milliseconds instead of waiting out a timeout for an error element that (on the
success path) was never going to appear.

`execute_pay_remaining_balance_for_invoice` ("Mark the remaining amount of INV-1005 as paid")
is implemented as a two-step composition, not a separate code path: it calls
`execute_get_invoice_remaining_balance` to find the current `remaining_amount`, then - if
it's greater than zero - calls `execute_record_payment_for_invoice` with that exact amount.
Both API-first/fallback logic and the overpayment safety check live in one place
(`execute_record_payment_for_invoice`) rather than being duplicated.

## Agent state machine

`app/services/agent.py` defines the states from the spec (`IDLE, PLANNING, OBSERVING,
VALIDATING, EXECUTING, VERIFYING, REPLANNING, WAITING_FOR_USER, COMPLETED, FAILED`).
`Execution.status` moves through `PLANNING → EXECUTING → (COMPLETED | FAILED |
WAITING_FOR_USER)` for each run; the finer-grained states (`OBSERVING`, `VALIDATING`,
`REPLANNING`) are represented as `execution_method`/`validation_result` values on individual
`ExecutionStep` rows rather than as top-level `Execution.status` values, since (per the
synchronous execution model - see `docs/architecture.md`) a full run typically passes
through several of them before the HTTP response is even returned.
