import pytest
from fastapi.testclient import TestClient

from app.database.session import init_db
from app.main import app

init_db()
client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["llm_mode"] in ("openrouter", "deterministic_fallback")


def test_create_and_get_task():
    resp = client.post("/api/tasks", json={"text": "Find the latest invoice and tell me its invoice number and amount."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["goal"] == "get_latest_invoice_info"

    resp2 = client.get(f"/api/tasks/{body['id']}")
    assert resp2.status_code == 200
    assert resp2.json()["raw_text"] == body["raw_text"]


def test_get_nonexistent_task_returns_404():
    resp = client.get("/api/tasks/does-not-exist")
    assert resp.status_code == 404


def test_list_tasks_returns_array():
    client.post("/api/tasks", json={"text": "Find all unpaid invoices above ₹5,000."})
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_integrations_endpoint_lists_all_three():
    resp = client.get("/api/integrations")
    assert resp.status_code == 200
    names = {i["name"] for i in resp.json()}
    assert names == {"OpenRouter", "Invoice API", "Browser Automation"}


def test_analytics_endpoint_shape():
    resp = client.get("/api/analytics")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("total_executions", "success_rate_pct", "llm_mode", "api_usage_rate_pct"):
        assert key in body


def test_browser_state_empty_before_any_execution():
    resp = client.get("/api/browser/state?execution_id=nonexistent-id")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_agent_stop_reports_not_applicable():
    resp = client.post("/api/agent/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_applicable"


def test_demo_load_returns_sample_tasks():
    resp = client.post("/api/demo/load", json={"reset": False})
    assert resp.status_code == 200
    assert len(resp.json()["sample_tasks"]) == 5


def test_openapi_schema_available():
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert "paths" in resp.json()
