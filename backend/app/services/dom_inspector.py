"""
Inspects the live page and returns a structured snapshot of what's actually
there. This is the ground truth the validator checks planned actions
against - the planner/LLM never gets to invent elements.
"""
from __future__ import annotations

from typing import Any, Dict, List

from playwright.async_api import Page

# Elements we care about for automation purposes.
_INTERESTING_SELECTORS = [
    ("button", "button, [role=button], input[type=submit], a.btn"),
    ("link", "a"),
    ("textbox", "input[type=text], input[type=email], input[type=password], "
                "input[type=date], input:not([type]), textarea"),
    ("combobox", "select"),
    ("heading", "h1, h2, h3"),
    ("table", "table"),
]


async def inspect_page(page: Page) -> Dict[str, Any]:
    url = page.url
    title = await page.title()
    elements: List[Dict[str, Any]] = []

    for role, selector in _INTERESTING_SELECTORS:
        locator = page.locator(selector)
        count = await locator.count()
        # Cap per-category to keep payloads small and avoid pathological pages.
        for i in range(min(count, 60)):
            el = locator.nth(i)
            try:
                visible = await el.is_visible()
            except Exception:
                visible = False
            try:
                enabled = await el.is_enabled()
            except Exception:
                enabled = False
            try:
                name = (await el.inner_text()).strip()
            except Exception:
                name = ""
            if not name:
                try:
                    name = (await el.get_attribute("placeholder")) or ""
                except Exception:
                    name = ""
            if not name:
                try:
                    name = (await el.get_attribute("aria-label")) or ""
                except Exception:
                    name = ""
            try:
                test_id = await el.get_attribute("data-testid")
            except Exception:
                test_id = None
            try:
                href = await el.get_attribute("href") if role == "link" else None
            except Exception:
                href = None

            elements.append({
                "role": role,
                "name": name[:120],
                "visible": visible,
                "enabled": enabled,
                "test_id": test_id,
                "href": href,
                "index": i,
                "selector": selector,
            })

    table_rows: List[Dict[str, Any]] = []
    row_locator = page.locator("tr[data-invoice-id]")
    row_count = await row_locator.count()
    for i in range(row_count):
        row = row_locator.nth(i)
        try:
            invoice_id = await row.get_attribute("data-invoice-id")
            cells = await row.locator("td").all_inner_texts()
            cells_lower = " ".join(cells).lower()
            # IMPORTANT: check the more specific "partially paid" substring
            # BEFORE "paid" - "partially paid" (or "partially_paid") itself
            # contains "paid" as a substring, so checking "paid" first would
            # misclassify every partially-paid row as fully paid.
            if "partially paid" in cells_lower or "partially_paid" in cells_lower:
                status = "partially_paid"
            elif "unpaid" in cells_lower:
                status = "unpaid"
            elif "paid" in cells_lower:
                status = "paid"
            else:
                status = "unknown"
            table_rows.append({"invoice_id": invoice_id, "cells": [c.strip() for c in cells], "status": status})
        except Exception:
            continue

    return {"url": url, "title": title, "elements": elements, "table_rows": table_rows}


def find_matching_element(observation: Dict[str, Any], role: str = None, name: str = None) -> Dict[str, Any] | None:
    """Pure-Python matcher used by the validator: does an element matching
    the planned target actually exist in the last observation?"""
    name_norm = (name or "").strip().lower()
    best = None
    for el in observation.get("elements", []):
        if role and el.get("role") != role:
            continue
        el_name = (el.get("name") or "").strip().lower()
        if name_norm and name_norm not in el_name and el_name not in name_norm:
            continue
        best = el
        if el.get("visible") and el.get("enabled"):
            return el
    return best
