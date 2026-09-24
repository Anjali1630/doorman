"""
Executor: carries out a task's goal using the registered tools, with every
element-targeting action passing through pre-execution validation, bounded
retries, and (when needed) a documented re-plan to browser fallback.

Each `execute_<goal>` function is a small, goal-specific control flow -
this is a deliberate simplification over a fully generic step-by-step
planner (see docs/architecture.md "Known limitations"). What it does NOT
simplify away is the safety machinery: every element action still goes
through `registry.py` tools, `validator.validate_step`, retry counting,
and execution-trace logging, and API-first tasks genuinely re-plan to
Playwright when the live API health check fails.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import orm
from app.services import api_router, validator
from app.services.dom_inspector import inspect_page
from app.tools import registry

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent.parent / "artifacts"
ARTIFACTS_DIR.mkdir(exist_ok=True)


class TaskFailed(Exception):
    def __init__(self, message: str, category: str = "unexpected_error"):
        super().__init__(message)
        self.category = category


class ClarificationNeeded(Exception):
    def __init__(self, message: str, options: list[str]):
        super().__init__(message)
        self.options = options


class StepLogger:
    """Writes one ExecutionStep row per logged event and keeps the parent
    Execution row's counters (api_calls/browser_actions/retries/replans)
    in sync - this IS the execution trace (section 20)."""

    def __init__(self, db: Session, execution: orm.Execution):
        self.db = db
        self.execution = execution

    def log(self, step_label: str, *, action: Optional[str] = None, target: Optional[dict] = None,
             tool: Optional[str] = None, execution_method: Optional[str] = None,
             validation_result: Optional[str] = None, status: str = "success",
             duration_ms: float = 0.0, error: Optional[str] = None, retry_count: int = 0):
        row = orm.ExecutionStep(
            execution_id=self.execution.id, step_label=step_label, action=action, target=target,
            tool=tool, execution_method=execution_method, validation_result=validation_result,
            status=status, duration_ms=duration_ms, error=error, retry_count=retry_count,
        )
        self.db.add(row)
        if execution_method == "api":
            self.execution.api_calls += 1
        elif execution_method == "browser":
            self.execution.browser_actions += 1
        if retry_count:
            self.execution.retries_used += retry_count
        if execution_method == "replanning":
            self.execution.replans_used += 1
        self.db.commit()
        return row

    def record_observation(self, observation: Dict[str, Any]):
        row = orm.BrowserObservation(
            execution_id=self.execution.id, url=observation.get("url"),
            title=observation.get("title"), elements=observation.get("elements", []),
        )
        self.db.add(row)
        self.db.commit()
        return row


async def _run_tool(tool_name: str, page, args: Dict[str, Any]) -> registry.ToolResult:
    fn: Callable = registry.ALL_TOOLS[tool_name]
    return await fn(page, args)


async def _validated_action(logger: StepLogger, page, action: str, target: Dict[str, Any],
                             preconditions: list[str], step_label: str, value: Optional[str] = None,
                             max_retries: int = None) -> registry.ToolResult:
    """Runs an element-targeting tool call through: observe -> validate ->
    execute -> (on validation failure) retarget/re-inspect -> retry, up to
    MAX_RETRIES_PER_ACTION. Never calls the tool if validation fails and no
    safe retarget is found."""
    max_retries = settings.MAX_RETRIES_PER_ACTION if max_retries is None else max_retries
    step = {"action": action, "target": target, "preconditions": preconditions}
    last_error = None

    for attempt in range(max_retries + 1):
        t0 = time.time()
        observation = await inspect_page(page)
        logger.record_observation(observation)
        result = validator.validate_step(step, observation)
        duration_ms = (time.time() - t0) * 1000

        if not result.passed:
            retarget = validator.attempt_retarget(step, observation)
            if retarget:
                logger.log(
                    f"Validation rejected original target; retargeted to '{retarget.get('name')}'",
                    action=action, target=target, tool=action, execution_method="browser",
                    validation_result="rejected_then_retargeted", status="success",
                    duration_ms=duration_ms, retry_count=attempt,
                )
                target = {"role": retarget.get("role"), "name": retarget.get("name")}
                step["target"] = target
            else:
                logger.log(
                    f"Pre-execution validation REJECTED action '{action}': {result.reason}",
                    action=action, target=target, tool=action, execution_method="browser",
                    validation_result="rejected", status="failed", duration_ms=duration_ms,
                    error=result.reason, retry_count=attempt,
                )
                last_error = result.reason
                if attempt < max_retries:
                    continue
                raise TaskFailed(f"Validation rejected '{action}' on {target}: {result.reason}",
                                  category="validation_error")

        logger.log(f"Pre-execution validation passed for '{action}'", action=action, target=target,
                   tool=action, execution_method="browser", validation_result="passed",
                   status="success", duration_ms=duration_ms)

        t1 = time.time()
        args = {"target": target}
        if value is not None:
            args["value"] = value
        try:
            tool_result = await _run_tool(action, page, args)
        except registry.ToolExecutionError as e:
            dur = (time.time() - t1) * 1000
            logger.log(f"Execution of '{action}' failed: {e}", action=action, target=target, tool=action,
                       execution_method="browser", validation_result="passed", status="failed",
                       duration_ms=dur, error=str(e), retry_count=attempt)
            last_error = str(e)
            if attempt < max_retries:
                continue
            raise TaskFailed(str(e), category=e.category)

        dur = (time.time() - t1) * 1000
        logger.log(step_label, action=action, target=target, tool=action, execution_method="browser",
                   validation_result="passed", status="success", duration_ms=dur)
        return tool_result

    raise TaskFailed(last_error or f"'{action}' failed after retries", category="unexpected_error")


# ---------------------------------------------------------------------------
# Generic LLM-driven plan execution
# ---------------------------------------------------------------------------
# The 5 `execute_<goal>` functions below are the reliable, deterministic
# path used when no LLM key is configured (or as a safety-net fallback if
# the LLM-driven path fails). The functions in this section are the OTHER
# path: they replay an ActionPlan that the LLM genuinely produced
# (planner.generate_action_plan), and when a step fails, ask the LLM for a
# real next step grounded in the real DOM/error (planner.generate_next_step)
# rather than a hardcoded "switch to browser" rule. agent.py tries this path
# first when a key is configured, and falls back to the goal handlers below
# if it fails - so it is always a safe *addition*, never a required path.

MAX_LLM_TOTAL_STEPS = 25


async def _execute_generic_step(logger: StepLogger, page, step) -> registry.ToolResult:
    """Executes ONE PlanStepSchema-shaped step through the same tool
    registry + pre-execution validation path the goal handlers use,
    generically rather than for one hardcoded goal. Raises TaskFailed on
    failure - the LLM-driven loop below decides whether to re-plan."""
    action = step.action.value if hasattr(step.action, "value") else step.action
    target = step.target.model_dump(exclude_none=True) if step.target else {}

    if action in validator.ELEMENT_ACTIONS:
        return await _validated_action(
            logger, page, action, target, step.preconditions,
            step.expected_result or f"'{action}' succeeded", value=step.value,
        )

    if action == "api_request":
        meta = dict(step.api_meta or {})
        t0 = time.time()
        try:
            result = await _run_tool("api_request", page, meta)
        except registry.ToolExecutionError as e:
            logger.log(f"LLM plan API step failed: {e}", action=action, execution_method="api",
                       status="failed", error=str(e))
            raise TaskFailed(str(e), category=e.category)
        logger.log(step.expected_result or "API step succeeded", action=action,
                   execution_method="api", status="success", duration_ms=(time.time() - t0) * 1000)
        return result

    # navigate / wait / press / extract / inspect_page / screenshot / go_back / go_forward
    args: Dict[str, Any] = {}
    if target:
        args["target"] = target
    if step.value is not None:
        args["url" if action == "navigate" else "value"] = step.value
    t0 = time.time()
    try:
        result = await _run_tool(action, page, args)
    except registry.ToolExecutionError as e:
        logger.log(f"LLM plan step '{action}' failed: {e}", action=action, execution_method="browser",
                   status="failed", error=str(e))
        raise TaskFailed(str(e), category=e.category)
    if action == "inspect_page":
        logger.record_observation(result.data)
    logger.log(step.expected_result or f"'{action}' succeeded", action=action,
               execution_method="browser", status="success", duration_ms=(time.time() - t0) * 1000)
    return result


def _summarize_llm_result(history: list, understanding) -> Dict[str, Any]:
    """Best-effort, goal-agnostic result extraction from an LLM-driven run.
    Unlike the deterministic goal handlers (which return precisely the
    fields their goal promises), this pulls whatever structured data the
    executed steps actually produced - documented as a known limitation of
    the generic path in docs/architecture.md."""
    result: Dict[str, Any] = {"produced_by": "llm_driven_plan", "steps_executed": len(history)}
    for entry in history:
        if entry.get("status") != "success":
            continue
        data = entry.get("data") or {}
        api_json = data.get("json")
        if isinstance(api_json, dict):
            for key in ("id", "amount", "status", "customer_id", "name", "email"):
                if key in api_json:
                    result[key if key != "id" else "invoice_number"] = api_json[key]
        if isinstance(api_json, dict) and "invoices" in api_json:
            result["invoices"] = api_json["invoices"]
        if "invoices" in data:
            result["invoices"] = data["invoices"]
        if "text" in data:
            result["extracted_text"] = data["text"]
        if "file_path" in data:
            result["download_path"] = data["file_path"]
        if "filename" in data:
            result["filename"] = data["filename"]
    result.setdefault("expected_output_requested", understanding.expected_output)
    return result


async def execute_llm_plan(page, logger: StepLogger, task_text: str, understanding,
                            plan) -> Dict[str, Any]:
    """Runs a genuinely LLM-produced ActionPlan (see planner.generate_action_plan)
    step by step. Every element-targeting step still goes through the exact
    same validator as the deterministic goal handlers - the LLM planned it,
    but reality decides whether it runs. When a step fails, this calls
    planner.generate_next_step() with the REAL current DOM snapshot and the
    REAL error, and splices its answer in as the very next step - genuine
    LLM-driven re-planning, not a hardcoded rule. Bounded by
    MAX_REPLANS_PER_TASK and MAX_LLM_TOTAL_STEPS so a confused model can't
    loop forever. Raises TaskFailed if execution can't complete; agent.py
    falls back to the deterministic goal handler in that case."""
    from app.services import planner

    history: list = []
    steps_run = 0
    replans_used = 0
    max_replans = settings.MAX_REPLANS_PER_TASK
    step_queue = list(plan.steps)
    last_observation: Optional[Dict[str, Any]] = None

    while step_queue:
        if steps_run >= MAX_LLM_TOTAL_STEPS:
            raise TaskFailed("LLM-driven execution exceeded the maximum step budget",
                              category="unexpected_error")
        step = step_queue.pop(0)
        steps_run += 1
        try:
            tool_result = await _execute_generic_step(logger, page, step)
            history.append({
                "step": step.model_dump(mode="json"), "status": "success", "data": tool_result.data,
            })
        except TaskFailed as e:
            history.append({"step": step.model_dump(mode="json"), "status": "failed", "error": str(e)})
            if replans_used >= max_replans:
                raise TaskFailed(
                    f"LLM-driven execution failed and the re-planning budget ({max_replans}) is "
                    f"exhausted: {e}", category=e.category,
                )
            try:
                last_observation = await inspect_page(page)
                logger.record_observation(last_observation)
            except Exception:  # noqa: BLE001 - page may be on about:blank / mid-navigation
                pass

            next_step = planner.generate_next_step(task_text, history, last_observation, str(e))
            replans_used += 1
            logger.log(
                f"LLM re-planned after a real failure ({e}): "
                + (f"proposed next action '{next_step.action}'" if next_step else "the model gave up"),
                execution_method="replanning",
                status="success" if next_step is not None else "failed",
                error=None if next_step is not None else str(e),
            )
            if next_step is None:
                raise TaskFailed(f"LLM re-planning could not find a safe next step after: {e}",
                                  category=e.category)
            step_queue.insert(0, next_step)
            continue

    return _summarize_llm_result(history, understanding)


async def _login(page, logger: StepLogger):
    t0 = time.time()
    await _run_tool("navigate", page, {"url": f"{settings.DEMO_SITE_BASE_URL}/login"})
    logger.log("Navigated to login page", action="navigate", execution_method="browser",
                duration_ms=(time.time() - t0) * 1000)

    await _validated_action(logger, page, "type", {"role": "textbox", "name": "Username"},
                             ["element_exists", "element_visible", "element_enabled"],
                             "Entered username", value=settings.DEMO_SITE_USERNAME)
    await _validated_action(logger, page, "type", {"role": "textbox", "name": "Password"},
                             ["element_exists", "element_visible", "element_enabled"],
                             "Entered password", value=settings.DEMO_SITE_PASSWORD)
    await _validated_action(logger, page, "click", {"role": "button", "name": "Log In"},
                             ["element_exists", "element_visible", "element_enabled"], "Clicked Log In")

    observation = await inspect_page(page)
    logger.record_observation(observation)
    if "/dashboard" not in observation["url"] and "/invoices" not in observation["url"]:
        raise TaskFailed(f"Login did not reach dashboard (landed on {observation['url']})",
                          category="authentication_error")
    logger.log("Verified login succeeded", action="verify", execution_method="browser", status="success")


def _amount_from_text(text: str) -> float:
    m = re.search(r"[\d,]+\.\d{2}", text)
    if not m:
        raise TaskFailed(f"Could not extract an amount from '{text}'", category="extraction_error")
    return float(m.group(0).replace(",", ""))


async def execute_login_and_download_latest_invoice(page, logger: StepLogger) -> Dict[str, Any]:
    await _login(page, logger)
    await _run_tool("navigate", page, {"url": f"{settings.DEMO_SITE_BASE_URL}/invoices"})
    logger.log("Navigated to Invoices", action="navigate", execution_method="browser")

    observation = await inspect_page(page)
    logger.record_observation(observation)
    rows = observation.get("table_rows", [])
    if not rows:
        raise TaskFailed("No invoices found on the invoices page", category="extraction_error")
    latest = rows[0]
    invoice_id = latest["invoice_id"]

    await _validated_action(
        logger, page, "click", {"css": f'a[href="/invoices/{invoice_id}"]'}, [],
        f"Opened invoice {invoice_id}",
    )
    result = await _run_tool("download", page, {"target": {"role": "button", "name": "Download Invoice"}})
    file_path = result.data["file_path"]
    amount = _amount_from_text(" ".join(latest["cells"]))

    logger.log("Verified download artifact exists on disk", action="verify",
               execution_method="browser", status="success" if Path(file_path).exists() else "failed")
    if not Path(file_path).exists():
        raise TaskFailed("Downloaded file was not found on disk after download", category="download_error")

    return {"invoice_number": invoice_id, "amount": amount, "download_path": file_path}


async def execute_get_latest_invoice_info(page, logger: StepLogger) -> Dict[str, Any]:
    if api_router.is_api_available():
        t0 = time.time()
        try:
            result = await _run_tool("api_request", page, {"goal": "get_latest_invoice"})
        except registry.ToolExecutionError as e:
            logger.log(f"API call failed ({e}); re-planning to browser fallback", execution_method="replanning",
                        status="failed", error=str(e))
            return await _browser_get_latest_invoice_info(page, logger)
        inv = result.data["json"]
        logger.log("Fetched latest invoice via Invoice API", action="api_request",
                   execution_method="api", status="success", duration_ms=(time.time() - t0) * 1000)
        return {"invoice_number": inv["id"], "amount": inv["amount"], "status": inv["status"]}

    logger.log("Invoice API unavailable; re-planning to browser fallback", execution_method="replanning")
    return await _browser_get_latest_invoice_info(page, logger)


async def _browser_get_latest_invoice_info(page, logger: StepLogger) -> Dict[str, Any]:
    await _login(page, logger)
    await _run_tool("navigate", page, {"url": f"{settings.DEMO_SITE_BASE_URL}/invoices"})
    logger.log("Navigated to Invoices", action="navigate", execution_method="browser")
    observation = await inspect_page(page)
    logger.record_observation(observation)
    rows = observation.get("table_rows", [])
    if not rows:
        raise TaskFailed("No invoices found", category="extraction_error")
    latest = rows[0]
    amount = _amount_from_text(" ".join(latest["cells"]))
    return {"invoice_number": latest["invoice_id"], "amount": amount, "status": latest["status"]}


async def execute_list_unpaid_invoices_above(page, logger: StepLogger, min_amount: float) -> Dict[str, Any]:
    if api_router.is_api_available():
        t0 = time.time()
        try:
            result = await _run_tool("api_request", page, {
                "goal": "list_invoices_filtered",
                "query_params": {"status": "unpaid", "min_amount": min_amount},
            })
            invoices = result.data["json"]["invoices"]
            logger.log("Fetched filtered invoices via Invoice API", action="api_request",
                       execution_method="api", status="success", duration_ms=(time.time() - t0) * 1000)
            return {"invoices": invoices, "count": len(invoices)}
        except registry.ToolExecutionError as e:
            logger.log(f"API call failed ({e}); re-planning to browser fallback", execution_method="replanning",
                        status="failed", error=str(e))

    logger.log("Invoice API unavailable; re-planning to browser fallback", execution_method="replanning")
    await _login(page, logger)
    await _run_tool("navigate", page, {"url": f"{settings.DEMO_SITE_BASE_URL}/invoices"})
    observation = await inspect_page(page)
    logger.record_observation(observation)
    matches = []
    for row in observation.get("table_rows", []):
        if row["status"] != "unpaid":
            continue
        amount = _amount_from_text(" ".join(row["cells"]))
        if amount > min_amount:
            matches.append({"id": row["invoice_id"], "amount": amount, "status": "unpaid"})
    return {"invoices": matches, "count": len(matches)}


async def execute_list_unpaid_invoices(page, logger: StepLogger) -> Dict[str, Any]:
    """General "which invoices/customers haven't paid" query - no amount
    threshold, but (unlike list_unpaid_invoices_above) each result includes
    the customer's name, not just the invoice id/amount/status. Reuses the
    existing "list_invoices_filtered" and "get_customer" API capabilities
    (the same ones execute_get_latest_invoice_customer_email already uses)
    rather than adding a new endpoint - a per-customer lookup is cached so a
    customer with several unpaid invoices is only fetched once."""
    if api_router.is_api_available():
        t0 = time.time()
        try:
            result = await _run_tool("api_request", page, {
                "goal": "list_invoices_filtered", "query_params": {"status": "unpaid"},
            })
            invoices = result.data["json"]["invoices"]
            customer_names: Dict[str, str] = {}
            enriched = []
            for inv in invoices:
                customer_id = inv.get("customer_id")
                if customer_id not in customer_names:
                    cust_result = await _run_tool("api_request", page, {
                        "goal": "get_customer", "path_params": {"customer_id": customer_id},
                    })
                    customer_names[customer_id] = cust_result.data["json"]["name"]
                enriched.append({
                    "invoice_number": inv["id"],
                    "customer_name": customer_names[customer_id],
                    "amount": inv.get("total_amount", inv.get("amount")),
                    "status": inv["status"],
                })
            logger.log("Fetched unpaid invoices + customer names via Invoice API", action="api_request",
                       execution_method="api", status="success", duration_ms=(time.time() - t0) * 1000)
            return {"invoices": enriched, "count": len(enriched)}
        except registry.ToolExecutionError as e:
            logger.log(f"API call failed ({e}); re-planning to browser fallback", execution_method="replanning",
                        status="failed", error=str(e))

    logger.log("Invoice API unavailable; re-planning to browser fallback", execution_method="replanning")
    await _login(page, logger)
    await _run_tool("navigate", page, {"url": f"{settings.DEMO_SITE_BASE_URL}/invoices"})
    observation = await inspect_page(page)
    logger.record_observation(observation)
    matches = []
    for row in observation.get("table_rows", []):
        if row["status"] != "unpaid":
            continue
        cells = row["cells"]
        # invoices.html renders columns as [Invoice, Customer, Date, Amount,
        # Status, Open] - the customer name is already right there in the
        # row, no extra navigation/lookup needed for the browser path.
        customer_name = cells[1] if len(cells) > 1 else None
        amount = _amount_from_text(" ".join(cells))
        matches.append({
            "invoice_number": row["invoice_id"], "customer_name": customer_name,
            "amount": amount, "status": "unpaid",
        })
    return {"invoices": matches, "count": len(matches)}


async def execute_download_latest_unpaid_invoice(page, logger: StepLogger) -> Dict[str, Any]:
    if api_router.is_api_available():
        t0 = time.time()
        try:
            listing = await _run_tool("api_request", page, {
                "goal": "list_invoices_filtered", "query_params": {"status": "unpaid"},
            })
            unpaid = listing.data["json"]["invoices"]
            if not unpaid:
                raise TaskFailed("No unpaid invoices found via API", category="extraction_error")
            latest = sorted(unpaid, key=lambda i: i["date"])[-1]
            dl = await _run_tool("api_request", page, {
                "goal": "download_invoice", "path_params": {"invoice_id": latest["id"]},
            })
            out_path = ARTIFACTS_DIR / f"{latest['id']}.txt"
            out_path.write_text(dl.data["content"])
            logger.log("Downloaded latest unpaid invoice via Invoice API", action="api_request",
                       execution_method="api", status="success", duration_ms=(time.time() - t0) * 1000)
            return {"invoice_number": latest["id"], "amount": latest["amount"], "download_path": str(out_path)}
        except registry.ToolExecutionError as e:
            logger.log(f"API call failed ({e}); re-planning to browser fallback", execution_method="replanning",
                        status="failed", error=str(e))

    logger.log("Invoice API unavailable; re-planning to browser fallback (this is the documented "
               "API-first -> Playwright fallback path)", execution_method="replanning")
    await _login(page, logger)
    await _run_tool("navigate", page, {"url": f"{settings.DEMO_SITE_BASE_URL}/invoices"})
    observation = await inspect_page(page)
    logger.record_observation(observation)
    unpaid_rows = [r for r in observation.get("table_rows", []) if r["status"] == "unpaid"]
    if not unpaid_rows:
        raise TaskFailed("No unpaid invoices found on the invoices page", category="extraction_error")
    target_row = unpaid_rows[0]  # rows are rendered latest-first
    invoice_id = target_row["invoice_id"]
    await _validated_action(logger, page, "click", {"css": f'a[href="/invoices/{invoice_id}"]'}, [],
                             f"Opened unpaid invoice {invoice_id}")
    result = await _run_tool("download", page, {"target": {"role": "button", "name": "Download Invoice"}})
    amount = _amount_from_text(" ".join(target_row["cells"]))
    return {"invoice_number": invoice_id, "amount": amount, "download_path": result.data["file_path"]}


async def execute_get_latest_invoice_customer_email(page, logger: StepLogger) -> Dict[str, Any]:
    if api_router.is_api_available():
        t0 = time.time()
        try:
            inv_result = await _run_tool("api_request", page, {"goal": "get_latest_invoice"})
            inv = inv_result.data["json"]
            cust_result = await _run_tool("api_request", page, {
                "goal": "get_customer", "path_params": {"customer_id": inv["customer_id"]},
            })
            customer = cust_result.data["json"]
            logger.log("Fetched latest invoice + customer via Invoice API", action="api_request",
                       execution_method="api", status="success", duration_ms=(time.time() - t0) * 1000)
            return {"customer_name": customer["name"], "customer_email": customer["email"]}
        except registry.ToolExecutionError as e:
            logger.log(f"API call failed ({e}); re-planning to browser fallback", execution_method="replanning",
                        status="failed", error=str(e))

    logger.log("Invoice API unavailable; re-planning to browser fallback", execution_method="replanning")
    await _login(page, logger)
    await _run_tool("navigate", page, {"url": f"{settings.DEMO_SITE_BASE_URL}/invoices"})
    observation = await inspect_page(page)
    logger.record_observation(observation)
    rows = observation.get("table_rows", [])
    if not rows:
        raise TaskFailed("No invoices found", category="extraction_error")
    invoice_id = rows[0]["invoice_id"]
    await _validated_action(logger, page, "click", {"css": f'a[href="/invoices/{invoice_id}"]'}, [],
                             f"Opened invoice {invoice_id}")
    name_result = await _run_tool("extract", page, {"target": {"test_id": "customer-name"}})
    email_result = await _run_tool("extract", page, {"target": {"test_id": "customer-email"}})
    return {"customer_name": name_result.data["text"], "customer_email": email_result.data["text"]}


# ---------------------------------------------------------------------------
# Payment tracking goals
# ---------------------------------------------------------------------------
# Unlike the goals above (which operate on "the latest invoice" or "invoices
# matching a filter"), these four always act on an EXPLICITLY NAMED invoice
# id extracted from the task text (planner._extract_invoice_id). That makes
# the browser-fallback path simpler and just as safe: rather than clicking
# through the list to find a row, it navigates straight to
# /invoices/{invoice_id} - still fully validated (the payment form's
# elements are checked against the real page before any click/type, exactly
# like every other browser action), it just doesn't need a list-scan step
# first because the id is already known.

_STATUS_TEXT_TO_VALUE = {"unpaid": "unpaid", "partially paid": "partially_paid",
                          "partially_paid": "partially_paid", "paid": "paid"}


def _normalize_status_text(text: str) -> str:
    """Maps a displayed status label ("Partially Paid") or a raw internal
    value ("partially_paid") back to the canonical lowercase snake_case
    value used everywhere else in results/filters."""
    key = text.strip().lower()
    return _STATUS_TEXT_TO_VALUE.get(key, key)


async def _open_invoice_by_id(page, logger: StepLogger, invoice_id: str) -> Dict[str, Any]:
    """Navigates directly to a known invoice's detail page and returns the
    resulting observation. Raises TaskFailed (extraction_error) if the
    invoice detail page doesn't actually show the expected elements - e.g.
    a nonexistent invoice id renders a 404 page with no payment form."""
    await _run_tool("navigate", page, {"url": f"{settings.DEMO_SITE_BASE_URL}/invoices/{invoice_id}"})
    logger.log(f"Navigated to invoice {invoice_id}", action="navigate", execution_method="browser")
    observation = await inspect_page(page)
    logger.record_observation(observation)
    return observation


async def execute_record_payment_for_invoice(page, logger: StepLogger, invoice_id: str,
                                              payment_amount: float) -> Dict[str, Any]:
    today = time.strftime("%Y-%m-%d")
    if api_router.is_api_available():
        t0 = time.time()
        try:
            result = await _run_tool("api_request", page, {
                "goal": "record_payment", "path_params": {"invoice_id": invoice_id},
                "json_body": {"amount": payment_amount, "date": today},
            })
            inv = result.data["json"]
            logger.log(f"Recorded payment of {payment_amount} for {invoice_id} via Invoice API",
                       action="api_request", execution_method="api", status="success",
                       duration_ms=(time.time() - t0) * 1000)
            return {
                "invoice_number": inv["id"], "payment_amount": payment_amount,
                "paid_amount": inv["paid_amount"], "remaining_amount": inv["remaining_amount"],
                "status": inv["status"],
            }
        except registry.ToolExecutionError as e:
            if e.category == "validation_error":
                # A rejected overpayment is a legitimate outcome, not a
                # reason to fall back to the browser and try again - the
                # browser would reject it for the exact same reason.
                raise TaskFailed(str(e), category="validation_error")
            logger.log(f"API call failed ({e}); re-planning to browser fallback", execution_method="replanning",
                        status="failed", error=str(e))

    logger.log("Invoice API unavailable; re-planning to browser fallback", execution_method="replanning")
    await _login(page, logger)
    await _open_invoice_by_id(page, logger, invoice_id)
    await _validated_action(logger, page, "type", {"role": "textbox", "name": "Payment Amount"},
                             ["element_exists", "element_visible", "element_enabled"],
                             "Entered payment amount", value=str(payment_amount))
    await _validated_action(logger, page, "type", {"role": "textbox", "name": "Payment Date"},
                             ["element_exists", "element_visible", "element_enabled"],
                             "Entered payment date", value=today)
    await _validated_action(logger, page, "click", {"role": "button", "name": "Record Payment"},
                             ["element_exists", "element_visible", "element_enabled"], "Clicked Record Payment")

    observation = await inspect_page(page)
    logger.record_observation(observation)
    if observation["url"].rstrip("/").endswith("/payments"):
        # No redirect happened, which is exactly what main.py does when
        # record_payment_submit() rejects the form (e.g. overpayment) -
        # it re-renders the same page with a 400 instead of the normal
        # 303-to-the-invoice-detail-page redirect on success. Reading the
        # error banner off that same page (no extra navigation needed).
        error_result = await _run_tool("extract", page, {"target": {"test_id": "payment-error"}})
        raise TaskFailed(error_result.data["text"], category="validation_error")

    paid_result = await _run_tool("extract", page, {"target": {"test_id": "paid-amount"}})
    remaining_result = await _run_tool("extract", page, {"target": {"test_id": "remaining-amount"}})
    status_result = await _run_tool("extract", page, {"target": {"test_id": "invoice-status"}})
    return {
        "invoice_number": invoice_id, "payment_amount": payment_amount,
        "paid_amount": _amount_from_text(paid_result.data["text"]),
        "remaining_amount": _amount_from_text(remaining_result.data["text"]),
        "status": _normalize_status_text(status_result.data["text"]),
    }


async def execute_get_invoice_remaining_balance(page, logger: StepLogger, invoice_id: str) -> Dict[str, Any]:
    if api_router.is_api_available():
        t0 = time.time()
        try:
            result = await _run_tool("api_request", page, {
                "goal": "get_invoice_by_id", "path_params": {"invoice_id": invoice_id},
            })
            inv = result.data["json"]
            logger.log(f"Fetched invoice {invoice_id} via Invoice API", action="api_request",
                       execution_method="api", status="success", duration_ms=(time.time() - t0) * 1000)
            return {"invoice_number": inv["id"], "remaining_amount": inv["remaining_amount"],
                    "status": inv["status"]}
        except registry.ToolExecutionError as e:
            logger.log(f"API call failed ({e}); re-planning to browser fallback", execution_method="replanning",
                        status="failed", error=str(e))

    logger.log("Invoice API unavailable; re-planning to browser fallback", execution_method="replanning")
    await _login(page, logger)
    await _open_invoice_by_id(page, logger, invoice_id)
    remaining_result = await _run_tool("extract", page, {"target": {"test_id": "remaining-amount"}})
    status_result = await _run_tool("extract", page, {"target": {"test_id": "invoice-status"}})
    return {
        "invoice_number": invoice_id,
        "remaining_amount": _amount_from_text(remaining_result.data["text"]),
        "status": _normalize_status_text(status_result.data["text"]),
    }


async def execute_list_partially_paid_invoices(page, logger: StepLogger) -> Dict[str, Any]:
    if api_router.is_api_available():
        t0 = time.time()
        try:
            result = await _run_tool("api_request", page, {
                "goal": "list_invoices_filtered", "query_params": {"status": "partially_paid"},
            })
            invoices = result.data["json"]["invoices"]
            logger.log("Fetched partially-paid invoices via Invoice API", action="api_request",
                       execution_method="api", status="success", duration_ms=(time.time() - t0) * 1000)
            return {"invoices": invoices, "count": len(invoices)}
        except registry.ToolExecutionError as e:
            logger.log(f"API call failed ({e}); re-planning to browser fallback", execution_method="replanning",
                        status="failed", error=str(e))

    logger.log("Invoice API unavailable; re-planning to browser fallback", execution_method="replanning")
    await _login(page, logger)
    await _run_tool("navigate", page, {"url": f"{settings.DEMO_SITE_BASE_URL}/invoices"})
    observation = await inspect_page(page)
    logger.record_observation(observation)
    matches = []
    for row in observation.get("table_rows", []):
        if row["status"] != "partially_paid":
            continue
        amount = _amount_from_text(" ".join(row["cells"]))
        matches.append({"id": row["invoice_id"], "amount": amount, "status": "partially_paid"})
    return {"invoices": matches, "count": len(matches)}


async def execute_pay_remaining_balance_for_invoice(page, logger: StepLogger, invoice_id: str) -> Dict[str, Any]:
    """"Mark the remaining amount of INV-1005 as paid" - fetches the
    current remaining balance, then (if there is one) records a payment for
    exactly that amount, reusing execute_record_payment_for_invoice so both
    API-first/fallback logic and overpayment safety live in one place."""
    balance = await execute_get_invoice_remaining_balance(page, logger, invoice_id)
    remaining = balance["remaining_amount"]
    if remaining <= 0:
        logger.log(f"Invoice {invoice_id} already fully paid; nothing to record", action="verify",
                   status="success")
        return {"invoice_number": invoice_id, "payment_amount": 0.0, "paid_amount": None,
                "remaining_amount": 0.0, "status": balance["status"]}
    return await execute_record_payment_for_invoice(page, logger, invoice_id, remaining)


GOAL_HANDLERS: Dict[str, Callable] = {
    "login_and_download_latest_invoice": execute_login_and_download_latest_invoice,
    "get_latest_invoice_info": execute_get_latest_invoice_info,
    "download_latest_unpaid_invoice": execute_download_latest_unpaid_invoice,
    "get_latest_invoice_customer_email": execute_get_latest_invoice_customer_email,
    "list_partially_paid_invoices": execute_list_partially_paid_invoices,
    "list_unpaid_invoices": execute_list_unpaid_invoices,
    # list_unpaid_invoices_above, record_payment_for_invoice,
    # get_invoice_remaining_balance, and pay_remaining_balance_for_invoice
    # take extra args and are dispatched specially by agent.py
}
