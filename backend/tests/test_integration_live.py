"""
Integration tests that exercise the REAL stack: a live demo site subprocess,
the real Playwright browser, and the real agent orchestrator. These cover
click/type/navigation/API-execution/browser-execution/API-fallback/download/
extraction/task-completion end to end - not mocks.

If the demo site fails to start (e.g. port already bound in a constrained
CI sandbox), tests skip with a clear reason rather than reporting a false
failure unrelated to the code under test.
"""
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from app.database.session import SessionLocal, init_db
from app.services import agent

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEMO_SITE_URL = "http://127.0.0.1:8001"


def _port_open(host, port, timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def demo_site_process():
    if _port_open("127.0.0.1", 8001):
        yield None  # Already running (e.g. started manually) - reuse it.
        return

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "demo_site.main:app", "--port", "8001", "--host", "127.0.0.1"],
        cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    for _ in range(40):
        if _port_open("127.0.0.1", 8001):
            break
        time.sleep(0.25)
    else:
        proc.terminate()
        pytest.skip("Could not start demo_site for integration tests")

    yield proc
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def db():
    init_db()
    session = SessionLocal()
    yield session
    session.close()


def _ensure_api_enabled():
    with httpx.Client(timeout=3.0) as client:
        status = client.get(f"{DEMO_SITE_URL}/api/status").json()
        if not status.get("available"):
            cookies = client.post(f"{DEMO_SITE_URL}/login", data={"username": "demo", "password": "demo1234"}).cookies
            client.post(f"{DEMO_SITE_URL}/settings/toggle-api", cookies=cookies)


@pytest.mark.asyncio
async def test_login_and_download_latest_invoice(demo_site_process, db):
    _ensure_api_enabled()
    execution = await agent.run_agent(db, "Log into the website and download the latest invoice.")
    assert execution.status == "COMPLETED"
    assert execution.success is True
    assert execution.browser_actions > 0
    assert "download_path" in execution.result
    assert Path(execution.result["download_path"]).exists()


@pytest.mark.asyncio
async def test_get_latest_invoice_info_uses_api(demo_site_process, db):
    _ensure_api_enabled()
    execution = await agent.run_agent(db, "Find the latest invoice and tell me its invoice number and amount.")
    assert execution.status == "COMPLETED"
    assert execution.strategy == "api_first"
    assert execution.api_calls >= 1
    assert execution.browser_actions == 0
    assert execution.result["invoice_number"].startswith("inv_")


@pytest.mark.asyncio
async def test_api_disabled_triggers_real_browser_fallback(demo_site_process, db):
    _ensure_api_enabled()
    with httpx.Client(timeout=3.0) as client:
        cookies = client.post(f"{DEMO_SITE_URL}/login", data={"username": "demo", "password": "demo1234"}).cookies
        client.post(f"{DEMO_SITE_URL}/settings/toggle-api", cookies=cookies)
        assert client.get(f"{DEMO_SITE_URL}/api/status").json()["available"] is False

    try:
        execution = await agent.run_agent(db, "Open the latest unpaid invoice and download it.")
        assert execution.status == "COMPLETED"
        assert execution.api_calls == 0
        assert execution.browser_actions > 0
        assert execution.replans_used >= 1
        assert Path(execution.result["download_path"]).exists()
    finally:
        _ensure_api_enabled()


@pytest.mark.asyncio
async def test_unknown_task_asks_for_clarification(demo_site_process, db):
    execution = await agent.run_agent(db, "Book me a flight to Paris next week.")
    assert execution.status == "WAITING_FOR_USER"
    assert len(execution.result["clarification_options"]) == 5


@pytest.mark.asyncio
async def test_validation_blocks_hallucinated_target_end_to_end(demo_site_process, db):
    """A real end-to-end check that a step targeting an element which does
    not exist on the real live page is rejected by the validator before any
    Playwright click happens, and the task fails cleanly instead of hanging
    or silently 'succeeding'."""
    from app.services.browser import BrowserSession
    from app.services.executor import _validated_action, StepLogger, TaskFailed
    from app.core.config import settings

    execution = None
    from app.models import orm
    task = orm.Task(raw_text="adversarial probe")
    db.add(task)
    db.flush()
    execution = orm.Execution(task_id=task.id, status="EXECUTING")
    db.add(execution)
    db.commit()
    logger = StepLogger(db, execution)

    async with BrowserSession() as session:
        page = session.page
        await page.goto(f"{settings.DEMO_SITE_BASE_URL}/login")
        with pytest.raises(TaskFailed) as exc:
            await _validated_action(
                logger, page, "click", {"role": "button", "name": "This Button Does Not Exist"},
                ["element_exists"], "should never succeed", max_retries=0,
            )
        assert exc.value.category == "validation_error"


# ---------------------------------------------------------------------------
# Genuine LLM-driven planning + re-planning, end to end against the real
# demo site and real Playwright. The LLM client is mocked (no real API key
# in this test environment), but everything downstream of that mock is
# real: the fabricated plan is actually executed by Playwright, a
# fabricated hallucinated target is actually rejected by the real
# validator against the real live DOM, and the re-planning call is
# genuinely given that real DOM snapshot and that real error string - this
# is what proves the wiring, not just the prompt-construction unit tests in
# test_llm_planning.py.
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_llm_key(monkeypatch):
    """Makes llm_client.is_available True without touching the network -
    every test using this fixture must also monkeypatch chat_json."""
    from app.services.llm import llm_client
    monkeypatch.setattr(llm_client, "api_key", "fake-test-key-for-mocked-llm-tests")
    yield llm_client


