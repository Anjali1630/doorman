"""
Tests for payment tracking on invoices:
- the data layer (demo_site/data.py: record_payment, status transitions)
- the JSON API (POST/GET /api/invoices/{id}/payments)
- the browser UI (the Record Payment form and payment history table)
- and, most importantly, that the EXISTING agent (both API-first and
  real-Playwright browser-fallback paths) can discover and perform
  payment-related tasks phrased in natural language:
    "Record a payment of ₹500 for invoice INV-1005"
    "How much is still pending for invoice INV-1005?"
    "Show me all partially paid invoices"
    "Mark the remaining amount of INV-1005 as paid"

Each test resets the demo site's data to the original seed set first
(autouse fixture below), so tests in this file don't depend on execution
order or leak state into each other.
"""
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from app.database.session import SessionLocal, init_db
from app.services import agent, planner

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


@contextmanager
def _logged_in_client():
    client = httpx.Client(base_url=DEMO_SITE_URL, timeout=5.0)
    try:
        client.post("/login", data={"username": "demo", "password": "demo1234"})
        yield client
    finally:
        client.close()


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


# ---------------------------------------------------------------------------
# Data layer: seed data shape, status transitions, overpayment rejection
# ---------------------------------------------------------------------------

def test_seed_invoices_have_payment_fields(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        resp = client.get(f"{DEMO_SITE_URL}/api/invoices/inv_1002", headers=API_KEY_HEADERS)
    inv = resp.json()
    assert inv["total_amount"] == 12800.0
    assert inv["paid_amount"] == 0.0
    assert inv["remaining_amount"] == 12800.0
    assert inv["status"] == "unpaid"
    assert inv["payments"] == []


def test_seed_paid_invoice_has_zero_remaining(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        resp = client.get(f"{DEMO_SITE_URL}/api/invoices/inv_1001", headers=API_KEY_HEADERS)
    inv = resp.json()
    assert inv["status"] == "paid"
    assert inv["paid_amount"] == inv["total_amount"]
    assert inv["remaining_amount"] == 0.0


def test_partial_payment_sets_partially_paid_status(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                            json={"amount": 5000, "date": "2026-09-15"})
    assert resp.status_code == 200
    inv = resp.json()
    assert inv["status"] == "partially_paid"
    assert inv["paid_amount"] == 5000.0
    assert inv["remaining_amount"] == 7800.0
    assert inv["payments"] == [{"amount": 5000.0, "date": "2026-09-15"}]


def test_full_payment_sets_paid_status(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                            json={"amount": 12800, "date": "2026-09-15"})
    assert resp.status_code == 200
    inv = resp.json()
    assert inv["status"] == "paid"
    assert inv["remaining_amount"] == 0.0


def test_multiple_partial_payments_accumulate_correctly(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1005/payments", headers=API_KEY_HEADERS,
                    json={"amount": 5000, "date": "2026-09-10"})
        client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1005/payments", headers=API_KEY_HEADERS,
                    json={"amount": 3000, "date": "2026-09-12"})
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1005/payments", headers=API_KEY_HEADERS,
                            json={"amount": 7600, "date": "2026-09-14"})
    inv = resp.json()
    assert inv["paid_amount"] == 15600.0
    assert inv["remaining_amount"] == 0.0
    assert inv["status"] == "paid"
    assert len(inv["payments"]) == 3
    assert [p["amount"] for p in inv["payments"]] == [5000.0, 3000.0, 7600.0]


def test_overpayment_rejected_via_api(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                            json={"amount": 12801, "date": "2026-09-15"})  # remaining is exactly 12800
    assert resp.status_code == 422
    assert "exceeds the remaining balance" in resp.json()["detail"]

    # And the invoice is genuinely untouched by the rejected attempt.
    with httpx.Client(timeout=5.0) as client:
        inv = client.get(f"{DEMO_SITE_URL}/api/invoices/inv_1002", headers=API_KEY_HEADERS).json()
    assert inv["paid_amount"] == 0.0
    assert inv["status"] == "unpaid"


