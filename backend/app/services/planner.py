"""
Planner: turns natural-language task text into a structured TaskUnderstanding,
a genuine LLM-produced ActionPlan, and (when a step fails) an LLM-produced
next step grounded in the real DOM/error state.

Three LLM-backed responsibilities, each with a deterministic fallback so the
app never requires a key and never crashes on a bad/rate-limited response:

1. `understand_task()` - classify the task into one of 5 known goals (or
   "unknown"). Unchanged from the original build.
2. `generate_action_plan()` - genuinely LLM-driven: the model proposes an
   actual ordered list of tool calls (not just a goal id), grounded in a
   text description of the demo site and (when available) a live DOM
   snapshot. Validated against `ActionPlan`/`PlanStepSchema` before it's
   trusted; on failure, returns `(None, reason)` and the caller
   (`agent.py`) falls back to the deterministic goal-template plan.
3. `generate_next_step()` - genuinely LLM-driven re-planning: called by the
   executor only when a step actually fails, with the REAL current DOM
   snapshot and the REAL error message included in the prompt. Returns a
   single next `PlanStepSchema`, or `None` if the model can't find a safe
   one - the caller then either retries with a different approach or fails
   the task, it never guesses on the LLM's behalf.

Both (2) and (3) are validated the same way (1) always was: the model's
output must parse into the closed Pydantic schema before it's used for
anything, and a malformed/off-schema/rate-limited response is treated as
"the LLM didn't help this time", never as a crash and never silently
mislabelled as deterministic output.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from app.core.config import settings
from app.schemas.schemas import ActionPlan, PlanStepSchema, TaskUnderstanding
from app.services.llm import llm_client, LLMError
from app.tools.registry import TOOL_DESCRIPTIONS

logger = logging.getLogger("agent.planner")

KNOWN_GOALS = [
    "login_and_download_latest_invoice",
    "get_latest_invoice_info",
    "list_unpaid_invoices",
    "list_unpaid_invoices_above",
    "download_latest_unpaid_invoice",
    "get_latest_invoice_customer_email",
    "record_payment_for_invoice",
    "get_invoice_remaining_balance",
    "list_partially_paid_invoices",
    "pay_remaining_balance_for_invoice",
]

# Matches "inv_1005", "inv-1005", "INV 1005", "INV-1005" etc. and normalizes
# to the canonical "inv_<N>" id the demo site actually uses - user-facing
# task text is not required to match the internal id format exactly.
_INVOICE_ID_PATTERN = re.compile(r"\binv[-_\s]?(\d{3,})\b", re.IGNORECASE)


def _extract_invoice_id(text: str) -> Optional[str]:
    m = _INVOICE_ID_PATTERN.search(text)
    if not m:
        return None
    return f"inv_{m.group(1)}"


def _extract_payment_amount(text: str) -> Optional[float]:
    # Handles "₹500", "Rs. 500", "of 500", "500 rupees" etc.
    match = re.search(r"(?:₹|rs\.?\s*)\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
    if not match:
        match = re.search(r"payment of\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


# Phrases that indicate a GENERAL "which invoices/customers haven't paid"
# query (goal="list_unpaid_invoices") - deliberately specific multi-word
# phrases rather than single common words, so this stays "safe" matching
# (won't fire on unrelated tasks) per the requirement. This rule is checked
# LAST in _deterministic_understand, after every other rule (including the
# amount-threshold and invoice-id-specific ones), so it can never override
# a more specific match - it only catches what nothing else already claimed.
_GENERAL_UNPAID_QUERY_PHRASES = (
    "unpaid",
    "not paid",
    "hasn't paid", "haven't paid", "has not paid", "have not paid",
    "still owe", "owes money", "who owes",
    "pending",
)

# ---------------------------------------------------------------------------
# 1. Task understanding (goal classification) - unchanged behaviour
# ---------------------------------------------------------------------------

_UNDERSTAND_SYSTEM_PROMPT = f"""You are the task-understanding module of a browser automation agent.
Classify the user's task into EXACTLY ONE of these goal ids:
{json.dumps(KNOWN_GOALS)}
If none fit, use "unknown".
Respond with ONLY a JSON object, no markdown, matching this shape:
{{"goal": "<one id above>", "entities": ["invoice"], "constraints": ["amount > 10000"], "expected_output": ["invoice_number","amount"]}}
constraints should capture numeric thresholds mentioned in the task, in plain text like
"amount > 10000". For payment-related goals, also capture the referenced invoice as
"invoice_id = inv_1005" (normalize ids like "INV-1005" to "inv_1005") and, when a payment
amount is mentioned, "payment_amount = 500".
"""


def _extract_amount_threshold(text: str) -> Optional[float]:
    # Handles "above ₹10,000", "over 10000", "> 10,000" etc.
    match = re.search(r"(?:above|over|greater than|>)\s*[₹rs\.]*\s*([\d,]+)", text, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _deterministic_understand(text: str) -> TaskUnderstanding:
    t = text.lower()
    amount = _extract_amount_threshold(text)
    invoice_id = _extract_invoice_id(text)

    # --- Payment-related goals are checked FIRST and are gated on finding
    # an explicit invoice id (except the "list partially paid" one, which
    # doesn't reference a specific invoice) - this keeps them from ever
    # colliding with the older, broader rules below, regardless of which
    # incidental words ("invoice", "amount") a payment sentence also uses.
    if invoice_id and any(kw in t for kw in ("mark", "settle", "pay off", "pay in full")) and \
            any(kw in t for kw in ("remaining", "balance", "full", "paid")):
        return TaskUnderstanding(
            goal="pay_remaining_balance_for_invoice",
            entities=["invoice", "payment"],
            constraints=[f"invoice_id = {invoice_id}"],
            expected_output=["invoice_number", "payment_amount", "paid_amount", "remaining_amount", "status"],
        )

    if invoice_id and ("record" in t or "payment of" in t or ("pay" in t and "payment" in t)):
        payment_amount = _extract_payment_amount(text)
        if payment_amount is not None:
            return TaskUnderstanding(
                goal="record_payment_for_invoice",
                entities=["invoice", "payment"],
                constraints=[f"invoice_id = {invoice_id}", f"payment_amount = {payment_amount}"],
                expected_output=["invoice_number", "payment_amount", "paid_amount", "remaining_amount", "status"],
            )

    if invoice_id and any(kw in t for kw in ("pending", "remaining", "balance", "still owe", "outstanding",
                                              "hasn't paid", "haven't paid", "has not paid", "have not paid",
                                              "not paid")):
        return TaskUnderstanding(
            goal="get_invoice_remaining_balance",
            entities=["invoice"],
            constraints=[f"invoice_id = {invoice_id}"],
            expected_output=["invoice_number", "remaining_amount", "status"],
        )

    if "partially paid" in t or "partially-paid" in t or "partially_paid" in t:
        return TaskUnderstanding(
            goal="list_partially_paid_invoices",
            entities=["invoice"],
            constraints=[],
            expected_output=["invoices", "count"],
        )

    if "unpaid" in t and amount is not None and ("list" in t or "find all" in t or "find" in t):
        return TaskUnderstanding(
            goal="list_unpaid_invoices_above",
            entities=["invoice"],
            constraints=[f"amount > {amount}", "status = unpaid"],
            expected_output=["invoice_number", "amount", "status"],
        )
    if "unpaid" in t and ("download" in t or "open" in t):
        return TaskUnderstanding(
            goal="download_latest_unpaid_invoice",
            entities=["invoice"],
            constraints=["status = unpaid"],
            expected_output=["invoice_number", "download_path"],
        )
    if "customer" in t and "email" in t:
        return TaskUnderstanding(
            goal="get_latest_invoice_customer_email",
            entities=["invoice", "customer"],
            constraints=[],
            expected_output=["customer_name", "customer_email"],
        )
    if ("log in" in t or "login" in t or "log into" in t) and "download" in t:
        return TaskUnderstanding(
            goal="login_and_download_latest_invoice",
            entities=["invoice"],
            constraints=[],
            expected_output=["invoice_number", "amount", "download_path"],
        )
    if "invoice" in t and ("number" in t or "amount" in t or "tell me" in t or "find" in t):
        return TaskUnderstanding(
            goal="get_latest_invoice_info",
            entities=["invoice"],
            constraints=[],
            expected_output=["invoice_number", "amount"],
        )

    # --- General "who/what is unpaid" queries. Checked LAST, after every
    # other rule above (including the amount-threshold and
    # download-latest-unpaid rules), so a phrase that already matched a
    # more specific goal never reaches here - this rule only fires on
    # unpaid-related phrasing nothing else claimed. Gated on "amount is
    # None" and "no invoice_id" as an explicit second safeguard: a
    # threshold query or a specific-invoice query should never resolve
    # here even if some future rule reordering introduced a bug.
    if amount is None and invoice_id is None and any(kw in t for kw in _GENERAL_UNPAID_QUERY_PHRASES):
        return TaskUnderstanding(
            goal="list_unpaid_invoices",
            entities=["invoice", "customer"],
            constraints=["status = unpaid"],
            expected_output=["invoice_number", "customer_name", "amount", "status"],
        )

    return TaskUnderstanding(goal="unknown", entities=[], constraints=[], expected_output=[])


def understand_task(text: str) -> tuple[TaskUnderstanding, str]:
    """Returns (understanding, produced_by) where produced_by is
    'openrouter' or 'deterministic_fallback'.

    A valid, KNOWN (non-"unknown") goal from the LLM is trusted directly -
    the deterministic matcher never overrides it. But an LLM response of
    goal="unknown" is NOT treated as the final answer: it's an "I don't
    know", not a considered "this genuinely doesn't fit any goal", so the
    deterministic matcher still gets a chance to resolve the task before
    the caller falls back to clarification. This matters because a
    correctly-formed, schema-valid "unknown" used to be returned as-is,
    even when a simple keyword rule could have recognized the task."""
    if llm_client.is_available:
        try:
            raw = llm_client.chat_json(_UNDERSTAND_SYSTEM_PROMPT, f"Task: {text}")
            candidate = TaskUnderstanding(**raw)
            if candidate.goal not in KNOWN_GOALS + ["unknown"]:
                raise ValueError(f"LLM returned unknown goal id: {candidate.goal}")
            if candidate.goal != "unknown":
                return candidate, "openrouter"
            logger.info("LLM task understanding returned 'unknown'; trying the deterministic "
                        "matcher before giving up.")
        except (LLMError, ValueError, TypeError, ValidationError) as e:
            logger.warning("LLM task understanding failed (%s); falling back to deterministic matcher.", e)

    return _deterministic_understand(text), "deterministic_fallback"


# ---------------------------------------------------------------------------
# 2. Genuine LLM-driven action plan generation
# ---------------------------------------------------------------------------

_DEMO_SITE_DESCRIPTION = """The automation target is a demo "Business Portal" web app:
- {login_url} - a login form with a "Username" textbox, a "Password" textbox, and a
  "Log In" button (role=button). A successful login redirects to /dashboard.