@pytest.mark.asyncio
async def test_llm_driven_plan_executes_real_api_step(demo_site_process, db, fake_llm_key, monkeypatch):
    """The LLM proposes a genuine action plan (a real api_request step, not
    a goal id) and it is actually persisted and actually executed."""
    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        if "task-understanding module" in system_prompt:
            return {"not": "a valid TaskUnderstanding shape"}  # forces the (harmless) deterministic classifier
        return {
            "strategy": "api_first",
            "steps": [
                {"step_id": 1, "action": "api_request", "execution_method": "api",
                 "api_meta": {"goal": "get_latest_invoice"}, "expected_result": "fetched latest invoice"},
            ],
        }
    monkeypatch.setattr(fake_llm_key, "chat_json", fake_chat_json)

    _ensure_api_enabled()
    execution = await agent.run_agent(db, "Find the latest invoice and tell me its invoice number and amount.")

    assert execution.status == "COMPLETED"
    assert execution.api_calls >= 1
    assert execution.browser_actions == 0

    from app.models import orm
    plan = db.get(orm.Plan, execution.plan_id)
    assert plan.created_by == "openrouter"
    assert len(plan.steps) == 1
    assert plan.steps[0].action == "api_request"
    assert plan.steps[0].execution_method == "api"


@pytest.mark.asyncio
async def test_llm_driven_replanning_uses_real_dom_and_error(demo_site_process, db, fake_llm_key, monkeypatch):
    """The LLM's first plan hallucinates a button that doesn't exist. This
    proves: (1) the real validator blocks it against the real live page,
    (2) the re-planning call actually receives the real error text and the
    real DOM snapshot from that exact moment (not a static/templated one),
    and (3) the task still completes because the re-planned step is valid.

    Dispatch is based on which prompt is being sent (task-understanding vs.
    planning vs. re-planning all share the same chat_json() call), not on
    call order - understand_task() also calls the LLM once before planning
    does, so counting calls naively is fragile."""
    captured = {}
    replan_calls = {"n": 0}

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        if "task-understanding module" in system_prompt:
            # Let this fall back to the deterministic classifier - simplest
            # way to get a reliably-known goal id without over-specifying
            # the mock, and it's realistic (a real key might also produce
            # nothing usable here on a given call).
            return {"not": "a valid TaskUnderstanding shape"}
        if "re-planning module" in system_prompt:
            replan_calls["n"] += 1
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return {"step_id": 99, "action": "navigate", "value": f"{DEMO_SITE_URL}/invoices",
                    "execution_method": "browser", "expected_result": "navigated to invoices instead"}
        # planning module: the initial plan, deliberately containing one
        # hallucinated target that does not exist anywhere on the real site.
        return {
            "strategy": "browser_fallback",
            "steps": [
                {"step_id": 1, "action": "navigate", "value": f"{DEMO_SITE_URL}/login",
                 "execution_method": "browser", "expected_result": "on login page"},
                {"step_id": 2, "action": "type", "target": {"role": "textbox", "name": "Username"},
                 "value": "demo", "preconditions": ["element_exists"], "execution_method": "browser"},
                {"step_id": 3, "action": "type", "target": {"role": "textbox", "name": "Password"},
                 "value": "demo1234", "preconditions": ["element_exists"], "execution_method": "browser"},
                {"step_id": 4, "action": "click", "target": {"role": "button", "name": "Log In"},
                 "preconditions": ["element_exists"], "execution_method": "browser"},
                {"step_id": 5, "action": "click",
                 "target": {"role": "button", "name": "Nonexistent Magic Button"},
                 "preconditions": ["element_exists"], "execution_method": "browser"},
            ],
        }

    monkeypatch.setattr(fake_llm_key, "chat_json", fake_chat_json)
    _ensure_api_enabled()

    execution = await agent.run_agent(db, "Find the latest invoice and tell me its invoice number and amount.")

    assert replan_calls["n"] == 1, "exactly one genuine re-plan call should have happened"
    # The REAL validation error reached the (fake) LLM...
    assert "Nonexistent Magic Button" in captured["system_prompt"]
    assert "No element matching" in captured["system_prompt"]
    # ...alongside the REAL current DOM state (the dashboard, reached after
    # the real login actually succeeded) - not a cached or synthetic one.
    assert "Invoices" in captured["system_prompt"] or "dashboard" in captured["system_prompt"].lower()
    assert execution.replans_used >= 1
    assert execution.status == "COMPLETED"


@pytest.mark.asyncio
async def test_llm_driven_execution_falls_back_when_llm_gives_up(demo_site_process, db, fake_llm_key, monkeypatch):
    """If the LLM's plan fails AND its re-planning attempt gives up, the
    whole task must not just fail - agent.py must fall back to the
    reliable deterministic goal handler and still complete the task."""
    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        if "task-understanding module" in system_prompt:
            return {"not": "a valid TaskUnderstanding shape"}
        if "re-planning module" in system_prompt:
            return {"give_up": True, "reason": "no safe element found"}
        return {"strategy": "browser_fallback", "steps": [
            {"step_id": 1, "action": "click", "target": {"role": "button", "name": "Totally Fake Button"},
             "preconditions": ["element_exists"], "execution_method": "browser"},
        ]}

    monkeypatch.setattr(fake_llm_key, "chat_json", fake_chat_json)
    _ensure_api_enabled()

    execution = await agent.run_agent(db, "Find the latest invoice and tell me its invoice number and amount.")

    # The LLM path failed and its re-plan attempt gave up, but the
    # deterministic API-first handler picked the task back up and completed it.
    assert execution.status == "COMPLETED"
    assert execution.api_calls >= 1
    assert execution.result["invoice_number"].startswith("inv_")