def test_overpayment_after_partial_payment_rejected(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                    json={"amount": 10000, "date": "2026-09-10"})
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                            json={"amount": 2801, "date": "2026-09-11"})  # remaining is 2800
    assert resp.status_code == 422
    with httpx.Client(timeout=5.0) as client:
        inv = client.get(f"{DEMO_SITE_URL}/api/invoices/inv_1002", headers=API_KEY_HEADERS).json()
    assert inv["paid_amount"] == 10000.0  # unaffected by the rejected second payment
    assert inv["status"] == "partially_paid"


@pytest.mark.parametrize("amount", [0, -50])
def test_non_positive_payment_rejected(demo_site_process, amount):
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                            json={"amount": amount, "date": "2026-09-15"})
    assert resp.status_code == 422
    assert "greater than zero" in resp.json()["detail"]


def test_payment_for_nonexistent_invoice_returns_404(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices/inv_9999/payments", headers=API_KEY_HEADERS,
                            json={"amount": 100, "date": "2026-09-15"})
    assert resp.status_code == 404


def test_payment_without_api_key_rejected(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments",
                            json={"amount": 100, "date": "2026-09-15"})
    assert resp.status_code == 401


def test_payment_history_endpoint(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                    json={"amount": 4000, "date": "2026-09-10"})
        resp = client.get(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["invoice_id"] == "inv_1002"
    assert body["payments"] == [{"amount": 4000.0, "date": "2026-09-10"}]


def test_payment_defaults_to_today_when_date_omitted(demo_site_process):
    import datetime
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                            json={"amount": 100})
    assert resp.status_code == 200
    assert resp.json()["payments"][-1]["date"] == str(datetime.date.today())


def test_list_invoices_filtered_by_partially_paid_status(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                    json={"amount": 1000, "date": "2026-09-10"})
        client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1005/payments", headers=API_KEY_HEADERS,
                    json={"amount": 2000, "date": "2026-09-10"})
        resp = client.get(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS,
                           params={"status": "partially_paid"})
    ids = {i["id"] for i in resp.json()["invoices"]}
    assert ids == {"inv_1002", "inv_1005"}


def test_demo_reset_clears_payment_history(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                    json={"amount": 5000, "date": "2026-09-10"})
        client.post(f"{DEMO_SITE_URL}/api/demo/reset")
        inv = client.get(f"{DEMO_SITE_URL}/api/invoices/inv_1002", headers=API_KEY_HEADERS).json()
    assert inv["status"] == "unpaid"
    assert inv["paid_amount"] == 0.0
    assert inv["payments"] == []


# ---------------------------------------------------------------------------
# Browser UI: the Record Payment form and payment history table
# ---------------------------------------------------------------------------

def test_invoice_detail_page_shows_payment_form_when_unpaid(demo_site_process):
    with _logged_in_client() as client:
        resp = client.get("/invoices/inv_1002")
    assert resp.status_code == 200
    assert 'name="payment_amount"' in resp.text
    assert 'name="payment_date"' in resp.text
    assert "Record Payment" in resp.text
    assert 'data-testid="remaining-amount"' in resp.text


def test_invoice_detail_page_hides_payment_form_when_fully_paid(demo_site_process):
    with _logged_in_client() as client:
        resp = client.get("/invoices/inv_1001")  # seed "paid" invoice
    assert resp.status_code == 200
    assert 'name="payment_amount"' not in resp.text
    assert "fully paid" in resp.text.lower()


def test_record_payment_via_browser_form_updates_detail_page(demo_site_process):
    with _logged_in_client() as client:
        resp = client.post("/invoices/inv_1002/payments",
                            data={"payment_amount": "5000", "payment_date": "2026-09-15"},
                            follow_redirects=True)
    assert resp.status_code == 200
    assert "Rs. 5000.00" in resp.text
    assert "Rs. 7800.00" in resp.text
    assert "Partially Paid" in resp.text


