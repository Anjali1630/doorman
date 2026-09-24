from app.services.validator import validate_step, attempt_retarget


def test_navigate_needs_no_element_validation():
    result = validate_step({"action": "navigate", "target": None}, observation=None)
    assert result.passed


def test_click_rejected_when_no_observation_yet():
    result = validate_step(
        {"action": "click", "target": {"role": "button", "name": "Submit"}}, observation=None
    )
    assert not result.passed
    assert "observation" in result.reason.lower()


def test_click_rejected_when_element_missing():
    observation = {"url": "http://x/invoices", "elements": []}
    result = validate_step(
        {"action": "click", "target": {"role": "button", "name": "Download Invoice"}}, observation
    )
    assert not result.passed
    assert "No element matching" in result.reason


def test_click_rejected_when_element_disabled():
    observation = {"url": "http://x", "elements": [
        {"role": "button", "name": "Download Invoice", "visible": True, "enabled": False}
    ]}
    step = {"action": "click", "target": {"role": "button", "name": "Download Invoice"},
            "preconditions": ["element_enabled"]}
    result = validate_step(step, observation)
    assert not result.passed
    assert "disabled" in result.reason


def test_click_rejected_when_element_not_visible():
    observation = {"url": "http://x", "elements": [
        {"role": "button", "name": "Download Invoice", "visible": False, "enabled": True}
    ]}
    step = {"action": "click", "target": {"role": "button", "name": "Download Invoice"},
            "preconditions": ["element_visible"]}
    result = validate_step(step, observation)
    assert not result.passed
    assert "not visible" in result.reason


def test_click_passes_when_element_present_and_ready():
    observation = {"url": "http://x", "elements": [
        {"role": "button", "name": "Download Invoice", "visible": True, "enabled": True}
    ]}
    step = {"action": "click", "target": {"role": "button", "name": "Download Invoice"},
            "preconditions": ["element_exists", "element_visible", "element_enabled"]}
    result = validate_step(step, observation)
    assert result.passed
    assert result.matched_element["name"] == "Download Invoice"


def test_retarget_finds_semantically_similar_element():
    observation = {"url": "http://x", "elements": [
        {"role": "button", "name": "Download Invoice PDF", "visible": True, "enabled": True}
    ]}
    step = {"action": "click", "target": {"role": "button", "name": "Download Invoice"}}
    match = attempt_retarget(step, observation)
    assert match is not None
    assert match["name"] == "Download Invoice PDF"


def test_retarget_returns_none_when_nothing_similar():
    observation = {"url": "http://x", "elements": [
        {"role": "button", "name": "Logout", "visible": True, "enabled": True}
    ]}
    step = {"action": "click", "target": {"role": "button", "name": "Download Invoice"}}
    assert attempt_retarget(step, observation) is None