- /invoices - lists every invoice as a table row; each row has an "Open" link (role=link,
  name="Open") that navigates to that invoice's detail page. Rows are sorted newest-first,
  so the FIRST "Open" link corresponds to the latest invoice.
- /invoices/<id> - shows the invoice's customer name/email/date/total amount/paid
  amount/remaining amount/status, a "Download Invoice" button (role=button), and - if the
  invoice isn't fully paid - a payment form with a "Payment Amount" textbox, a "Payment
  Date" textbox, and a "Record Payment" button (role=button). You can navigate directly to
  a specific invoice's URL (e.g. /invoices/inv_1005) when the task names that invoice id.
- A JSON Invoice API also exists and should be preferred over the browser when it can
  satisfy the task directly. Available api_request goals: {api_capabilities}. The
  "record_payment" goal is a POST - pass "json_body": {{"amount": <number>, "date": "YYYY-MM-DD"}}
  and "path_params": {{"invoice_id": "inv_1005"}}. Invoice status is one of "unpaid",
  "partially_paid", or "paid"."""

_PLAN_SYSTEM_PROMPT = """You are the planning module of a browser automation agent. You propose
a concrete, ordered action plan - you do NOT execute anything yourself. Every step you
propose will be independently re-validated against the real page before it runs, and if a
step turns out to be wrong you will be asked to propose a fix with the real error in hand -
so it is fine to plan your best guess even without having seen the live page yet, but you
must never invent tool names or fields outside what's listed below.