def test_record_payment_via_browser_form_shows_history(demo_site_process):
    with _logged_in_client() as client:
        client.post("/invoices/inv_1002/payments",
                     data={"payment_amount": "3000", "payment_date": "2026-09-10"})
        resp = client.post("/invoices/inv_1002/payments",
                            data={"payment_amount": "2000", "payment_date": "2026-09-12"},
                            follow_redirects=True)
    assert resp.status_code == 200
    assert 'id="payment-history-table"' in resp.text
    assert "2026-09-10" in resp.text
    assert "2026-09-12" in resp.text


def test_overpayment_rejected_via_browser_form_with_message(demo_site_process):
    with _logged_in_client() as client:
        resp = client.post("/invoices/inv_1002/payments",
                            data={"payment_amount": "999999", "payment_date": "2026-09-15"})
    assert resp.status_code == 400
    assert "exceeds the remaining balance" in resp.text
    assert 'data-testid="payment-error"' in resp.text


@pytest.mark.parametrize("overrides,expected_substring", [
    ({"payment_amount": "-5"}, "greater than zero"),
    ({"payment_amount": "not-a-number"}, "must be a number"),
    ({"payment_date": "not-a-date"}, "valid date"),
])
def test_invalid_payment_form_submissions_rejected(demo_site_process, overrides, expected_substring):
    base = {"payment_amount": "100", "payment_date": "2026-09-15"}
    base.update(overrides)
    with _logged_in_client() as client:
        resp = client.post("/invoices/inv_1002/payments", data=base)
    assert resp.status_code == 400
    assert expected_substring.lower() in resp.text.lower()


def test_invoices_list_shows_partially_paid_badge(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                    json={"amount": 1000, "date": "2026-09-10"})
    with _logged_in_client() as client:
        resp = client.get("/invoices")
    assert "Partially Paid" in resp.text
    assert "badge-partially_paid" in resp.text


# ---------------------------------------------------------------------------
# Planner: natural-language task understanding for the 4 payment phrasings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected_goal", [
    ("Record a payment of ₹500 for invoice INV-1005", "record_payment_for_invoice"),
    ("How much is still pending for invoice INV-1005?", "get_invoice_remaining_balance"),
    ("Show me all partially paid invoices", "list_partially_paid_invoices"),
    ("Mark the remaining amount of INV-1005 as paid", "pay_remaining_balance_for_invoice"),
])
def test_planner_understands_payment_phrasings(text, expected_goal):
    understanding, _ = planner.understand_task(text)
    assert understanding.goal == expected_goal


def test_planner_normalizes_hyphenated_uppercase_invoice_id():
    understanding, _ = planner.understand_task("How much is still pending for invoice INV-1005?")
    assert any("inv_1005" in c for c in understanding.constraints)


def test_planner_extracts_payment_amount_from_rupee_symbol():
    understanding, _ = planner.understand_task("Record a payment of ₹500 for invoice INV-1005")
    assert any("payment_amount = 500" in c for c in understanding.constraints)


def test_existing_five_task_phrasings_still_work_unaffected():
    """Guards against the new payment rules accidentally shadowing the
    original 5 goals (e.g. via an overly broad keyword match)."""
    cases = [
        ("Log into the website and download the latest invoice.", "login_and_download_latest_invoice"),
        ("Find the latest invoice and tell me its invoice number and amount.", "get_latest_invoice_info"),
        ("Find all unpaid invoices above ₹10,000.", "list_unpaid_invoices_above"),
        ("Open the latest unpaid invoice and download it.", "download_latest_unpaid_invoice"),
        ("Find the customer associated with the latest invoice and return their email.",
         "get_latest_invoice_customer_email"),
    ]
    for text, expected_goal in cases:
        understanding, _ = planner.understand_task(text)
        assert understanding.goal == expected_goal, f"regressed: {text!r}"


