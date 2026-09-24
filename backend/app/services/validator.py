"""
Pre-execution validation (section 10/11 of the spec).

Before ANY element-targeting action is executed, this module checks the
planned target against the most recent real DOM observation. The plan is
only ever a *proposal* - if the element isn't actually there, visible, and
enabled, the action is rejected and never reaches Playwright. This is what
prevents the agent from clicking/typing into elements the LLM imagined.

Actions with no element target (navigate, wait, press, api_request,
inspect_page, screenshot) are validated more lightly - they can't hallucinate
an element, but navigate is still checked against the domain allow-list by
the browser tool itself.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.services.dom_inspector import find_matching_element

ELEMENT_ACTIONS = {"click", "type", "select", "download"}


class ValidationResult:
    def __init__(self, passed: bool, reason: str, matched_element: Optional[Dict[str, Any]] = None):
        self.passed = passed
        self.reason = reason
        self.matched_element = matched_element


def validate_step(step: Dict[str, Any], observation: Optional[Dict[str, Any]]) -> ValidationResult:
    action = step.get("action")

    if action not in ELEMENT_ACTIONS:
        return ValidationResult(True, "No element target required for this action.")

    target = step.get("target") or {}
    if observation is None:
        return ValidationResult(False, "No page observation available yet - cannot verify target exists.")

    role = target.get("role")
    name = target.get("name")

    element = find_matching_element(observation, role=role, name=name)
    if element is None:
        return ValidationResult(
            False,
            f"No element matching role='{role}' name='{name}' was found on the current page "
            f"({observation.get('url')}).",
        )

    preconditions = set(step.get("preconditions") or [])
    if "element_visible" in preconditions and not element.get("visible", False):
        return ValidationResult(False, f"Matched element '{element.get('name')}' exists but is not visible.",
                                 matched_element=element)
    if "element_enabled" in preconditions and not element.get("enabled", False):
        return ValidationResult(False, f"Matched element '{element.get('name')}' exists but is disabled.",
                                 matched_element=element)

    return ValidationResult(True, f"Matched element '{element.get('name')}' (role={element.get('role')}).",
                             matched_element=element)


def attempt_retarget(step: Dict[str, Any], observation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """When the exact target isn't found, look for a semantically similar
    element (same role, loosely-matching name) before giving up and
    escalating to re-planning. This is the 'safe re-targeting' step from
    section 10."""
    target = step.get("target") or {}
    role = target.get("role")
    candidates = [e for e in observation.get("elements", []) if e.get("role") == role and e.get("visible")]
    if not candidates:
        return None
    name = (target.get("name") or "").lower()
    words = set(name.split())
    best, best_score = None, 0
    for el in candidates:
        el_words = set((el.get("name") or "").lower().split())
        score = len(words & el_words)
        if score > best_score:
            best, best_score = el, score
    return best if best_score > 0 else None
