"""
Tool registry. Every action the agent takes - browser or API - goes through
one of these tools. The LLM/planner may only *select* a tool by name and
supply arguments; it never touches Playwright directly (section 23/7 of
the spec). Each tool classifies its own failures into the error categories
the retry/replan logic understands.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from app.core.config import settings
from app.services import api_router
from app.services.dom_inspector import inspect_page, find_matching_element


class ToolExecutionError(Exception):
    """Raised by a tool when it fails. `category` matches the error
    taxonomy in section 16 of the spec so the executor can decide what to
    do next (retry / re-inspect / re-plan / ask user)."""
    def __init__(self, message: str, category: str = "unexpected_error"):
        super().__init__(message)
        self.category = category


@dataclass
class ToolResult:
    success: bool
    data: Dict[str, Any]
    error: Optional[str] = None
    error_category: Optional[str] = None


def _locator_for(page: Page, target: Dict[str, Any]):
    """Implements the preferred targeting order from section 8: role+name,
    then label, then test id, then css, then xpath."""
    if not target:
        raise ToolExecutionError("No target provided for element action", "validation_error")
    role = target.get("role")
    name = target.get("name")
    if role and name:
        return page.get_by_role(role, name=name, exact=False)
    if target.get("label"):
        return page.get_by_label(target["label"], exact=False)
    if target.get("test_id"):
        return page.get_by_test_id(target["test_id"])
    if target.get("css"):
        return page.locator(target["css"])
    if target.get("xpath"):
        return page.locator(f"xpath={target['xpath']}")
    if name:
        return page.get_by_text(name, exact=False)
    raise ToolExecutionError("Target has no usable selector fields", "validation_error")


async def tool_navigate(page: Page, args: Dict[str, Any]) -> ToolResult:
    from app.services.browser import BrowserSession, DomainNotAllowedError
    url = args.get("url") or args.get("value")
    if not url:
        raise ToolExecutionError("navigate requires a url", "validation_error")
    try:
        BrowserSession.assert_domain_allowed(url)
    except DomainNotAllowedError as e:
        raise ToolExecutionError(str(e), "navigation_error")
    try:
        await page.goto(url, timeout=settings.ACTION_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise ToolExecutionError(f"Timed out navigating to {url}", "timeout")
    return ToolResult(True, {"url": page.url})


async def tool_click(page: Page, args: Dict[str, Any]) -> ToolResult:
    locator = _locator_for(page, args.get("target", {}))
    try:
        await locator.first.click(timeout=settings.ACTION_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise ToolExecutionError("Element not clickable within timeout", "timeout")
    except Exception as e:
        raise ToolExecutionError(f"Click failed: {e}", "element_not_found")
    return ToolResult(True, {"clicked": args.get("target")})


async def tool_type(page: Page, args: Dict[str, Any]) -> ToolResult:
    locator = _locator_for(page, args.get("target", {}))
    value = args.get("value", "")
    try:
        await locator.first.fill(value, timeout=settings.ACTION_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise ToolExecutionError("Field not fillable within timeout", "timeout")
    except Exception as e:
        raise ToolExecutionError(f"Type failed: {e}", "element_not_found")
    return ToolResult(True, {"typed_into": args.get("target")})


async def tool_select(page: Page, args: Dict[str, Any]) -> ToolResult:
    locator = _locator_for(page, args.get("target", {}))
    value = args.get("value", "")
    try:
        await locator.first.select_option(value, timeout=settings.ACTION_TIMEOUT_MS)
    except Exception as e:
        raise ToolExecutionError(f"Select failed: {e}", "element_not_found")
    return ToolResult(True, {"selected": value})


async def tool_press(page: Page, args: Dict[str, Any]) -> ToolResult:
    key = args.get("value", "Enter")
    try:
        await page.keyboard.press(key)
    except Exception as e:
        raise ToolExecutionError(f"Key press failed: {e}", "unexpected_error")
    return ToolResult(True, {"pressed": key})


async def tool_wait(page: Page, args: Dict[str, Any]) -> ToolResult:
    ms = int(args.get("value") or 500)
    await asyncio.sleep(min(ms, 5000) / 1000)
    return ToolResult(True, {"waited_ms": ms})


async def tool_extract(page: Page, args: Dict[str, Any]) -> ToolResult:
    target = args.get("target") or {}
    try:
        if target:
            locator = _locator_for(page, target)
            text = await locator.first.inner_text(timeout=settings.ACTION_TIMEOUT_MS)
        else:
            text = await page.inner_text("body")
    except Exception as e:
        raise ToolExecutionError(f"Extraction failed: {e}", "extraction_error")
    return ToolResult(True, {"text": text.strip()[:2000]})


async def tool_inspect_page(page: Page, args: Dict[str, Any]) -> ToolResult:
    obs = await inspect_page(page)
    return ToolResult(True, obs)


async def tool_screenshot(page: Page, args: Dict[str, Any]) -> ToolResult:
    import base64
    try:
        data = await page.screenshot(type="png")
    except Exception as e:
        raise ToolExecutionError(f"Screenshot failed: {e}", "unexpected_error")
    return ToolResult(True, {"screenshot_base64": base64.b64encode(data).decode()[:200] + "...(truncated)"})


async def tool_download(page: Page, args: Dict[str, Any]) -> ToolResult:
    import uuid
    from pathlib import Path as _Path

    artifacts_dir = _Path(__file__).resolve().parent.parent.parent.parent / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    target = args.get("target", {})
    locator = _locator_for(page, target)
    try:
        async with page.expect_download(timeout=settings.ACTION_TIMEOUT_MS) as dl_info:
            await locator.first.click(timeout=settings.ACTION_TIMEOUT_MS)
        download = await dl_info.value
        suggested = download.suggested_filename
        # IMPORTANT: Playwright's download.path() points at a temp file that
        # is deleted once the browser context/browser closes. Since each
        # execution's BrowserSession is closed right after the task
        # finishes, we must persist the file with save_as() BEFORE that
        # happens, or "verify download" checks done afterwards would
        # (incorrectly) fail even though the download itself succeeded.
        permanent_path = artifacts_dir / f"{uuid.uuid4().hex[:10]}_{suggested}"
        await download.save_as(str(permanent_path))
    except PlaywrightTimeoutError:
        raise ToolExecutionError("Download did not complete within timeout", "download_error")
    except Exception as e:
        raise ToolExecutionError(f"Download failed: {e}", "download_error")
    return ToolResult(True, {"file_path": str(permanent_path), "filename": suggested})


async def tool_go_back(page: Page, args: Dict[str, Any]) -> ToolResult:
    await page.go_back(timeout=settings.ACTION_TIMEOUT_MS)
    return ToolResult(True, {"url": page.url})


async def tool_go_forward(page: Page, args: Dict[str, Any]) -> ToolResult:
    await page.go_forward(timeout=settings.ACTION_TIMEOUT_MS)
    return ToolResult(True, {"url": page.url})


async def tool_api_request(page: Optional[Page], args: Dict[str, Any]) -> ToolResult:
    goal = args.get("goal")
    capability = api_router.capability_for(goal)
    if not capability:
        raise ToolExecutionError(f"No registered API capability for goal '{goal}'", "api_error")
    try:
        result = api_router.call_api(
            capability,
            path_params=args.get("path_params"),
            query_params=args.get("query_params"),
            json_body=args.get("json_body"),
        )
    except api_router.ApiCallError as e:
        raise ToolExecutionError(str(e), e.category)
    return ToolResult(True, result)


BROWSER_TOOLS = {
    "navigate": tool_navigate,
    "click": tool_click,
    "type": tool_type,
    "select": tool_select,
    "press": tool_press,
    "wait": tool_wait,
    "extract": tool_extract,
    "inspect_page": tool_inspect_page,
    "screenshot": tool_screenshot,
    "download": tool_download,
    "go_back": tool_go_back,
    "go_forward": tool_go_forward,
}

API_TOOLS = {
    "api_request": tool_api_request,
}

ALL_TOOLS = {**BROWSER_TOOLS, **API_TOOLS}

TOOL_DESCRIPTIONS = {
    "navigate": "Navigate the browser to a URL.",
    "click": "Click an element identified by role/name/label/test_id/css.",
    "type": "Type text into an input identified by role/name/label/test_id/css.",
    "select": "Choose an option in a <select> element.",
    "press": "Press a keyboard key (e.g. Enter).",
    "wait": "Wait a bounded number of milliseconds for the UI to settle.",
    "extract": "Extract visible text from an element or the page body.",
    "inspect_page": "Take a structured snapshot of the current page's interactive elements.",
    "screenshot": "Capture a screenshot of the current page.",
    "download": "Click a download trigger and capture the resulting file.",
    "go_back": "Navigate back in browser history.",
    "go_forward": "Navigate forward in browser history.",
    "api_request": "Call a registered REST API endpoint directly instead of using the browser. "
                   "For POST capabilities (e.g. record_payment), include a json_body dict.",
}
