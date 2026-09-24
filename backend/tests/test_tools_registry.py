import pytest

from app.tools.registry import (
    ALL_TOOLS, BROWSER_TOOLS, API_TOOLS, TOOL_DESCRIPTIONS,
    ToolExecutionError, _locator_for,
)


def test_all_required_tools_are_registered():
    required = {
        "navigate", "click", "type", "select", "press", "wait", "extract",
        "inspect_page", "screenshot", "download", "api_request", "go_back", "go_forward",
    }
    assert required.issubset(ALL_TOOLS.keys())


def test_every_tool_has_a_description():
    for name in ALL_TOOLS:
        assert name in TOOL_DESCRIPTIONS
        assert len(TOOL_DESCRIPTIONS[name]) > 0


def test_browser_and_api_tools_partition_all_tools():
    assert set(BROWSER_TOOLS) | set(API_TOOLS) == set(ALL_TOOLS)
    assert set(BROWSER_TOOLS).isdisjoint(set(API_TOOLS))


def test_locator_for_raises_on_empty_target():
    class FakePage:
        pass
    with pytest.raises(ToolExecutionError):
        _locator_for(FakePage(), {})


def test_locator_for_raises_on_none_target():
    class FakePage:
        pass
    with pytest.raises(ToolExecutionError):
        _locator_for(FakePage(), None)


def test_locator_for_prefers_role_and_name(monkeypatch):
    calls = {}

    class FakePage:
        def get_by_role(self, role, name=None, exact=False):
            calls["role"] = role
            calls["name"] = name
            return "role_locator"

        def get_by_label(self, *a, **k):
            raise AssertionError("should not be called when role+name are present")

    locator = _locator_for(FakePage(), {"role": "button", "name": "Download Invoice"})
    assert locator == "role_locator"
    assert calls == {"role": "button", "name": "Download Invoice"}


def test_locator_for_falls_back_to_css():
    class FakePage:
        def locator(self, css):
            return f"css:{css}"

    locator = _locator_for(FakePage(), {"css": "a.btn"})
    assert locator == "css:a.btn"