AVAILABLE TOOLS (you may ONLY use these action names):
{tool_list}

Each step in your plan must be a JSON object shaped EXACTLY like this:
{{"step_id": <int, 1-based, sequential>,
  "action": "<one tool name from the list above>",
  "target": {{"role": "<accessibility role, e.g. button/link/textbox>", "name": "<visible text or label>"}} or null,
  "value": "<text to type, url to navigate to, or null>",
  "preconditions": ["element_exists", "element_visible", "element_enabled"],
  "expected_result": "<one short sentence>",
  "execution_method": "api" or "browser",
  "api_meta": {{"goal": "<api capability id>", "path_params": {{}}, "query_params": {{}}}} or null}}

Rules:
- "target" is required for click/type/select/download steps, and must be omitted (null) for
  navigate/wait/press/extract-without-target/inspect_page/screenshot/api_request/go_back/go_forward.
- For api_request steps, set "execution_method":"api" and fill "api_meta"; leave "target" null.
- For every other step, set "execution_method":"browser".
- Prefer api_request steps over browser steps whenever a listed API capability covers the need.
- Any task that needs to view invoices in the browser must first log in.

{demo_site_description}

{dom_context_block}

Respond with ONLY a JSON object of this shape, no markdown, no commentary:
{{"strategy": "api_first" | "browser_fallback" | "hybrid", "steps": [ ...step objects... ]}}
"""


def _tool_list_block() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in TOOL_DESCRIPTIONS.items())


def _api_capabilities_block() -> str:
    from app.services import api_router
    return ", ".join(api_router.API_CAPABILITIES.keys())


def _dom_context_block(dom_snapshot: Optional[Dict[str, Any]]) -> str:
    if not dom_snapshot:
        return ("CURRENT PAGE STATE: not available yet (planning happens before the browser "
                "opens, per this project's separation of planning and execution) - use the "
                "site description above to ground your targets, and trust that each step will "
                "be re-validated against the real page before it runs.")
    trimmed = json.dumps(dom_snapshot, indent=2)[:3000]
    return f"CURRENT REAL PAGE STATE (ground your targets in this, do not invent elements not listed here):\n{trimmed}"


def generate_action_plan(
    task_text: str,
    understanding: TaskUnderstanding,
    dom_snapshot: Optional[Dict[str, Any]] = None,
) -> tuple[Optional[ActionPlan], str]:
    """Asks the LLM to produce a genuine, ordered action plan for the task -
    not a goal classification. Returns (ActionPlan, "openrouter") on
    success. Returns (None, reason) if no key is configured, the call
    fails/rate-limits, or the response doesn't validate against
    ActionPlan/PlanStepSchema - callers MUST have a deterministic fallback
    plan for this case (see agent.py: _persist_plan). This function never
    raises."""
    if not llm_client.is_available:
        return None, "no_llm_key"

    system_prompt = _PLAN_SYSTEM_PROMPT.format(
        tool_list=_tool_list_block(),
        demo_site_description=_DEMO_SITE_DESCRIPTION.format(
            login_url=f"{settings.DEMO_SITE_BASE_URL}/login",
            api_capabilities=_api_capabilities_block(),
        ),
        dom_context_block=_dom_context_block(dom_snapshot),
    )
    user_prompt = (
        f"TASK: {task_text}\n"
        f"UNDERSTOOD GOAL: {understanding.goal}\n"
        f"CONSTRAINTS: {understanding.constraints}\n"
        f"EXPECTED OUTPUT FIELDS: {understanding.expected_output}\n"
        "Produce the action plan now."
    )
    try:
        raw = llm_client.chat_json(system_prompt, user_prompt)
        plan = ActionPlan(**raw)
        if not plan.steps:
            raise ValueError("LLM returned a plan with zero steps")
        return plan, "openrouter"
    except (LLMError, ValidationError, ValueError, TypeError) as e:
        logger.warning("LLM action-plan generation failed (%s); caller will use the deterministic "
                        "goal-template plan instead.", e)
        return None, str(e)


# ---------------------------------------------------------------------------
# 3. Genuine LLM-driven re-planning (uses the REAL DOM + REAL error)
# ---------------------------------------------------------------------------

_REPLAN_SYSTEM_PROMPT = """You are the re-planning module of a browser automation agent. A step
in the original plan just failed for real, against the real page. You are given exactly what
actually happened - the real page state and the real error - and must propose exactly ONE
next step to try, grounded ONLY in elements that are actually present in the CURRENT REAL PAGE
STATE below. Do not repeat the exact failed step. If you cannot find a safe next step from
what's actually on the page, say so instead of guessing.

