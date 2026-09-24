"""
Demo Business Portal
=====================
A small, realistic local website used as the browser-automation fallback
target, PLUS a genuine JSON "Invoice API" used by the API-first path.

Run with:  uvicorn demo_site.main:app --port 8001
"""
import datetime
import io
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from demo_site import data

APP_DIR = Path(__file__).resolve().parent
DEMO_USERNAME = os.environ.get("DEMO_SITE_USERNAME", "demo")
DEMO_PASSWORD = os.environ.get("DEMO_SITE_PASSWORD", "demo1234")
INVOICE_API_KEY = os.environ.get("INVOICE_API_KEY", "demo-invoice-api-key")

app = FastAPI(title="Demo Business Portal")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

# Simple in-memory session store: cookie value -> True
_SESSIONS = set()

# Toggle used by the demo ("disable API to show browser fallback")
API_ENABLED = {"value": True}


def is_authed(request: Request) -> bool:
    sid = request.cookies.get("session_id")
    return sid in _SESSIONS


def require_auth_page(request: Request):
    if not is_authed(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


# --------------------------------------------------------------------
# HTML pages (Playwright automation target)
# --------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    if is_authed(request):
        return RedirectResponse("/dashboard")
    return RedirectResponse("/login")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == DEMO_USERNAME and password == DEMO_PASSWORD:
        sid = uuid.uuid4().hex
        _SESSIONS.add(sid)
        resp = RedirectResponse("/dashboard", status_code=303)
        resp.set_cookie("session_id", sid, httponly=True)
        return resp
    return templates.TemplateResponse(
        request, "login.html", {"error": "Invalid username or password"}, status_code=401
    )


@app.get("/logout")
def logout(request: Request):
    sid = request.cookies.get("session_id")
    _SESSIONS.discard(sid)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session_id")
    return resp


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    if not is_authed(request):
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"invoice_count": len(data.INVOICES), "customer_count": len(data.CUSTOMERS)},
    )


@app.get("/invoices", response_class=HTMLResponse)
def invoices_page(request: Request):
    if not is_authed(request):
        return RedirectResponse("/login")
    invoices = sorted(data.INVOICES, key=lambda i: i["date"], reverse=True)
    enriched = []
    for inv in invoices:
        c = data.customer_for(inv["customer_id"])
        enriched.append({**inv, "customer_name": c["name"] if c else "Unknown"})
    return templates.TemplateResponse(request, "invoices.html", {
        "invoices": enriched, "status_labels": data.STATUS_LABELS,
    })


def _default_new_invoice_form() -> dict:
    return {
        "invoice_number": data.generate_invoice_id(),
        "customer_name": "",
        "customer_email": "",
        "amount": "",
        "status": "unpaid",
        "date": str(datetime.date.today()),
    }


def _validate_invoice_form(invoice_number: str, customer_name: str, customer_email: str,
                            amount: str, status: str, date: str) -> Optional[str]:
    """Returns an error message string, or None if the form is valid."""
    if not invoice_number.strip():
        return "Invoice number is required."
    if data.invoice_id_exists(invoice_number.strip()):
        return f"An invoice with number '{invoice_number.strip()}' already exists."
    if not customer_name.strip():
        return "Customer name is required."
    if not customer_email.strip() or "@" not in customer_email:
        return "A valid customer email is required."
    try:
        amount_value = float(amount)
    except (TypeError, ValueError):
        return "Amount must be a number."
    if amount_value <= 0:
        return "Amount must be greater than zero."
    if status not in ("paid", "unpaid"):
        return "Status must be 'paid' or 'unpaid'."
    try:
        datetime.date.fromisoformat(date)
    except (TypeError, ValueError):
        return "Date must be a valid date."
    return None


@app.get("/invoices/new", response_class=HTMLResponse)
def new_invoice_page(request: Request):
    if not is_authed(request):
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "invoice_new.html", {
        "error": None, "form": _default_new_invoice_form(),
    })


