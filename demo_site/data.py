"""
In-memory fixture data for the demo 'Business Portal'.
Represents a realistic small SaaS invoicing product. Mutable at runtime via
the "Add New Invoice" feature (see main.py: new_invoice_submit / api_create_invoice)
and the "Record Payment" feature (see main.py: record_payment_submit /
api_record_payment) - new invoices/customers/payments are appended to or
recorded against the same lists every other route reads from, so they're
immediately visible everywhere (invoice list page, invoice detail page, and
the JSON Invoice API) with no separate sync step.

Payment tracking fields on each invoice dict:
- total_amount: the invoice's full amount (kept in sync with the legacy
  "amount" key, which is preserved for backward compatibility - existing
  code/tests reading inv["amount"] keep working unchanged).
- paid_amount: sum of all recorded payments so far.
- remaining_amount: total_amount - paid_amount (never negative; payments
  greater than the remaining balance are rejected by record_payment()).
- payments: list of {"amount": float, "date": "YYYY-MM-DD"} dicts, oldest
  first - the payment history for that invoice.
- status: one of "unpaid" | "partially_paid" | "paid", recomputed
  automatically every time record_payment() is called. Never set directly
  by calling code other than at invoice creation (see add_invoice()).

reset_data() restores the original seed lists; called by POST /api/demo/reset
so "Load Demo" / "Reset Demo" in the main backend gives back a known state
even after invoices/payments have been added during a session.
"""
import copy
import datetime
import re

CUSTOMERS = [
    {"id": "cust_1", "name": "Aarav Sharma", "email": "aarav.sharma@example.com", "company": "Nimbus Retail"},
    {"id": "cust_2", "name": "Priya Nair", "email": "priya.nair@example.com", "company": "Sundial Logistics"},
    {"id": "cust_3", "name": "Devansh Iyer", "email": "devansh.iyer@example.com", "company": "Orbit Foods"},
    {"id": "cust_4", "name": "Meera Kulkarni", "email": "meera.kulkarni@example.com", "company": "Larkspur Media"},
]

_BASE_DATE = datetime.date(2026, 8, 1)


def _seed_invoice(inv_id, customer_id, amount, status, days_offset):
    """Builds a seed invoice with payment fields backfilled consistently
    with its legacy paid/unpaid status: "paid" seed invoices are treated as
    paid in full with no logged payment history (e.g. a cash sale entered
    as already settled), "unpaid" ones have zero paid_amount - this mirrors
    exactly what the seed data meant before payment tracking existed, it
    just now has the new fields to match."""
    total = float(amount)
    paid = total if status == "paid" else 0.0
    return {
        "id": inv_id, "customer_id": customer_id,
        "amount": total, "total_amount": total,
        "paid_amount": paid, "remaining_amount": round(total - paid, 2),
        "status": status, "payments": [],
        "date": str(_BASE_DATE + datetime.timedelta(days=days_offset)),
    }


INVOICES = [
    _seed_invoice("inv_1001", "cust_1", 4500.0, "paid", 1),
    _seed_invoice("inv_1002", "cust_2", 12800.0, "unpaid", 3),
    _seed_invoice("inv_1003", "cust_3", 9800.0, "unpaid", 6),
    _seed_invoice("inv_1004", "cust_1", 2100.0, "paid", 9),
    _seed_invoice("inv_1005", "cust_4", 15600.0, "unpaid", 12),
    _seed_invoice("inv_1006", "cust_2", 3200.0, "paid", 15),
]

# Snapshots of the original seed data, for reset_data().
_ORIGINAL_CUSTOMERS = copy.deepcopy(CUSTOMERS)
_ORIGINAL_INVOICES = copy.deepcopy(INVOICES)

STATUS_LABELS = {"unpaid": "Unpaid", "partially_paid": "Partially Paid", "paid": "Paid"}


class InvoiceNotFoundError(Exception):
    pass


class PaymentValidationError(Exception):
    pass


def latest_invoice():
    return sorted(INVOICES, key=lambda i: i["date"])[-1]


def customer_for(customer_id):
    return next((c for c in CUSTOMERS if c["id"] == customer_id), None)


def invoice_by_id(invoice_id):
    return next((i for i in INVOICES if i["id"] == invoice_id), None)


def invoice_id_exists(invoice_id: str) -> bool:
    return any(i["id"] == invoice_id for i in INVOICES)


