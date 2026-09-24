# Planning

## Task understanding

Input: free-form text, e.g. `"Find all unpaid invoices above ₹10,000."` or
`"Record a payment of ₹500 for invoice INV-1005"`.

Output: a `TaskUnderstanding` (see `app/schemas/schemas.py`):

```json
{
  "goal": "list_unpaid_invoices_above",
  "entities": ["invoice"],
  "constraints": ["amount > 10000", "status = unpaid"],
  "expected_output": ["invoice_number", "amount", "status"]
}
```

`goal` must be one of 10 known ids or `"unknown"` - this is enforced by both the deterministic
matcher and, when OpenRouter is used, by validating the model's JSON output against this
closed set before trusting it (see `docs/architecture.md`). The 10 goals: the original 5
(`login_and_download_latest_invoice`, `get_latest_invoice_info`,
`list_unpaid_invoices_above`, `download_latest_unpaid_invoice`,
`get_latest_invoice_customer_email`), 4 payment-tracking goals
(`record_payment_for_invoice`, `get_invoice_remaining_balance`,
`list_partially_paid_invoices`, `pay_remaining_balance_for_invoice`), and one general query
goal (`list_unpaid_invoices`).

### General vs. threshold-specific unpaid queries

`list_unpaid_invoices` ("Show me all unpaid invoices", "Who hasn't paid yet?", "Which
customers haven't paid?") is deliberately a SEPARATE goal from `list_unpaid_invoices_above`
("Find all unpaid invoices above ₹10,000") - the former returns every unpaid invoice with its
customer name, the latter filters by a minimum amount and doesn't need one. In
`planner._deterministic_understand`, the general rule is checked LAST, after every other
rule (including the amount-threshold one), and is additionally gated on "no amount threshold
was detected and no specific invoice id was named" - so a phrase like "Find all unpaid
invoices above ₹10,000" always resolves to the threshold-specific goal, never the general one,
regardless of which unpaid-related keywords it happens to also contain. See
`docs/guardrails.md` for why this ordering, not just the keyword list, is what actually
prevents misclassification.

For the 4 payment goals, `constraints` also carries the specific invoice referenced and (for
recording a payment) the amount, as plain `key = value` strings the executor parses back out
at run time: `"invoice_id = inv_1005"`, `"payment_amount = 500"`. Task text is not required
to use the internal id format - `planner._extract_invoice_id` normalizes phrasings like
`"INV-1005"`, `"INV 1005"`, or `"inv-1005"` to the canonical `"inv_1005"` before it's ever
stored in a constraint or looked up.

## Genuine LLM-driven action plan (when a key is configured)

`agent.py: _persist_plan()` decides and persists the plan BEFORE the browser opens: if
`OPENROUTER_API_KEY` is set, it asks `planner.generate_action_plan()` for a real, ordered
list of tool calls (not just a goal id) grounded in a text description of the demo site,
validates the response against `ActionPlan`/`PlanStepSchema`, and persists exactly that if it
validates. Otherwise (no key, or the LLM call fails/returns something invalid) it falls back
to a deterministic goal-template plan instead - see `docs/architecture.md` "LLM architecture"
for the full design, including how re-planning on failure works.

## Deterministic coarse action plan (fallback, and always used without a key)

Example persisted steps for `login_and_download_latest_invoice`:

| step_id | action | target | execution_method |
|---|---|---|---|
| 1 | navigate | login page | browser |
| 2 | type | role=textbox name=Username | browser |
| 3 | type | role=textbox name=Password | browser |
| 4 | click | role=button name="Log In" | browser |
| 5 | navigate | invoices page | browser |
| 6 | click | role=link name="Open (latest invoice)" | browser |
| 7 | download | role=button name="Download Invoice" | browser |

Example persisted step for `record_payment_for_invoice` (API-first; the browser-fallback
equivalent navigates straight to `/invoices/{invoice_id}` and fills in the Record Payment
form - see `docs/execution.md`):

| step_id | action | target | execution_method |
|---|---|---|---|
| 1 | api_request | `POST /api/invoices/{id}/payments` | api |

## Clarification instead of guessing

If `understand_task()` returns `goal = "unknown"` (task doesn't match any of the 9 known
goals), the agent orchestrator sets `Execution.status = WAITING_FOR_USER` and returns
`agent.py: CLARIFICATION_OPTIONS` as suggestions, rather than fabricating a plan for a task
it doesn't actually understand. That list deliberately still names only the original 5 task
phrasings (kept stable on purpose - see `README.md#known-limitations`); the 4 payment goals
are fully functional when phrased directly, they're just not offered as one of the 5 curated
examples. See `app/services/agent.py: run_agent`.
