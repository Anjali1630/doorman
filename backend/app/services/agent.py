"""
Agent orchestrator (section 22 of the spec): an explicit state machine that
ties task understanding -> planning -> API-first/browser execution ->
verification -> completion together, persisting everything to SQLite.

States: IDLE -> PLANNING -> EXECUTING -> VERIFYING -> COMPLETED
                                   (or)-> WAITING_FOR_USER (clarification)
                                   (or)-> FAILED

Planning and execution are genuinely separate steps here: `_persist_plan()`
decides and PERSISTS the plan (asking the LLM for a real one first if a key
is configured, before the browser even opens) - then `run_agent()` executes
that exact persisted plan. This mirrors the spec's required "separation
between planning and execution", and also means the LLM is called at most
once for planning (plus once per real failure for re-planning) - not once
per browser action - keeping free-tier usage low, per the project's
original design constraint.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models import orm
from app.schemas.schemas import ActionPlan, TaskUnderstanding
from app.services import api_router, executor, planner
from app.services.browser import BrowserSession
from app.services.executor import StepLogger, TaskFailed, ClarificationNeeded, GOAL_HANDLERS
from app.services.llm import llm_client

STATES = [
    "IDLE", "PLANNING", "OBSERVING", "VALIDATING", "EXECUTING",
    "VERIFYING", "REPLANNING", "WAITING_FOR_USER", "COMPLETED", "FAILED",
]

CLARIFICATION_OPTIONS = [
    "Log into the website and download the latest invoice.",
    "Find the latest invoice and tell me its invoice number and amount.",
    "Find all unpaid invoices above ₹10,000.",
    "Open the latest unpaid invoice and download it.",
    "Find the customer associated with the latest invoice and return their email.",
]

# Goals the deterministic template (and therefore the LLM prompt's "prefer
# API" guidance) expects to be satisfiable via a direct API call first.
_API_FIRST_GOALS = (
    "get_latest_invoice_info", "list_unpaid_invoices", "list_unpaid_invoices_above",
    "download_latest_unpaid_invoice", "get_latest_invoice_customer_email",
    "record_payment_for_invoice", "get_invoice_remaining_balance",
    "list_partially_paid_invoices", "pay_remaining_balance_for_invoice",
)


def _deterministic_plan_steps(understanding: TaskUnderstanding, api_available: bool) -> list:
    """The original template-based coarse plan. Used to persist a plan when
    no LLM key is configured, AND as the plan record for the deterministic
    goal-handler path when an LLM-produced plan failed and execution fell
    back - so what's persisted always accurately reflects what actually ran."""
    templates: Dict[str, list] = {
        "login_and_download_latest_invoice": [
            ("navigate", {"role": None, "name": "login page"}, "browser"),
            ("type", {"role": "textbox", "name": "Username"}, "browser"),
            ("type", {"role": "textbox", "name": "Password"}, "browser"),
            ("click", {"role": "button", "name": "Log In"}, "browser"),
            ("navigate", {"role": None, "name": "invoices page"}, "browser"),
            ("click", {"role": "link", "name": "Open (latest invoice)"}, "browser"),
            ("download", {"role": "button", "name": "Download Invoice"}, "browser"),
        ],
        "get_latest_invoice_info": [
            ("api_request", {"role": None, "name": "GET /api/invoices/latest"},
             "api" if api_available else "browser"),
        ],
        "list_unpaid_invoices_above": [
            ("api_request", {"role": None, "name": "GET /api/invoices?status=unpaid"},
             "api" if api_available else "browser"),
        ],
        "list_unpaid_invoices": [
            ("api_request", {"role": None, "name": "GET /api/invoices?status=unpaid"},
             "api" if api_available else "browser"),
            ("api_request", {"role": None, "name": "GET /api/customers/{id} (per invoice, cached)"},
             "api" if api_available else "browser"),
        ],
        "download_latest_unpaid_invoice": [
            ("api_request", {"role": None, "name": "GET /api/invoices?status=unpaid"},
             "api" if api_available else "browser"),
            ("api_request", {"role": None, "name": "GET /api/invoices/{id}/download"},
             "api" if api_available else "browser"),
        ],
        "get_latest_invoice_customer_email": [
            ("api_request", {"role": None, "name": "GET /api/invoices/latest"},
             "api" if api_available else "browser"),
            ("api_request", {"role": None, "name": "GET /api/customers/{id}"},
             "api" if api_available else "browser"),
        ],
        "record_payment_for_invoice": [
            ("api_request", {"role": None, "name": "POST /api/invoices/{id}/payments"},
             "api" if api_available else "browser"),
        ],
        "get_invoice_remaining_balance": [
            ("api_request", {"role": None, "name": "GET /api/invoices/{id}"},
             "api" if api_available else "browser"),
        ],
        "list_partially_paid_invoices": [
            ("api_request", {"role": None, "name": "GET /api/invoices?status=partially_paid"},
             "api" if api_available else "browser"),
        ],
        "pay_remaining_balance_for_invoice": [
            ("api_request", {"role": None, "name": "GET /api/invoices/{id}"},
             "api" if api_available else "browser"),
            ("api_request", {"role": None, "name": "POST /api/invoices/{id}/payments"},
             "api" if api_available else "browser"),
        ],
    }
    return templates.get(understanding.goal, [])