def generate_invoice_id() -> str:
    """Next sequential inv_<N> id, based on the highest existing numeric
    suffix - works whether that invoice was seed data or added at runtime."""
    numbers = []
    for inv in INVOICES:
        m = re.match(r"^inv_(\d+)$", inv["id"])
        if m:
            numbers.append(int(m.group(1)))
    return f"inv_{max(numbers, default=1000) + 1}"


def find_or_create_customer(name: str, email: str) -> dict:
    """Looks up a customer by email (case-insensitive); if none matches,
    creates one. This is what lets the invoice form take a name/email pair
    directly rather than requiring the customer to already exist."""
    name = name.strip()
    email = email.strip()
    email_norm = email.lower()
    for c in CUSTOMERS:
        if c["email"].strip().lower() == email_norm:
            return c

    numbers = []
    for c in CUSTOMERS:
        m = re.match(r"^cust_(\d+)$", c["id"])
        if m:
            numbers.append(int(m.group(1)))
    new_id = f"cust_{max(numbers, default=0) + 1}"
    customer = {"id": new_id, "name": name, "email": email, "company": "—"}
    CUSTOMERS.append(customer)
    return customer


def add_invoice(invoice_id: str, customer_id: str, amount: float, status: str, date: str) -> dict:
    """status here is the invoice's state AT CREATION - still just "paid" or
    "unpaid" (an invoice can't be created "partially_paid"; that only
    happens as a result of a partial payment). A "paid" invoice at creation
    is treated the same way seed "paid" invoices are: fully paid, no logged
    payment history (see _seed_invoice's docstring)."""
    total = float(amount)
    paid = total if status == "paid" else 0.0
    invoice = {
        "id": invoice_id.strip(), "customer_id": customer_id,
        "amount": total, "total_amount": total,
        "paid_amount": paid, "remaining_amount": round(total - paid, 2),
        "status": status, "payments": [], "date": date,
    }
    INVOICES.append(invoice)
    return invoice


def record_payment(invoice_id: str, amount: float, date: str) -> dict:
    """Records a payment against an invoice and recomputes paid_amount,
    remaining_amount, and status automatically:
    - remaining_amount reaches (effectively) zero -> "paid"
    - some but not all paid -> "partially_paid"
    - nothing paid -> "unpaid" (unreachable via this function since amount
      must be > 0, but kept as the explicit default for clarity)

    Raises InvoiceNotFoundError if the invoice doesn't exist, or
    PaymentValidationError for a non-positive amount, a payment that would
    exceed the remaining balance, or an invalid date - callers (both the
    HTML form and the JSON API) turn these into the appropriate user-facing
    error instead of a 500."""
    inv = invoice_by_id(invoice_id)
    if inv is None:
        raise InvoiceNotFoundError(invoice_id)
    if amount is None or amount <= 0:
        raise PaymentValidationError("Payment amount must be greater than zero.")
    try:
        datetime.date.fromisoformat(date)
    except (TypeError, ValueError):
        raise PaymentValidationError("Payment date must be a valid date.")

    remaining = round(inv["total_amount"] - inv["paid_amount"], 2)
    # Small tolerance for floating-point rounding (e.g. three payments of
    # 33.34 on a 100.02 invoice should be allowed to fully settle it).
    if amount > remaining + 0.01:
        raise PaymentValidationError(
            f"Payment of Rs. {amount:.2f} exceeds the remaining balance of Rs. {remaining:.2f}."
        )

    inv["payments"].append({"amount": round(amount, 2), "date": date})
    inv["paid_amount"] = round(inv["paid_amount"] + amount, 2)
    inv["remaining_amount"] = round(inv["total_amount"] - inv["paid_amount"], 2)

    if inv["remaining_amount"] <= 0.01:
        inv["remaining_amount"] = 0.0
        inv["paid_amount"] = inv["total_amount"]
        inv["status"] = "paid"
    elif inv["paid_amount"] > 0:
        inv["status"] = "partially_paid"
    else:
        inv["status"] = "unpaid"
    return inv


def reset_data():
    """Restores CUSTOMERS/INVOICES (including payment history) to the
    original seed data - used by POST /api/demo/reset so runtime-added
    invoices/payments don't linger across demo resets or (in tests) across
    unrelated test runs."""
    global CUSTOMERS, INVOICES
    CUSTOMERS[:] = copy.deepcopy(_ORIGINAL_CUSTOMERS)
    INVOICES[:] = copy.deepcopy(_ORIGINAL_INVOICES)