@app.post("/invoices/new", response_class=HTMLResponse)
def new_invoice_submit(
    request: Request,
    invoice_number: str = Form(default=""),
    customer_name: str = Form(default=""),
    customer_email: str = Form(default=""),
    amount: str = Form(default=""),
    status: str = Form(default=""),
    date: str = Form(default=""),
):
    if not is_authed(request):
        return RedirectResponse("/login")

    form_values = {
        "invoice_number": invoice_number, "customer_name": customer_name,
        "customer_email": customer_email, "amount": amount, "status": status, "date": date,
    }
    error = _validate_invoice_form(invoice_number, customer_name, customer_email, amount, status, date)
    if error:
        return templates.TemplateResponse(
            request, "invoice_new.html", {"error": error, "form": form_values}, status_code=400
        )

    customer = data.find_or_create_customer(customer_name, customer_email)
    invoice = data.add_invoice(invoice_number, customer["id"], float(amount), status, date)
    return RedirectResponse(f"/invoices/{invoice['id']}", status_code=303)


@app.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_id: str):
    if not is_authed(request):
        return RedirectResponse("/login")
    inv = data.invoice_by_id(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    customer = data.customer_for(inv["customer_id"])
    return templates.TemplateResponse(request, "invoice_detail.html", {
        "invoice": inv, "customer": customer, "status_labels": data.STATUS_LABELS,
        "payment_error": None, "payment_form": None, "today": str(datetime.date.today()),
    })


def _validate_payment_form(amount: str, date: str) -> Optional[str]:
    """Returns an error message string, or None if the form is valid.
    (The overpayment/invoice-not-found checks happen inside
    data.record_payment() itself - this only covers what can be checked
    before even trying to record it.)"""
    try:
        amount_value = float(amount)
    except (TypeError, ValueError):
        return "Payment amount must be a number."
    if amount_value <= 0:
        return "Payment amount must be greater than zero."
    try:
        datetime.date.fromisoformat(date)
    except (TypeError, ValueError):
        return "Payment date must be a valid date."
    return None


@app.post("/invoices/{invoice_id}/payments", response_class=HTMLResponse)
def record_payment_submit(
    request: Request, invoice_id: str,
    payment_amount: str = Form(default=""),
    payment_date: str = Form(default=""),
):
    if not is_authed(request):
        return RedirectResponse("/login")
    inv = data.invoice_by_id(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    error = _validate_payment_form(payment_amount, payment_date)
    if error is None:
        try:
            data.record_payment(invoice_id, float(payment_amount), payment_date)
        except data.PaymentValidationError as e:
            error = str(e)
        except data.InvoiceNotFoundError:
            raise HTTPException(status_code=404, detail="Invoice not found")

    if error:
        customer = data.customer_for(inv["customer_id"])
        return templates.TemplateResponse(request, "invoice_detail.html", {
            "invoice": inv, "customer": customer, "status_labels": data.STATUS_LABELS,
            "payment_error": error,
            "payment_form": {"payment_amount": payment_amount, "payment_date": payment_date},
            "today": str(datetime.date.today()),
        }, status_code=400)

    return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)


def _invoice_download_content(inv: dict, customer: Optional[dict]) -> str:
    return (
        f"INVOICE {inv['id']}\n"
        f"Date: {inv['date']}\n"
        f"Customer: {customer['name'] if customer else 'Unknown'}\n"
        f"Total Amount: Rs. {inv['total_amount']:.2f}\n"
        f"Paid Amount: Rs. {inv['paid_amount']:.2f}\n"
        f"Remaining Amount: Rs. {inv['remaining_amount']:.2f}\n"
        f"Status: {data.STATUS_LABELS.get(inv['status'], inv['status']).upper()}\n"
    )


@app.get("/invoices/{invoice_id}/download")
def invoice_download(request: Request, invoice_id: str):
    if not is_authed(request):
        return RedirectResponse("/login")
    inv = data.invoice_by_id(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    customer = data.customer_for(inv["customer_id"])
    content = _invoice_download_content(inv, customer)
    buf = io.BytesIO(content.encode("utf-8"))
    return StreamingResponse(
        buf,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{inv["id"]}.txt"'},
    )


@app.get("/customers", response_class=HTMLResponse)
def customers_page(request: Request):
    if not is_authed(request):
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "customers.html", {"customers": data.CUSTOMERS})


@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    if not is_authed(request):
        return RedirectResponse("/login")
    total = sum(i["amount"] for i in data.INVOICES)
    unpaid = sum(i["amount"] for i in data.INVOICES if i["status"] == "unpaid")
    return templates.TemplateResponse(request, "reports.html", {"total": total, "unpaid": unpaid})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    if not is_authed(request):
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "settings.html", {"api_enabled": API_ENABLED["value"]})


