"""
Integration tests for the general "list_unpaid_invoices" goal fix: real
agent, real demo site, real Playwright for the browser-fallback path.

This complements the pure-function tests in test_planner.py (which check
task-understanding classification in isolation) by proving the fix changes
actual application behavior end to end: these natural-language queries used
to incorrectly return WAITING_FOR_USER/goal="unknown"; they must now
COMPLETE with real invoice+customer data, via both the API-first and the
real-browser-fallback paths, without needing any invoice-specific goal.
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
API_KEY_HEADERS = {"X-API-Key": "demo-invoice-api-key"}


def _port_open(host, port, timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def demo_site_process():
    if _port_open("127.0.0.1", 8001):
        yield None
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


@pytest.fixture(autouse=True)
def _reset_demo_data(demo_site_process):
    with httpx.Client(timeout=3.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/demo/reset")
    yield
    with httpx.Client(timeout=3.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/demo/reset")


def _ensure_api_enabled():
    with httpx.Client(timeout=3.0) as client:
        status = client.get(f"{DEMO_SITE_URL}/api/status").json()
        if not status.get("available"):
            cookies = client.post(f"{DEMO_SITE_URL}/login", data={"username": "demo", "password": "demo1234"}).cookies
            client.post(f"{DEMO_SITE_URL}/settings/toggle-api", cookies=cookies)


def _disable_api():
    with httpx.Client(timeout=3.0) as client:
        cookies = client.post(f"{DEMO_SITE_URL}/login", data={"username": "demo", "password": "demo1234"}).cookies
        client.post(f"{DEMO_SITE_URL}/settings/toggle-api", cookies=cookies)
        assert client.get(f"{DEMO_SITE_URL}/api/status").json()["available"] is False


# Seed data has exactly 3 unpaid invoices: inv_1002 (Priya Nair),
# inv_1003 (Devansh Iyer), inv_1005 (Meera Kulkarni).
_EXPECTED_UNPAID_IDS = {"inv_1002", "inv_1003", "inv_1005"}
_EXPECTED_UNPAID_CUSTOMERS = {"Priya Nair", "Devansh Iyer", "Meera Kulkarni"}


def _assert_correct_unpaid_result(execution):
    assert execution.status == "COMPLETED"
    assert execution.success is True
    invoices = execution.result["invoices"]
    assert execution.result["count"] == len(invoices) == 3
    ids = {inv["invoice_number"] for inv in invoices}
    names = {inv["customer_name"] for inv in invoices}
    assert ids == _EXPECTED_UNPAID_IDS
    assert names == _EXPECTED_UNPAID_CUSTOMERS
    for inv in invoices:
        assert inv["status"] == "unpaid"
        assert isinstance(inv["amount"], (int, float))


# ---------------------------------------------------------------------------
# The bug this fix addresses: these used to return WAITING_FOR_USER.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Show me all unpaid invoices and the customer names associated with them",
    "Who has not paid yet?",
    "Show me all unpaid invoices",
    "Who hasn't paid the amount yet?",
    "Which customers haven't paid?",
    "Which invoices are unpaid?",
])
async def test_general_unpaid_query_no_longer_needs_clarification(demo_site_process, db, text):
    _ensure_api_enabled()
    execution = await agent.run_agent(db, text)
    assert execution.status != "WAITING_FOR_USER", (
        f"regression: {text!r} incorrectly needs clarification again"
    )
    _assert_correct_unpaid_result(execution)


@pytest.mark.asyncio
async def test_general_unpaid_query_uses_api_first_strategy(demo_site_process, db):
    _ensure_api_enabled()
    execution = await agent.run_agent(db, "Show me all unpaid invoices")
    assert execution.strategy == "api_first"
    assert execution.api_calls >= 1
    assert execution.browser_actions == 0


@pytest.mark.asyncio
async def test_general_unpaid_query_falls_back_to_real_browser(demo_site_process, db):
    """The definitive proof for both the goal AND the fix: with the
    Invoice API forced off, the real agent - real Playwright, real login,
    real DOM inspection - reads the unpaid rows straight out of the live
    invoices table, customer names included, with no invoice-specific
    goal or id involved anywhere."""
    try:
        _disable_api()
        execution = await agent.run_agent(db, "Which customers haven't paid?")
        assert execution.api_calls == 0
        assert execution.browser_actions > 0
        assert execution.replans_used >= 1
        _assert_correct_unpaid_result(execution)
    finally:
        _ensure_api_enabled()


@pytest.mark.asyncio
async def test_general_unpaid_query_reflects_runtime_payment_changes(demo_site_process, db):
    """Paying off one of the 3 seed unpaid invoices in full must remove it
    from a subsequent "who hasn't paid" query - proving this goal reads
    live data, not a snapshot, and composes correctly with payment tracking."""
    _ensure_api_enabled()
    with httpx.Client(timeout=5.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                    json={"amount": 12800, "date": "2026-09-15"})

    execution = await agent.run_agent(db, "Who has not paid yet?")
    assert execution.status == "COMPLETED"
    ids = {inv["invoice_number"] for inv in execution.result["invoices"]}
    assert ids == {"inv_1003", "inv_1005"}
    assert execution.result["count"] == 2


# ---------------------------------------------------------------------------
# Regression: the amount-threshold goal must still work exactly as before,
# through both the API-first and browser-fallback paths.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_threshold_query_still_uses_list_unpaid_invoices_above(demo_site_process, db):
    _ensure_api_enabled()
    execution = await agent.run_agent(db, "Find all unpaid invoices above ₹10,000.")
    assert execution.status == "COMPLETED"
    assert execution.strategy == "api_first"
    ids = {inv["id"] for inv in execution.result["invoices"]}
    assert ids == {"inv_1002", "inv_1005"}  # inv_1003 (9800) is below the threshold


@pytest.mark.asyncio
async def test_threshold_query_still_works_via_real_browser_fallback(demo_site_process, db):
    try:
        _disable_api()
        execution = await agent.run_agent(db, "Find all unpaid invoices above ₹10,000.")
        assert execution.status == "COMPLETED"
        assert execution.api_calls == 0
        assert execution.browser_actions > 0
        ids = {inv["id"] for inv in execution.result["invoices"]}
        assert ids == {"inv_1002", "inv_1005"}
    finally:
        _ensure_api_enabled()


@pytest.mark.asyncio
async def test_other_existing_goals_unaffected_by_this_fix(demo_site_process, db):
    """Broad regression guard: a handful of the other pre-existing goals
    (including payment-tracking ones) still resolve and execute correctly
    after this change."""
    _ensure_api_enabled()

    e1 = await agent.run_agent(db, "Open the latest unpaid invoice and download it.")
    assert e1.status == "COMPLETED"

    e2 = await agent.run_agent(db, "Show me all partially paid invoices")
    assert e2.status == "COMPLETED"
    assert e2.result["invoices"] == []  # nothing partially paid yet in fresh seed data

    e3 = await agent.run_agent(db, "How much is still pending for invoice INV-1005?")
    assert e3.status == "COMPLETED"
    assert e3.result["invoice_number"] == "inv_1005"


@pytest.mark.asyncio
async def test_truly_unrelated_task_still_asks_for_clarification(demo_site_process, db):
    """Guards against the new keyword rules being too broad - an unrelated
    task must still correctly ask for clarification, not silently resolve
    to the new goal."""
    execution = await agent.run_agent(db, "Book me a flight to Tokyo.")
    assert execution.status == "WAITING_FOR_USER"
    assert len(execution.result["clarification_options"]) == 5
