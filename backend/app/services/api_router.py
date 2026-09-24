"""
API registry — the "API-first" half of the API-first + browser-fallback
architecture.

For each known goal, this registry says whether a real API exists that can
satisfy it directly, and what request to make. The executor asks this
registry BEFORE falling back to Playwright. Availability is checked live
against the demo site's /api/status endpoint (which the Settings page can
toggle off) so the demo can genuinely show a fallback happening, not a
scripted one.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

DEMO_SITE_BASE_URL = settings.DEMO_SITE_BASE_URL
INVOICE_API_KEY = os.environ.get("INVOICE_API_KEY", "demo-invoice-api-key")


@dataclass
class ApiCapability:
    goal: str
    method: str
    path_template: str
    description: str


API_CAPABILITIES: Dict[str, ApiCapability] = {
    "get_latest_invoice": ApiCapability(
        goal="get_latest_invoice", method="GET", path_template="/api/invoices/latest",
        description="Directly fetches the most recent invoice via the Invoice API.",
    ),
    "list_invoices_filtered": ApiCapability(
        goal="list_invoices_filtered", method="GET", path_template="/api/invoices",
        description="Lists invoices with optional status/min_amount filters via the Invoice API. "
                    "status accepts 'unpaid', 'partially_paid', or 'paid'.",
    ),
    "get_invoice_by_id": ApiCapability(
        goal="get_invoice_by_id", method="GET", path_template="/api/invoices/{invoice_id}",
        description="Fetches a specific invoice by id via the Invoice API, including its "
                    "total_amount/paid_amount/remaining_amount/status/payments.",
    ),
    "download_invoice": ApiCapability(
        goal="download_invoice", method="GET", path_template="/api/invoices/{invoice_id}/download",
        description="Downloads an invoice file directly via the Invoice API.",
    ),
    "get_customer": ApiCapability(
        goal="get_customer", method="GET", path_template="/api/customers/{customer_id}",
        description="Fetches customer details via the Invoice API.",
    ),
    "record_payment": ApiCapability(
        goal="record_payment", method="POST", path_template="/api/invoices/{invoice_id}/payments",
        description="Records a payment (amount, date) against an invoice via the Invoice API. "
                    "Rejects payments greater than the invoice's remaining balance.",
    ),
}


def is_api_available() -> bool:
    """Live check against the demo site - lets the Settings toggle genuinely
    force a browser-fallback demonstration."""
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{DEMO_SITE_BASE_URL}/api/status")
            if resp.status_code != 200:
                return False
            return bool(resp.json().get("available", False))
    except httpx.RequestError:
        return False


def capability_for(goal: str) -> Optional[ApiCapability]:
    return API_CAPABILITIES.get(goal)


def _error_detail(resp) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])
    except ValueError:
        pass
    return resp.text[:200]


def call_api(capability: ApiCapability, path_params: Optional[Dict[str, Any]] = None,
             query_params: Optional[Dict[str, Any]] = None,
             json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Executes the real HTTP call against the demo Invoice API and returns a
    normalized result. Raises ApiCallError on failure so the executor can
    classify and decide whether to retry or fall back to the browser.

    method dispatch: GET capabilities (the original 5) call client.get(...)
    exactly as before - untouched code path, so existing GET-based tests
    keep working unchanged. POST capabilities (currently just
    "record_payment") call client.post(..., json=json_body) instead."""
    path = capability.path_template.format(**(path_params or {}))
    url = f"{DEMO_SITE_BASE_URL}{path}"
    headers = {"X-API-Key": INVOICE_API_KEY}
    method = (capability.method or "GET").upper()

    try:
        with httpx.Client(timeout=10.0) as client:
            if method == "POST":
                resp = client.post(url, headers=headers, json=json_body or {})
            else:
                resp = client.get(url, headers=headers, params=query_params or {})
    except httpx.RequestError as e:
        raise ApiCallError(f"Network error calling {url}: {e}", category="network_error")

    if resp.status_code == 503:
        raise ApiCallError("Invoice API reported unavailable", category="api_unavailable")
    if resp.status_code == 401:
        raise ApiCallError("Invoice API rejected credentials", category="authentication_error")
    if resp.status_code == 404:
        raise ApiCallError("Requested resource not found via API", category="not_found")
    if resp.status_code == 422:
        detail = _error_detail(resp)
        raise ApiCallError(f"Invoice API rejected the request: {detail}", category="validation_error")
    if resp.status_code >= 400:
        raise ApiCallError(f"API returned HTTP {resp.status_code}: {resp.text[:200]}", category="api_error")

    is_download = "download" in path
    if is_download:
        return {
            "status_code": resp.status_code,
            "content_type": resp.headers.get("content-type"),
            "content": resp.text,
            "filename": path.rsplit("/", 2)[-2] + ".txt",
        }

    try:
        return {"status_code": resp.status_code, "json": resp.json()}
    except ValueError:
        raise ApiCallError("API returned malformed (non-JSON) response", category="extraction_error")


class ApiCallError(Exception):
    def __init__(self, message: str, category: str = "api_error"):
        super().__init__(message)
        self.category = category