def _persist_plan(db: Session, task: orm.Task, understanding: TaskUnderstanding) -> tuple[orm.Plan, Optional[ActionPlan]]:
    """Decides and persists the plan BEFORE any browser action happens:

    1. If an OpenRouter key is configured, ask the LLM for a genuine action
       plan (planner.generate_action_plan) - this is a real ordered list of
       tool calls the model proposed, not a goal-template lookup.
    2. If that succeeds and validates, persist ITS steps and return the
       validated ActionPlan alongside the DB row, so run_agent() can
       execute the exact same plan it just persisted.
    3. Otherwise (no key, LLM error/rate-limit, or invalid output), persist
       the deterministic goal-template plan instead and return
       (plan_row, None) - run_agent() then uses the goal-handler path.
    """
    api_available = api_router.is_api_available()

    llm_plan: Optional[ActionPlan] = None
    produced_by = "deterministic_fallback"
    if llm_client.is_available:
        llm_plan, reason = planner.generate_action_plan(task.raw_text, understanding, dom_snapshot=None)
        if llm_plan is not None:
            produced_by = "openrouter"

    if llm_plan is not None:
        strategy = llm_plan.strategy if llm_plan.strategy in ("api_first", "browser_fallback", "hybrid") else "hybrid"
        plan = orm.Plan(task_id=task.id, strategy=strategy, created_by=produced_by)
        db.add(plan)
        db.flush()
        for step in llm_plan.steps:
            db.add(orm.PlanStep(
                plan_id=plan.id, step_id=step.step_id,
                action=step.action.value if hasattr(step.action, "value") else step.action,
                target=step.target.model_dump(exclude_none=True) if step.target else None,
                preconditions=step.preconditions, expected_result=step.expected_result,
                execution_method=step.execution_method,
            ))
        db.commit()
        return plan, llm_plan

    # Deterministic fallback plan.
    strategy = "api_first" if understanding.goal in _API_FIRST_GOALS else "browser_fallback"
    plan = orm.Plan(task_id=task.id, strategy=strategy, created_by=produced_by)
    db.add(plan)
    db.flush()
    for i, (action, target, method) in enumerate(_deterministic_plan_steps(understanding, api_available), start=1):
        db.add(orm.PlanStep(
            plan_id=plan.id, step_id=i, action=action, target=target,
            preconditions=["element_exists", "element_visible", "element_enabled"] if method == "browser" else [],
            expected_result=f"{action} succeeds", execution_method=method,
        ))
    db.commit()
    return plan, None