# ---------------------------------------------------------------------------
# Agent discovery: the 4 payment tasks, through the real agent, via BOTH
# the API-first path and the real-Playwright browser-fallback path.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_records_payment_via_api(demo_site_process, db):
    _ensure_api_enabled()
    execution = await agent.run_agent(db, "Record a payment of ₹500 for invoice INV-1005")
    assert execution.status == "COMPLETED"
    assert execution.strategy == "api_first"
    assert execution.api_calls >= 1
    assert execution.browser_actions == 0
    assert execution.result["invoice_number"] == "inv_1005"
    assert execution.result["payment_amount"] == 500.0
    assert execution.result["remaining_amount"] == 15100.0
    assert execution.result["status"] == "partially_paid"


@pytest.mark.asyncio
async def test_agent_gets_remaining_balance_via_api(demo_site_process, db):
    _ensure_api_enabled()
    with httpx.Client(timeout=5.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1005/payments", headers=API_KEY_HEADERS,
                    json={"amount": 6000, "date": "2026-09-10"})

    execution = await agent.run_agent(db, "How much is still pending for invoice INV-1005?")
    assert execution.status == "COMPLETED"
    assert execution.api_calls >= 1
    assert execution.result["invoice_number"] == "inv_1005"
    assert execution.result["remaining_amount"] == 9600.0
    assert execution.result["status"] == "partially_paid"


@pytest.mark.asyncio
async def test_agent_lists_partially_paid_invoices_via_api(demo_site_process, db):
    _ensure_api_enabled()
    with httpx.Client(timeout=5.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                    json={"amount": 1000, "date": "2026-09-10"})

    execution = await agent.run_agent(db, "Show me all partially paid invoices")
    assert execution.status == "COMPLETED"
    ids = [i["id"] for i in execution.result["invoices"]]
    assert "inv_1002" in ids
    assert execution.result["count"] == len(ids)


@pytest.mark.asyncio
async def test_agent_marks_remaining_balance_as_paid_via_api(demo_site_process, db):
    _ensure_api_enabled()
    execution = await agent.run_agent(db, "Mark the remaining amount of INV-1002 as paid")
    assert execution.status == "COMPLETED"
    assert execution.result["status"] == "paid"
    assert execution.result["remaining_amount"] == 0.0

    with httpx.Client(timeout=5.0) as client:
        inv = client.get(f"{DEMO_SITE_URL}/api/invoices/inv_1002", headers=API_KEY_HEADERS).json()
    assert inv["status"] == "paid"


@pytest.mark.asyncio
async def test_agent_marking_already_paid_invoice_is_a_safe_noop(demo_site_process, db):
    _ensure_api_enabled()
    execution = await agent.run_agent(db, "Mark the remaining amount of INV-1001 as paid")  # seed "paid" invoice
    assert execution.status == "COMPLETED"
    assert execution.result["remaining_amount"] == 0.0
    assert execution.result["status"] == "paid"


@pytest.mark.asyncio
async def test_agent_overpayment_task_fails_cleanly_via_api(demo_site_process, db):
    _ensure_api_enabled()
    execution = await agent.run_agent(db, "Record a payment of ₹999999 for invoice INV-1005")
    assert execution.status == "FAILED"
    assert "exceeds the remaining balance" in execution.error


@pytest.mark.asyncio
async def test_agent_records_payment_via_real_browser_fallback(demo_site_process, db):
    """The definitive proof for the payment feature: with the Invoice API
    forced OFF, the real agent - real Playwright, real DOM inspection, real
    pre-execution validation - fills in and submits the actual Record
    Payment form on the invoice detail page."""
    try:
        _disable_api()
        execution = await agent.run_agent(db, "Record a payment of ₹500 for invoice INV-1005")
        assert execution.status == "COMPLETED"
        assert execution.api_calls == 0
        assert execution.browser_actions > 0
        assert execution.replans_used >= 1
        assert execution.result["invoice_number"] == "inv_1005"
        assert execution.result["payment_amount"] == 500.0
        assert execution.result["remaining_amount"] == 15100.0
        assert execution.result["status"] == "partially_paid"
    finally:
        _ensure_api_enabled()


