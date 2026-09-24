import pytest

from app.services.planner import _deterministic_understand, understand_task


def test_understands_login_and_download():
    u = _deterministic_understand("Log into the website and download the latest invoice.")
    assert u.goal == "login_and_download_latest_invoice"


def test_understands_latest_invoice_info():
    u = _deterministic_understand("Find the latest invoice and tell me its invoice number and amount.")
    assert u.goal == "get_latest_invoice_info"


def test_understands_unpaid_above_threshold():
    u = _deterministic_understand("Find all unpaid invoices above ₹10,000.")
    assert u.goal == "list_unpaid_invoices_above"
    assert any("10000" in c for c in u.constraints)


@pytest.mark.parametrize("text", [
    "Show me all unpaid invoices",
    "Show me all unpaid invoices and the customer names associated with them",
    "Who has not paid yet?",
    "Who has not paid the amount yet?",
    "Who hasn't paid the amount yet?",
    "Which customers haven't paid?",
    "Which invoices are unpaid?",
    "Show unpaid customers",
    "Who still owes money?",
    "Show me pending unpaid invoices",
])
def test_understands_general_unpaid_queries(text):
    u = _deterministic_understand(text)
    assert u.goal == "list_unpaid_invoices"
    assert u.entities == ["invoice", "customer"]
    assert "status = unpaid" in u.constraints
    for field in ("invoice_number", "customer_name", "amount", "status"):
        assert field in u.expected_output


def test_general_unpaid_rule_does_not_override_threshold_rule():
    """The exact regression this fix must not introduce: a task with an
    explicit amount threshold must keep resolving to the more specific
    list_unpaid_invoices_above goal, not the new general one."""
    u = _deterministic_understand("Find all unpaid invoices above ₹10,000")
    assert u.goal == "list_unpaid_invoices_above"
    assert u.goal != "list_unpaid_invoices"


def test_general_unpaid_rule_does_not_override_download_rule():
    u = _deterministic_understand("Open the latest unpaid invoice and download it.")
    assert u.goal == "download_latest_unpaid_invoice"


def test_general_unpaid_rule_does_not_override_invoice_specific_balance_query():
    u = _deterministic_understand("How much is still pending for invoice INV-1005?")
    assert u.goal == "get_invoice_remaining_balance"


def test_understands_download_latest_unpaid():
    u = _deterministic_understand("Open the latest unpaid invoice and download it.")
    assert u.goal == "download_latest_unpaid_invoice"


def test_understands_customer_email():
    u = _deterministic_understand("Find the customer associated with the latest invoice and return their email.")
    assert u.goal == "get_latest_invoice_customer_email"


def test_unknown_task_does_not_guess():
    u = _deterministic_understand("Book me a flight to Tokyo.")
    assert u.goal == "unknown"


def test_understand_task_falls_back_without_api_key(monkeypatch):
    from app.services.llm import llm_client
    monkeypatch.setattr(llm_client, "api_key", "")
    understanding, produced_by = understand_task("Find the latest invoice and tell me its invoice number and amount.")
    assert produced_by == "deterministic_fallback"
    assert understanding.goal == "get_latest_invoice_info"


# ---------------------------------------------------------------------------
# understand_task(): LLM "unknown" must not be the final answer if the
# deterministic matcher can still resolve the task (the bug this fix
# addresses). A valid, non-"unknown" LLM goal must still win outright.
# ---------------------------------------------------------------------------

def test_llm_unknown_falls_back_to_deterministic_when_it_can_resolve(monkeypatch):
    from app.services.llm import llm_client
    monkeypatch.setattr(llm_client, "api_key", "fake-test-key")

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        return {"goal": "unknown", "entities": [], "constraints": [], "expected_output": []}

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    understanding, produced_by = understand_task("Show me all unpaid invoices")

    assert understanding.goal == "list_unpaid_invoices"
    assert produced_by == "deterministic_fallback"


def test_llm_unknown_stays_unknown_when_deterministic_also_cannot_resolve(monkeypatch):
    from app.services.llm import llm_client
    monkeypatch.setattr(llm_client, "api_key", "fake-test-key")

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        return {"goal": "unknown", "entities": [], "constraints": [], "expected_output": []}

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    understanding, produced_by = understand_task("Book me a flight to Tokyo.")

    assert understanding.goal == "unknown"
    assert produced_by == "deterministic_fallback"


def test_llm_valid_known_goal_is_never_overridden_by_deterministic(monkeypatch):
    """A valid LLM goal must win even when the deterministic matcher would
    have picked a DIFFERENT goal for the same text - the LLM is trusted
    outright once it returns something in KNOWN_GOALS."""
    from app.services.llm import llm_client
    monkeypatch.setattr(llm_client, "api_key", "fake-test-key")

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        return {"goal": "list_partially_paid_invoices", "entities": ["invoice"],
                "constraints": [], "expected_output": ["invoices", "count"]}

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    # Deterministically, this text would resolve to list_unpaid_invoices -
    # but the LLM's valid answer must be used as-is instead.
    understanding, produced_by = understand_task("Show me all unpaid invoices")

    assert understanding.goal == "list_partially_paid_invoices"
    assert produced_by == "openrouter"


def test_llm_error_still_falls_back_to_deterministic(monkeypatch):
    from app.services.llm import llm_client
    from app.services.llm import LLMError
    monkeypatch.setattr(llm_client, "api_key", "fake-test-key")

    def fake_chat_json(system_prompt, user_prompt, timeout=30.0):
        raise LLMError("rate limited", category="rate_limit")

    monkeypatch.setattr(llm_client, "chat_json", fake_chat_json)
    understanding, produced_by = understand_task("Show me all unpaid invoices")

    assert understanding.goal == "list_unpaid_invoices"
    assert produced_by == "deterministic_fallback"
