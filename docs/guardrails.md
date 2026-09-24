# Guardrails / Responsible AI

The central principle: **the LLM proposes, the validator decides, the executor executes,
the observer checks reality, the agent adapts.**

## What the LLM may and may not do

May: understand tasks, classify them into a known goal, (optionally) extract constraints.
Everything downstream of that - which tool to call, whether an element exists, whether an
action succeeded - is deterministic Python, not model output.

May not: execute arbitrary Python/shell/JS, access arbitrary filesystem paths, bypass
validation, invent a successful result, or directly manipulate Playwright. There is no code
path in this project where LLM output reaches `eval`, `exec`, a shell, or the Playwright API
directly - LLM output only ever selects a `goal` id from a closed enum
(`planner.KNOWN_GOALS`), which is validated before use.

## Hallucination prevention (pre-execution validation)

See `docs/execution.md` "Pre-execution validation". Concretely, `app/services/validator.py`
is a pure function - `validate_step(step, observation) -> ValidationResult` - with no
Playwright dependency, which is what makes it possible to unit-test directly (see
`tests/test_validator.py`) and to run as adversarial evaluation cases without needing a real
browser for most of them (see `app/services/evaluator.py: ADVERSARIAL_CASES`).

Adversarial cases exercised by `POST /api/evaluations/run`:

| Case | What it checks |
|---|---|
| `planned_element_does_not_exist` | Empty observation → click is rejected, not hallucinated |
| `button_text_changed` | Exact name missing, but a same-role element exists → safe retarget |
| `target_is_disabled` | Element present but `enabled=false` → rejected |
| `target_not_visible` | Element present but `visible=false` → rejected |
| `multiple_matching_elements` | Several valid matches → picking the first visible/enabled one is accepted (this is intentionally *not* blocked - it mirrors real DOMs like the invoices table where multiple "Open" links legitimately exist) |
| `no_observation_available` | No DOM snapshot yet → action rejected rather than assumed safe |

## Domain restriction

`app/services/browser.py: BrowserSession.assert_domain_allowed()` checks every `navigate`
call's hostname against `ALLOWED_DOMAINS` (default `localhost,127.0.0.1`) before Playwright
ever gets to it. A navigation outside the allow-list raises `DomainNotAllowedError`, which
`tool_navigate` turns into a `ToolExecutionError(category="navigation_error")` - the task
fails cleanly rather than browsing an arbitrary site.

## No silent success

- A downloaded file is verified to exist on disk (`Path(file_path).exists()`) before the
  task is reported as completed (`executor.py: execute_login_and_download_latest_invoice`).
- A login is verified by checking the post-click URL actually reached `/dashboard` or
  `/invoices`, not just that the click didn't error (`executor.py: _login`).
- Every tool failure is classified into a named category (`element_not_found`, `timeout`,
  `download_error`, `api_unavailable`, `authentication_error`, `validation_error`,
  `extraction_error`, `navigation_error`, `unexpected_error`) and surfaced on the
  `Execution.error` field and the relevant `ExecutionStep.error` - never swallowed.
- The top-level `run_agent()` has a catch-all `except Exception` that marks the execution
  `FAILED` with the real error message, so an unexpected bug fails loudly in the trace
  instead of hanging or crashing the API process.
- A payment that would exceed an invoice's remaining balance is rejected by
  `data.record_payment()` itself - the single function both the HTML form and the JSON API
  call - so the invoice is genuinely untouched by a rejected attempt, not left in some
  partially-applied state. The agent surfaces this the same way as any other guardrail
  rejection: `TaskFailed(category="validation_error")` with the real "exceeds the remaining
  balance" message, never a silently-capped or silently-ignored payment.

## Destructive actions / confirmation

The demo site's automatable actions (viewing invoices, downloading files) are non-destructive
by design, so this build does not implement an interactive confirmation prompt for them. The
architecture point stands, though: any goal handler that performed a destructive action
(e.g. deleting a record) would need a `WAITING_FOR_USER` step requesting confirmation before
proceeding - the state machine already supports this (`ClarificationNeeded` in
`executor.py`, `WAITING_FOR_USER` state in `agent.py`), it's just not wired to a specific
destructive action in the current goal set because none of the 5 demo tasks are destructive.

## No exposed chain-of-thought

The "Agent Reasoning" panel in the frontend shows only the `step_label` strings already
written to the execution trace (e.g. `"Task understood (via deterministic_fallback):
goal=get_latest_invoice_info"`, `"Invoice API unavailable; re-planning to browser
fallback"`) - concise, safe explanations of *why* a routing decision was made, not raw model
reasoning tokens (the deterministic fallback has no reasoning tokens to begin with, and the
OpenRouter path only ever returns a structured JSON classification, never freeform
chain-of-thought).