@pytest.mark.asyncio
async def test_agent_gets_remaining_balance_via_real_browser_fallback(demo_site_process, db):
    try:
        with httpx.Client(timeout=5.0) as client:
            client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1005/payments", headers=API_KEY_HEADERS,
                        json={"amount": 6000, "date": "2026-09-10"})
        _disable_api()
        execution = await agent.run_agent(db, "How much is still pending for invoice INV-1005?")
        assert execution.status == "COMPLETED"
        assert execution.api_calls == 0
        assert execution.browser_actions > 0
        assert execution.result["remaining_amount"] == 9600.0
        assert execution.result["status"] == "partially_paid"
    finally:
        _ensure_api_enabled()


@pytest.mark.asyncio
async def test_agent_lists_partially_paid_invoices_via_real_browser_fallback(demo_site_process, db):
    try:
        with httpx.Client(timeout=5.0) as client:
            client.post(f"{DEMO_SITE_URL}/api/invoices/inv_1002/payments", headers=API_KEY_HEADERS,
                        json={"amount": 1000, "date": "2026-09-10"})
        _disable_api()
        execution = await agent.run_agent(db, "Show me all partially paid invoices")
        assert execution.status == "COMPLETED"
        assert execution.api_calls == 0
        assert execution.browser_actions > 0
        ids = [i["id"] for i in execution.result["invoices"]]
        assert "inv_1002" in ids
    finally:
        _ensure_api_enabled()


@pytest.mark.asyncio
async def test_agent_marks_remaining_balance_as_paid_via_real_browser_fallback(demo_site_process, db):
    try:
        _disable_api()
        execution = await agent.run_agent(db, "Mark the remaining amount of INV-1002 as paid")
        assert execution.status == "COMPLETED"
        assert execution.api_calls == 0
        assert execution.browser_actions > 0
        assert execution.result["status"] == "paid"
        assert execution.result["remaining_amount"] == 0.0
    finally:
        _ensure_api_enabled()

    with httpx.Client(timeout=5.0) as client:
        inv = client.get(f"{DEMO_SITE_URL}/api/invoices/inv_1002", headers=API_KEY_HEADERS).json()
    assert inv["status"] == "paid"


@pytest.mark.asyncio
async def test_agent_overpayment_via_real_browser_fallback_fails_cleanly_without_hanging(demo_site_process, db):
    try:
        _disable_api()
        execution = await agent.run_agent(db, "Record a payment of ₹999999 for invoice INV-1005")
        assert execution.status == "FAILED"
        assert "exceeds the remaining balance" in execution.error
    finally:
        _ensure_api_enabled()

    # And the invoice was NOT mutated by the failed browser attempt (checked
    # via the API only after re-enabling it, since it was deliberately off
    # for the browser-fallback assertions above).
    with httpx.Client(timeout=5.0) as client:
        inv = client.get(f"{DEMO_SITE_URL}/api/invoices/inv_1005", headers=API_KEY_HEADERS).json()
    assert inv["status"] == "unpaid"
    assert inv["paid_amount"] == 0.0


@pytest.mark.asyncio
async def test_agent_discovers_payment_history_on_newly_added_invoice(demo_site_process, db):
    """Combines the "Add New Invoice" feature with payment tracking: an
    invoice created at runtime can immediately have a payment recorded
    against it and be found by "pending balance" / "partially paid" tasks -
    proving the two features compose cleanly."""
    _ensure_api_enabled()
    with httpx.Client(timeout=5.0) as client:
        create_resp = client.post(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS, json={
            "invoice_number": "inv_7001", "customer_name": "Fresh Co", "customer_email": "fresh.co@example.com",
            "amount": 20000, "status": "unpaid", "date": "2026-09-20",
        })
        assert create_resp.status_code == 200

    execution = await agent.run_agent(db, "Record a payment of ₹8000 for invoice INV-7001")
    assert execution.status == "COMPLETED"
    assert execution.result["remaining_amount"] == 12000.0
    assert execution.result["status"] == "partially_paid"

    execution2 = await agent.run_agent(db, "Show me all partially paid invoices")
    assert "inv_7001" in [i["id"] for i in execution2.result["invoices"]]
