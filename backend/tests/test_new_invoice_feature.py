"""
Tests for the "Add New Invoice" feature on the demo Business Portal:
- the HTML form (GET/POST /invoices/new)
- the JSON API (POST /api/invoices)
- validation of both
- customer lookup-or-create by email
- and, most importantly, that the EXISTING agent (both the API-first path
  and the real-Playwright browser-fallback path) genuinely discovers and
  interacts with invoices added at runtime, not just the static seed data.

Each test resets the demo site's data to the original seed set first
(autouse fixture below), so tests in this file don't depend on execution
order or leak state into each other.
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
    """Ensures every test in this file starts from the original seed data -
    invoices added by one test must not affect another."""
    with httpx.Client(timeout=3.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/demo/reset")
    yield
    with httpx.Client(timeout=3.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/demo/reset")


from contextlib import contextmanager


@contextmanager
def _logged_in_client():
    client = httpx.Client(base_url=DEMO_SITE_URL, timeout=5.0)
    try:
        client.post("/login", data={"username": "demo", "password": "demo1234"})
        yield client
    finally:
        client.close()


# ---------------------------------------------------------------------------
# HTML form
# ---------------------------------------------------------------------------

def test_add_invoice_button_visible_on_invoices_page(demo_site_process):
    with _logged_in_client() as client:
        resp = client.get("/invoices")
    assert resp.status_code == 200
    assert 'href="/invoices/new"' in resp.text
    assert "Add New Invoice" in resp.text


def test_new_invoice_form_loads_with_prefilled_next_id(demo_site_process):
    with _logged_in_client() as client:
        resp = client.get("/invoices/new")
    assert resp.status_code == 200
    for field in ("invoice_number", "customer_name", "customer_email", "amount", "status", "date"):
        assert f'name="{field}"' in resp.text
    # The 6 seed invoices are inv_1001..inv_1006, so the next id should be inv_1007.
    assert 'value="inv_1007"' in resp.text


def test_new_invoice_form_requires_login():
    with httpx.Client(base_url=DEMO_SITE_URL, timeout=5.0) as client:
        resp = client.get("/invoices/new", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)


def test_create_invoice_via_browser_form_appears_in_list_and_api(demo_site_process):
    with _logged_in_client() as client:
        resp = client.post("/invoices/new", data={
            "invoice_number": "inv_5001", "customer_name": "Rahul Verma",
            "customer_email": "rahul.verma@example.com", "amount": "25000",
            "status": "unpaid", "date": "2026-09-20",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert "inv_5001" in resp.text  # landed on the new invoice's detail page

        list_resp = client.get("/invoices")
        assert "inv_5001" in list_resp.text
        assert "Rahul Verma" in list_resp.text

    with httpx.Client(timeout=5.0) as client:
        api_resp = client.get(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS)
    ids = [i["id"] for i in api_resp.json()["invoices"]]
    assert "inv_5001" in ids


def test_duplicate_invoice_number_rejected_via_form(demo_site_process):
    with _logged_in_client() as client:
        resp = client.post("/invoices/new", data={
            "invoice_number": "inv_1001", "customer_name": "X", "customer_email": "x@example.com",
            "amount": "100", "status": "paid", "date": "2026-09-20",
        })
    assert resp.status_code == 400
    assert "already exists" in resp.text


@pytest.mark.parametrize("overrides,expected_substring", [
    ({"amount": "-5"}, "greater than zero"),
    ({"amount": "not-a-number"}, "must be a number"),
    ({"status": "overdue"}, "paid"),
    ({"customer_email": "not-an-email"}, "email"),
    ({"invoice_number": ""}, "required"),
    ({"date": "not-a-date"}, "date"),
])
def test_invalid_form_submissions_rejected_with_message(demo_site_process, overrides, expected_substring):
    base = {
        "invoice_number": "inv_5002", "customer_name": "X", "customer_email": "x@example.com",
        "amount": "100", "status": "paid", "date": "2026-09-20",
    }
    base.update(overrides)
    with _logged_in_client() as client:
        resp = client.post("/invoices/new", data=base)
    assert resp.status_code == 400
    assert expected_substring.lower() in resp.text.lower()


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

def test_create_invoice_via_json_api(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS, json={
            "customer_name": "API Customer", "customer_email": "api.customer@example.com",
            "amount": 8000, "status": "unpaid", "date": "2026-09-18",
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "inv_1007"  # auto-generated, next after the 6 seed invoices
    assert body["amount"] == 8000.0
    assert body["status"] == "unpaid"


def test_create_invoice_via_json_api_without_key_rejected(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices", json={
            "customer_name": "X", "customer_email": "x@example.com",
            "amount": 100, "status": "paid", "date": "2026-09-18",
        })
    assert resp.status_code == 401


def test_create_invoice_via_json_api_duplicate_id_rejected(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS, json={
            "invoice_number": "inv_1002", "customer_name": "X", "customer_email": "x@example.com",
            "amount": 100, "status": "paid", "date": "2026-09-18",
        })
    assert resp.status_code == 409


def test_json_api_reuses_existing_customer_by_email(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS, json={
            "customer_name": "Aarav Sharma", "customer_email": "aarav.sharma@example.com",
            "amount": 999, "status": "unpaid", "date": "2026-09-19",
        })
        assert resp.status_code == 200
        assert resp.json()["customer_id"] == "cust_1"  # existing customer, not a new one

        customers = client.get(f"{DEMO_SITE_URL}/api/customers/cust_1", headers=API_KEY_HEADERS).json()
        assert customers["name"] == "Aarav Sharma"


def test_json_api_creates_new_customer_for_new_email(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS, json={
            "customer_name": "Brand New Person", "customer_email": "brand.new@example.com",
            "amount": 500, "status": "paid", "date": "2026-09-19",
        })
        assert resp.status_code == 200
        new_customer_id = resp.json()["customer_id"]
        assert new_customer_id not in ("cust_1", "cust_2", "cust_3", "cust_4")

        customer = client.get(f"{DEMO_SITE_URL}/api/customers/{new_customer_id}",
                               headers=API_KEY_HEADERS).json()
        assert customer["name"] == "Brand New Person"
        assert customer["email"] == "brand.new@example.com"


# ---------------------------------------------------------------------------
# The critical requirement: the EXISTING agent discovers and interacts with
# newly added invoices, via BOTH the API-first path and the real-Playwright
# browser-fallback path.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_discovers_newly_added_invoice_via_api(demo_site_process, db):
    with httpx.Client(timeout=5.0) as client:
        create_resp = client.post(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS, json={
            "customer_name": "Fresh Customer", "customer_email": "fresh.customer@example.com",
            "amount": 42000, "status": "unpaid", "date": "2026-12-31",  # later than all seed data
        })
    assert create_resp.status_code == 200
    new_invoice_id = create_resp.json()["id"]

    execution = await agent.run_agent(db, "Find the latest invoice and tell me its invoice number and amount.")
    assert execution.status == "COMPLETED"
    assert execution.strategy == "api_first"
    assert execution.api_calls >= 1
    assert execution.result["invoice_number"] == new_invoice_id
    assert execution.result["amount"] == 42000.0


@pytest.mark.asyncio
async def test_agent_includes_newly_added_invoice_in_filtered_list(demo_site_process, db):
    with httpx.Client(timeout=5.0) as client:
        create_resp = client.post(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS, json={
            "customer_name": "Big Spender", "customer_email": "big.spender@example.com",
            "amount": 99000, "status": "unpaid", "date": "2026-09-25",
        })
    new_invoice_id = create_resp.json()["id"]

    execution = await agent.run_agent(db, "Find all unpaid invoices above ₹10,000.")
    assert execution.status == "COMPLETED"
    found_ids = [i["id"] for i in execution.result["invoices"]]
    assert new_invoice_id in found_ids


@pytest.mark.asyncio
async def test_agent_discovers_and_downloads_newly_added_invoice_via_real_browser(demo_site_process, db):
    """The definitive proof: with the Invoice API forced OFF (real browser
    fallback), the real agent - real Playwright, real DOM inspection, real
    pre-execution validation - opens and downloads an invoice that was
    added to the portal after the server started, using nothing but the
    existing goal handler and the existing invoices-table template."""
    # Add the invoice while the API is still enabled (simpler HTTP call),
    # THEN disable the API so the agent is forced through the browser.
    with httpx.Client(timeout=5.0) as client:
        create_resp = client.post(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS, json={
            "customer_name": "Browser Discovered Co", "customer_email": "browser.discovered@example.com",
            "amount": 77777, "status": "unpaid", "date": "2026-12-25",  # later than all seed data
        })
        assert create_resp.status_code == 200
        new_invoice_id = create_resp.json()["id"]

        cookies = client.post(f"{DEMO_SITE_URL}/login", data={"username": "demo", "password": "demo1234"}).cookies
        client.post(f"{DEMO_SITE_URL}/settings/toggle-api", cookies=cookies)
        assert client.get(f"{DEMO_SITE_URL}/api/status").json()["available"] is False

    try:
        execution = await agent.run_agent(db, "Open the latest unpaid invoice and download it.")
        assert execution.status == "COMPLETED"
        assert execution.api_calls == 0
        assert execution.browser_actions > 0
        assert execution.result["invoice_number"] == new_invoice_id
        assert Path(execution.result["download_path"]).exists()
        assert Path(execution.result["download_path"]).read_text().find(new_invoice_id) != -1
    finally:
        with httpx.Client(timeout=3.0) as client:
            status = client.get(f"{DEMO_SITE_URL}/api/status").json()
            if not status.get("available"):
                cookies = client.post(f"{DEMO_SITE_URL}/login",
                                       data={"username": "demo", "password": "demo1234"}).cookies
                client.post(f"{DEMO_SITE_URL}/settings/toggle-api", cookies=cookies)


@pytest.mark.asyncio
async def test_agent_finds_customer_email_for_newly_added_invoice_via_browser(demo_site_process, db):
    """Same idea as above, but for the 'find the customer's email' goal -
    exercises the browser path's per-invoice detail-page extraction
    (customer-name/customer-email test ids) against a runtime-created
    customer, not just seed data."""
    with httpx.Client(timeout=5.0) as client:
        create_resp = client.post(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS, json={
            "customer_name": "Zephyr Holdings", "customer_email": "contact@zephyrholdings.example",
            "amount": 6000, "status": "paid", "date": "2026-12-30",
        })
        assert create_resp.status_code == 200

        cookies = client.post(f"{DEMO_SITE_URL}/login", data={"username": "demo", "password": "demo1234"}).cookies
        client.post(f"{DEMO_SITE_URL}/settings/toggle-api", cookies=cookies)

    try:
        execution = await agent.run_agent(
            db, "Find the customer associated with the latest invoice and return their email."
        )
        assert execution.status == "COMPLETED"
        assert execution.result["customer_email"] == "contact@zephyrholdings.example"
        assert execution.result["customer_name"] == "Zephyr Holdings"
    finally:
        with httpx.Client(timeout=3.0) as client:
            status = client.get(f"{DEMO_SITE_URL}/api/status").json()
            if not status.get("available"):
                cookies = client.post(f"{DEMO_SITE_URL}/login",
                                       data={"username": "demo", "password": "demo1234"}).cookies
                client.post(f"{DEMO_SITE_URL}/settings/toggle-api", cookies=cookies)


# ---------------------------------------------------------------------------
# Reset behaviour
# ---------------------------------------------------------------------------

def test_demo_reset_restores_original_seed_data(demo_site_process):
    with httpx.Client(timeout=5.0) as client:
        client.post(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS, json={
            "customer_name": "Temp", "customer_email": "temp@example.com",
            "amount": 100, "status": "paid", "date": "2026-09-19",
        })
        listing = client.get(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS).json()
        assert len(listing["invoices"]) == 7

        client.post(f"{DEMO_SITE_URL}/api/demo/reset")
        listing_after = client.get(f"{DEMO_SITE_URL}/api/invoices", headers=API_KEY_HEADERS).json()
        assert len(listing_after["invoices"]) == 6
        ids_after = {i["id"] for i in listing_after["invoices"]}
        assert ids_after == {"inv_1001", "inv_1002", "inv_1003", "inv_1004", "inv_1005", "inv_1006"}