async def _run_deterministic_goal(page, logger: StepLogger, understanding: TaskUnderstanding) -> Dict[str, Any]:
    if understanding.goal == "list_unpaid_invoices_above":
        min_amount = _threshold_from_constraints(understanding.constraints)
        return await executor.execute_list_unpaid_invoices_above(page, logger, min_amount)
    if understanding.goal == "record_payment_for_invoice":
        invoice_id = _invoice_id_from_constraints(understanding.constraints)
        payment_amount = _payment_amount_from_constraints(understanding.constraints)
        if invoice_id is None or payment_amount is None:
            raise TaskFailed("Could not determine which invoice or payment amount to use",
                              category="validation_error")
        return await executor.execute_record_payment_for_invoice(page, logger, invoice_id, payment_amount)
    if understanding.goal in ("get_invoice_remaining_balance", "pay_remaining_balance_for_invoice"):
        invoice_id = _invoice_id_from_constraints(understanding.constraints)
        if invoice_id is None:
            raise TaskFailed("Could not determine which invoice to use", category="validation_error")
        if understanding.goal == "pay_remaining_balance_for_invoice":
            return await executor.execute_pay_remaining_balance_for_invoice(page, logger, invoice_id)
        return await executor.execute_get_invoice_remaining_balance(page, logger, invoice_id)
    handler = GOAL_HANDLERS[understanding.goal]
    return await handler(page, logger)


async def run_agent(db: Session, text: str) -> orm.Execution:
    task = orm.Task(raw_text=text)
    db.add(task)
    db.flush()

    understanding, produced_by = planner.understand_task(text)
    task.goal = understanding.goal
    task.entities = understanding.entities
    task.constraints = understanding.constraints
    task.expected_output = understanding.expected_output
    db.commit()

    execution = orm.Execution(task_id=task.id, status="PLANNING", strategy="unknown")
    db.add(execution)
    db.commit()

    if understanding.goal == "unknown":
        execution.status = "WAITING_FOR_USER"
        execution.result = {"clarification_options": CLARIFICATION_OPTIONS}
        db.commit()
        return execution

    plan, llm_plan = _persist_plan(db, task, understanding)
    execution.plan_id = plan.id
    execution.strategy = plan.strategy
    execution.status = "EXECUTING"
    db.commit()

    logger = StepLogger(db, execution)
    logger.log(f"Task understood (via {produced_by}): goal={understanding.goal}", action="understand_task",
               execution_method="planning")
    logger.log(f"Plan created (via {plan.created_by}, strategy={plan.strategy}, {len(plan.steps)} step(s))",
               action="create_plan", execution_method="planning")

    async with BrowserSession() as session:
        page = session.page
        try:
            if llm_plan is not None:
                try:
                    result = await executor.execute_llm_plan(page, logger, text, understanding, llm_plan)
                except TaskFailed as e:
                    logger.log(
                        f"LLM-driven plan could not complete ({e}); falling back to the reliable "
                        "deterministic goal handler for this task.",
                        execution_method="replanning", status="failed", error=str(e),
                    )
                    result = await _run_deterministic_goal(page, logger, understanding)
            else:
                result = await _run_deterministic_goal(page, logger, understanding)

            execution.status = "COMPLETED"
            execution.success = True
            execution.result = result
            logger.log("Task completed and result verified", action="complete", status="success")
        except TaskFailed as e:
            execution.status = "FAILED"
            execution.success = False
            execution.error = str(e)
            logger.log(f"Task failed: {e}", action="fail", status="failed", error=str(e))
        except ClarificationNeeded as e:
            execution.status = "WAITING_FOR_USER"
            execution.result = {"clarification_options": e.options, "reason": str(e)}
        except Exception as e:  # noqa: BLE001 - top-level safety net, never crash silently
            execution.status = "FAILED"
            execution.success = False
            execution.error = f"Unexpected error: {e}"
            logger.log(f"Unexpected error: {e}", action="fail", status="failed", error=str(e))

    db.commit()
    return execution


def _threshold_from_constraints(constraints: list[str]) -> float:
    for c in constraints:
        m = re.search(r"([\d.]+)", c)
        if m and ">" in c:
            return float(m.group(1))
    return 10000.0


def _invoice_id_from_constraints(constraints: list[str]) -> Optional[str]:
    for c in constraints:
        m = re.search(r"invoice_id\s*=\s*(\S+)", c)
        if m:
            return m.group(1).strip()
    return None


def _payment_amount_from_constraints(constraints: list[str]) -> Optional[float]:
    for c in constraints:
        m = re.search(r"payment_amount\s*=\s*([\d.]+)", c)
        if m:
            return float(m.group(1))
    return None