@app.post("/settings/toggle-api")
def toggle_api(request: Request):
    API_ENABLED["value"] = not API_ENABLED["value"]
    return RedirectResponse("/settings", status_code=303)


# --------------------------------------------------------------------
# JSON "Invoice API" - the API-first target for the agent
# --------------------------------------------------------------------

def _check_api_key(x_api_key: str = Header(default="")):
    if not API_ENABLED["value"]:
        raise HTTPException(status_code=503, detail="Invoice API temporarily unavailable")
    if x_api_key != INVOICE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


@app.get("/api/invoices")
def api_list_invoices(status: str | None = None, min_amount: float | None = None,
                       _=Depends(_check_api_key)):
    results = data.INVOICES
    if status:
        results = [i for i in results if i["status"] == status]
    if min_amount is not None:
        results = [i for i in results if i["amount"] >= min_amount]
    return {"invoices": results}


class InvoiceCreateRequest(BaseModel):
    invoice_number: Optional[str] = Field(default=None, description="If omitted, one is generated.")
    customer_name: str
    customer_email: str
    amount: float
    status: str
    date: Optional[str] = Field(default=None, description="YYYY-MM-DD. Defaults to today if omitted.")


@app.post("/api/invoices")
def api_create_invoice(payload: InvoiceCreateRequest, _=Depends(_check_api_key)):
    invoice_id = (payload.invoice_number or data.generate_invoice_id()).strip()
    if data.invoice_id_exists(invoice_id):
        raise HTTPException(status_code=409, detail=f"Invoice '{invoice_id}' already exists")
    if payload.status not in ("paid", "unpaid"):
        raise HTTPException(status_code=422, detail="status must be 'paid' or 'unpaid'")
    if payload.amount <= 0:
        raise HTTPException(status_code=422, detail="amount must be greater than zero")
    if not payload.customer_email or "@" not in payload.customer_email:
        raise HTTPException(status_code=422, detail="a valid customer_email is required")
    date_str = payload.date or str(datetime.date.today())
    try:
        datetime.date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be in YYYY-MM-DD format")

    customer = data.find_or_create_customer(payload.customer_name, payload.customer_email)
    invoice = data.add_invoice(invoice_id, customer["id"], payload.amount, payload.status, date_str)
    return invoice


@app.get("/api/invoices/latest")
def api_latest_invoice(_=Depends(_check_api_key)):
    return data.latest_invoice()


@app.get("/api/invoices/{invoice_id}")
def api_get_invoice(invoice_id: str, _=Depends(_check_api_key)):
    inv = data.invoice_by_id(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return inv


@app.get("/api/invoices/{invoice_id}/download")
def api_download_invoice(invoice_id: str, _=Depends(_check_api_key)):
    inv = data.invoice_by_id(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    customer = data.customer_for(inv["customer_id"])
    content = _invoice_download_content(inv, customer)
    buf = io.BytesIO(content.encode("utf-8"))
    return StreamingResponse(
        buf, media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{inv["id"]}.txt"'},
    )


class PaymentCreateRequest(BaseModel):
    amount: float
    date: Optional[str] = Field(default=None, description="YYYY-MM-DD. Defaults to today if omitted.")


@app.post("/api/invoices/{invoice_id}/payments")
def api_record_payment(invoice_id: str, payload: PaymentCreateRequest, _=Depends(_check_api_key)):
    date_str = payload.date or str(datetime.date.today())
    try:
        datetime.date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be in YYYY-MM-DD format")
    try:
        return data.record_payment(invoice_id, payload.amount, date_str)
    except data.InvoiceNotFoundError:
        raise HTTPException(status_code=404, detail="Invoice not found")
    except data.PaymentValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/api/invoices/{invoice_id}/payments")
def api_list_payments(invoice_id: str, _=Depends(_check_api_key)):
    inv = data.invoice_by_id(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"invoice_id": invoice_id, "payments": inv["payments"]}


@app.get("/api/customers/{customer_id}")
def api_get_customer(customer_id: str, _=Depends(_check_api_key)):
    c = data.customer_for(customer_id)
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    return c


@app.get("/api/status")
def api_status():
    """Unauthenticated health/availability check used by the agent's API router."""
    return {"available": API_ENABLED["value"]}


@app.post("/api/demo/reset")
def reset_demo():
    API_ENABLED["value"] = True
    _SESSIONS.clear()
    data.reset_data()
    return {"status": "reset"}