AVAILABLE TOOLS (you may ONLY use these action names): {tool_list}

Respond with ONLY a JSON object, no markdown, no commentary, in ONE of these two shapes:

1) A single next step:
{{"step_id": <int>, "action": "<tool name>", "target": {{"role": "...", "name": "..."}} or null,
  "value": "<string or null>", "preconditions": ["element_exists"], "expected_result": "<short sentence>",
  "execution_method": "browser"}}

2) If no safe next step exists:
{{"give_up": true, "reason": "<short sentence explaining why>"}}

TASK: {task_text}
STEPS ALREADY ATTEMPTED THIS RUN: {history}
CURRENT REAL PAGE STATE: {dom_context}
THE REAL ERROR THAT JUST HAPPENED: {last_error}
"""


def generate_next_step(
    task_text: str,
    history: List[Dict[str, Any]],
    dom_snapshot: Optional[Dict[str, Any]],
    last_error: str,
) -> Optional[PlanStepSchema]:
    """Genuinely LLM-driven re-planning. Unlike generate_action_plan (which
    can plan blind, before the browser opens), this is only ever called
    AFTER a real failure, and is always given the REAL dom_snapshot from
    the moment of failure and the REAL error string - never a synthetic or
    cached one. Returns None if the LLM is unavailable, errors, gives up,
    or returns something that fails PlanStepSchema validation; the caller
    must have its own fallback (agent.py falls back to the deterministic
    goal handler for the whole task in that case)."""
    if not llm_client.is_available:
        return None

    trimmed_dom = json.dumps(dom_snapshot, indent=2)[:3000] if dom_snapshot else "(no page has been observed yet)"
    trimmed_history = json.dumps(history, default=str)[:1500]
    system_prompt = _REPLAN_SYSTEM_PROMPT.format(
        tool_list=", ".join(TOOL_DESCRIPTIONS.keys()),
        task_text=task_text,
        history=trimmed_history,
        dom_context=trimmed_dom,
        last_error=last_error,
    )
    try:
        raw = llm_client.chat_json(system_prompt, "What is the single safest next step, if any?")
        if raw.get("give_up"):
            logger.info("LLM re-planning gave up: %s", raw.get("reason"))
            return None
        step = PlanStepSchema(**raw)
        return step
    except (LLMError, ValidationError, ValueError, TypeError) as e:
        logger.warning("LLM re-planning failed (%s); caller will fall back.", e)
        return None
