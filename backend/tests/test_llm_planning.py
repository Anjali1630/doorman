"""
Unit tests for planner.generate_action_plan() and planner.generate_next_step()
- the genuinely LLM-driven planning and re-planning functions. The LLM
client itself is mocked here (no network, no key needed to run these), but
that's the correct boundary: these tests prove the plumbing (prompt
construction, response validation, fallback-on-failure) is correct, while
tests/test_integration_live.py separately proves the SAME functions drive a
real Playwright browser correctly when wired end to end.
"""
import pytest

from app.schemas.schemas import ActionPlan, PlanStepSchema, TaskUnderstanding
from app.services import planner
from app.services.llm import llm_client, LLMError


@pytest.fixture(autouse=True)
def _restore_llm_client():
    original_key = llm_client.api_key
    yield
    llm_client.api_key = original_key


UNDERSTANDING = TaskUnderstanding(
    goal="get_latest_invoice_info", entities=["invoice"], constraints=[],
    expected_output=["invoice_number", "amount"],
)


def test_generate_action_plan_returns_none_without_key():
    llm_client.api_key = ""

    def _should_not_be_called(*a, **k):
        raise AssertionError("chat_json must not be called when no key is configured")

    original = llm_client.chat_json
    llm_client.chat_json = _should_not_be_called
    try:
        plan, reason = planner.generate_action_plan("Find the latest invoice", UNDERSTANDING)
    finally:
        llm_client.chat_json = original
    assert plan is None
    assert reason == "no_llm_key"


def test_generate_action_plan_parses_valid_llm_response(monkeypatch):
    llm_client.api_key = "fake-key"
    captured = {}

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return {
            "strategy": "api_first",
            "steps": [
                {"step_id": 1, "action": "api_request", "execution_method": "api",
                 "api_meta": {"goal": "get_latest_invoice"}, "expected_result": "fetched latest invoice"},
            ],
        }

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    plan, produced_by = planner.generate_action_plan("Find the latest invoice", UNDERSTANDING)

    assert produced_by == "openrouter"
    assert isinstance(plan, ActionPlan)
    assert plan.strategy == "api_first"
    assert len(plan.steps) == 1
    assert plan.steps[0].action == "api_request"
    assert plan.steps[0].api_meta == {"goal": "get_latest_invoice"}
    # The prompt actually names the real tools and the real task - not a stub.
    assert "api_request" in captured["system_prompt"]
    assert "Find the latest invoice" in captured["user_prompt"]


def test_generate_action_plan_includes_dom_snapshot_when_given(monkeypatch):
    llm_client.api_key = "fake-key"
    captured = {}

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        captured["system_prompt"] = system_prompt
        return {"strategy": "browser_fallback", "steps": [
            {"step_id": 1, "action": "click", "target": {"role": "button", "name": "Download Invoice"},
             "preconditions": ["element_exists"], "execution_method": "browser"},
        ]}

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    dom_snapshot = {"url": "http://x/invoices/inv_1", "elements": [
        {"role": "button", "name": "Download Invoice", "visible": True, "enabled": True},
    ]}
    plan, _ = planner.generate_action_plan("Download it", UNDERSTANDING, dom_snapshot=dom_snapshot)
    assert plan is not None
    assert "Download Invoice" in captured["system_prompt"]
    assert "inv_1" in captured["system_prompt"]


def test_generate_action_plan_falls_back_on_malformed_json(monkeypatch):
    llm_client.api_key = "fake-key"

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        raise LLMError("could not parse JSON from model output", category="malformed_response")

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    plan, reason = planner.generate_action_plan("Find the latest invoice", UNDERSTANDING)
    assert plan is None
    assert "malformed" in reason.lower() or "parse" in reason.lower()


def test_generate_action_plan_falls_back_on_schema_violation(monkeypatch):
    llm_client.api_key = "fake-key"

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        # Invalid: action is not one of the registered tool names.
        return {"strategy": "browser_fallback", "steps": [
            {"step_id": 1, "action": "delete_everything", "execution_method": "browser"},
        ]}

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    plan, reason = planner.generate_action_plan("Find the latest invoice", UNDERSTANDING)
    assert plan is None


def test_generate_action_plan_falls_back_on_empty_steps(monkeypatch):
    llm_client.api_key = "fake-key"

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        return {"strategy": "hybrid", "steps": []}

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    plan, reason = planner.generate_action_plan("Find the latest invoice", UNDERSTANDING)
    assert plan is None
    assert "zero steps" in reason


def test_generate_next_step_returns_none_without_key():
    llm_client.api_key = ""
    step = planner.generate_next_step("task", [], None, "some error")
    assert step is None


def test_generate_next_step_parses_valid_response_and_includes_real_context(monkeypatch):
    llm_client.api_key = "fake-key"
    captured = {}

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        captured["system_prompt"] = system_prompt
        return {"step_id": 2, "action": "navigate", "value": "http://127.0.0.1:8001/invoices",
                "execution_method": "browser", "expected_result": "navigated to invoices instead"}

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    dom_snapshot = {"url": "http://127.0.0.1:8001/dashboard", "elements": [
        {"role": "link", "name": "Invoices", "visible": True, "enabled": True},
    ]}
    step = planner.generate_next_step(
        "Find the latest invoice", [{"step": {"action": "click"}, "status": "failed"}],
        dom_snapshot, "No element matching role='button' name='Nonexistent Magic Button' was found",
    )
    assert isinstance(step, PlanStepSchema)
    assert step.action == "navigate"
    assert step.value == "http://127.0.0.1:8001/invoices"
    # The REAL error and REAL DOM actually reached the prompt - this is what
    # makes the re-planning "genuinely LLM-driven using latest DOM/error
    # state" rather than a templated message.
    assert "Nonexistent Magic Button" in captured["system_prompt"]
    assert "Invoices" in captured["system_prompt"]


def test_generate_next_step_returns_none_when_llm_gives_up(monkeypatch):
    llm_client.api_key = "fake-key"

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        return {"give_up": True, "reason": "No safe element found on this page"}

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    step = planner.generate_next_step("task", [], {"url": "x", "elements": []}, "some error")
    assert step is None


def test_generate_next_step_falls_back_on_malformed_response(monkeypatch):
    llm_client.api_key = "fake-key"

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        return {"action": "not_a_real_tool"}  # missing step_id, invalid action

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    step = planner.generate_next_step("task", [], None, "some error")
    assert step is None
