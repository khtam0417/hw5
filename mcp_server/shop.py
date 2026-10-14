"""Shop data and rules for the Campus Customs MCP server (Homework 5).

Every read and change the agents make to the shop database goes through these functions, which the MCP
tools in server.py call. The shop rules are enforced here, in code, not only in the agents' prompts:

- Dates: desk.date_today is the shop's current date. Due dates, overdue days, payment dates and expected
  arrivals are all counted from it, never from the computer's clock.
- Vendor shipments: a vendor with an open (unpaid) invoice cannot ship, and a shipment can only be asked
  for a product the database links to that vendor: an invoice from the vendor that names the SKU. No
  other supplier relationship is assumed.
- Payments: an agent can only ask for one (request_payment_approval); no agent tool can approve or pay. A
  person approves through the operator server (approve_and_pay, behind the dashboard's Approve button and
  backend/approve.py), never an agent and never whoever asked, and that approval makes the payment: once,
  and only if the cash covers it.
- Cash never goes below zero, and nothing here ever adds money to it, so sales revenue is never added.
- A payment is one transaction: the payments row, the cash balance, the invoice's status or the lease's
  next due date, and the approval request's status all change together, or none of them do.
- Messages to customers, vendors and the landlord are saved on the board as drafts. Nothing is sent.

Database: data/campus_customs_new.db, the working copy, unless CAMPUS_DB_PATH names another copy (the
checks use an isolated copy of the original). The original, data/campus_customs.db, is refused. The
tables this module adds (approval_requests, board_drafts, ticket_updates) are created when the server
starts. Read-only lookups open the database read-only; changes use one short transaction each.
"""

from __future__ import annotations

import calendar
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastmcp.exceptions import ToolError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ORIGINAL_DB = PROJECT_ROOT / "data" / "campus_customs.db"  # never opened here
WORKING_DB = PROJECT_ROOT / "data" / "campus_customs_new.db"
BUSY_TIMEOUT_SECONDS = 10  # the backend, the agents' server and Claude Code's server share the file

AGENTS = ("Boss", "Inventory", "Accounting", "Facilities", "Customer Service")
# Names that are never a person: the agents and the software around them.
NOT_A_PERSON = {re.sub(r"[^a-z]", "", name.lower()) for name in AGENTS} | {
    "claude", "assistant", "agent", "system", "bot", "mcp", "operator", "ai", "admin",
}
HUMAN_CHANNEL = "operator"  # decisions recorded through the operator server, which agents never connect to
TICKET_STATUSES = (
    "open", "in_progress", "waiting_on_approval", "waiting_on_vendor", "waiting_on_customer", "needs_human", "resolved",
)
DRAFT_KINDS = {"customer": "customer_message", "vendor": "vendor_message", "landlord": "landlord_message"}
MAX_QTY = 500

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_requests (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER REFERENCES tickets(id),
    kind TEXT NOT NULL CHECK (kind IN ('invoice', 'rent')),
    ref_id INTEGER NOT NULL,
    item_key TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    account TEXT NOT NULL,
    due_date TEXT NOT NULL,
    reason TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'paid')),
    decided_by TEXT,
    decided_at TEXT,
    decided_via TEXT,
    decision_note TEXT,
    payment_id INTEGER REFERENCES payments(id),
    paid_by TEXT,
    paid_at TEXT
);
-- One live request per item: an invoice, or a lease's rent for one due date. A rejected one can be asked again.
CREATE UNIQUE INDEX IF NOT EXISTS one_live_request_per_item
    ON approval_requests (item_key) WHERE status IN ('pending', 'approved', 'paid');
CREATE TABLE IF NOT EXISTS board_drafts (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER REFERENCES tickets(id),
    kind TEXT NOT NULL CHECK (kind IN ('customer_message', 'vendor_message', 'landlord_message', 'vendor_order')),
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status = 'draft'),  -- drafts only: nothing is ever sent
    vendor_id INTEGER REFERENCES vendors(id),
    sku TEXT,
    size TEXT,
    qty INTEGER,
    expected_arrival TEXT
);
CREATE TABLE IF NOT EXISTS ticket_updates (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    status TEXT NOT NULL,
    note TEXT NOT NULL,
    agent TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class Refused(Exception):
    """A request the shop rules turn down. The message says why, for the agent to report."""


# --- Database access -------------------------------------------------------------------------------


def db_path() -> Path:
    """The database to use: CAMPUS_DB_PATH if set (an isolated copy), else the working copy."""
    raw = os.environ.get("CAMPUS_DB_PATH")
    path = Path(raw).expanduser().resolve() if raw else WORKING_DB
    if path == ORIGINAL_DB.resolve() or (path.exists() and ORIGINAL_DB.exists() and path.samefile(ORIGINAL_DB)):
        raise RuntimeError("The original database, data/campus_customs.db, is never opened here. Use a copy.")
    return path


def db_label() -> str:
    """The database for messages, without the home folder."""
    path = db_path()
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return f"isolated copy {path.name}"


def _connect(mode: str) -> sqlite3.Connection:
    path = db_path()
    if not path.is_file():
        raise ToolError(f"The database {db_label()} is missing. Run `python backend/reset_db.py` to create it.")
    con = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def read_only() -> sqlite3.Connection:
    """Open the database read-only (mode=ro): nothing can be changed through this connection."""
    return _connect("ro")


def read_write() -> sqlite3.Connection:
    """Open the database for a change. Use it with transaction()."""
    return _connect("rw")


@contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[None]:
    """One write transaction: everything inside commits together, or nothing does. BEGIN IMMEDIATE takes
    the write lock first, so the checks made inside can't go stale before the changes are written."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def ensure_app_tables() -> None:
    """Create the tables this server adds, if they aren't there yet (for example after a reset)."""
    with closing(read_write()) as con:
        con.executescript(APP_SCHEMA)


def one(con: sqlite3.Connection, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    row = con.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def many(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in con.execute(sql, params)]


def shop_date(con: sqlite3.Connection) -> date:
    """The shop's current date, desk.date_today. All date checks use it, never the computer's clock."""
    row = con.execute("SELECT date_today FROM desk").fetchone()
    if row is None:
        raise ToolError("The desk table has no row, so the shop's current date is unknown.")
    return date.fromisoformat(row["date_today"])


# --- Small helpers -----------------------------------------------------------------------------------


def _letters(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def not_a_person(name: str) -> bool:
    """Whether a name is an agent's or the software's, or empty, rather than a person's."""
    words = re.findall(r"[a-z]+", name.lower())
    return not _letters(name) or _letters(name) in NOT_A_PERSON or any(word in {"agent", "claude", "bot"} for word in words)


def same_person(a: str | None, b: str | None) -> bool:
    return bool(a and b) and _letters(a) == _letters(b)


def text(value: str, field: str, limit: int) -> str:
    """A required piece of text, trimmed, refused if empty or too long."""
    value = (value or "").strip()
    if not value:
        raise Refused(f"{field} can't be empty.")
    if len(value) > limit:
        raise Refused(f"{field} is too long ({len(value)} characters; at most {limit}).")
    return value


def money(amount: float) -> str:
    return f"${amount:,.2f}"


def add_month(day: date) -> date:
    """The same day next month, or that month's last day (Jan 31 -> Feb 28)."""
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _require_ticket(con: sqlite3.Connection, ticket_id: int | None) -> None:
    if ticket_id is not None and one(con, "SELECT id FROM tickets WHERE id = ?", (ticket_id,)) is None:
        raise Refused(f"No ticket with id {ticket_id}.")


def _must_change_one_row(cursor: sqlite3.Cursor, what: str) -> None:
    if cursor.rowcount != 1:
        raise RuntimeError(f"{what} changed while the payment was being made, so nothing was paid.")


# --- Reads -----------------------------------------------------------------------------------------------


def get_shop_date() -> dict[str, Any]:
    with closing(read_only()) as con:
        desk = one(con, "SELECT date_today, notes FROM desk")
    return {"date_today": desk["date_today"], "notes": desk["notes"]} if desk else {"date_today": None}


def list_tickets(status: str | None = None) -> dict[str, Any]:
    with closing(read_only()) as con:
        today = shop_date(con)
        if status:
            tickets = many(con, "SELECT * FROM tickets WHERE status = ? COLLATE NOCASE ORDER BY id", (status.strip(),))
        else:
            tickets = many(con, "SELECT * FROM tickets ORDER BY id")
    return {"date_today": today.isoformat(), "tickets": tickets}


def get_ticket(ticket_id: int) -> dict[str, Any]:
    with closing(read_only()) as con:
        today = shop_date(con)
        ticket = one(con, "SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        if ticket is None:
            return {"found": False, "ticket_id": ticket_id, "message": f"No ticket with id {ticket_id}."}
        updates = many(con, "SELECT id, status, note, agent, created_at FROM ticket_updates WHERE ticket_id = ? ORDER BY id", (ticket_id,))
        approvals = many(
            con,
            "SELECT id, kind, ref_id, amount, due_date, status, requested_by, decided_by, payment_id "
            "FROM approval_requests WHERE ticket_id = ? ORDER BY id",
            (ticket_id,),
        )
        drafts = many(
            con,
            "SELECT id, kind, recipient, subject, status, created_at, expected_arrival FROM board_drafts WHERE ticket_id = ? ORDER BY id",
            (ticket_id,),
        )
    return {
        "found": True,
        "ticket": ticket,
        "date_today": today.isoformat(),
        "updates": updates,
        "approval_requests": approvals,
        "drafts": drafts,
    }


def get_inventory(sku: str, size: str) -> dict[str, Any]:
    sku, size = sku.strip(), size.strip()
    with closing(read_only()) as con:
        row = one(
            con,
            "SELECT sku, name, size, qty, location FROM inventory WHERE sku = ? COLLATE NOCASE AND size = ? COLLATE NOCASE",
            (sku, size),
        )
        if row is not None:
            return {"found": True, **row}
        sizes = [r["size"] for r in many(con, "SELECT size FROM inventory WHERE sku = ? COLLATE NOCASE ORDER BY rowid", (sku,))]
    if sizes:
        message = f"{sku} has no size {size!r} in inventory. Sizes on record: {', '.join(sizes)}."
    else:
        message = f"No product with SKU {sku!r} is in inventory."
    return {"found": False, "sku": sku, "size": size, "message": message}


def get_pricing(sku: str) -> dict[str, Any]:
    sku = sku.strip()
    with closing(read_only()) as con:
        price = one(con, "SELECT sku, unit_cost, list_price FROM pricing WHERE sku = ? COLLATE NOCASE", (sku,))
    if price is None:
        return {"found": False, "sku": sku, "message": f"No price on record for SKU {sku!r}."}
    margin = price["list_price"] - price["unit_cost"]
    return {
        "found": True,
        **price,
        "margin_per_unit": round(margin, 2),
        "margin_percent_of_list": round(margin / price["list_price"] * 100, 1) if price["list_price"] else None,
    }


def evaluate_discount(sku: str, qty: int, discount_percent: float) -> dict[str, Any]:
    if qty < 1 or qty > MAX_QTY:
        raise ToolError(f"qty must be between 1 and {MAX_QTY}.")
    if not 0 <= discount_percent < 100:
        raise ToolError("discount_percent must be at least 0 and below 100.")
    priced = get_pricing(sku)
    if not priced["found"]:
        return priced
    unit_price = round(priced["list_price"] * (1 - discount_percent / 100), 2)
    revenue, cost = round(unit_price * qty, 2), round(priced["unit_cost"] * qty, 2)
    return {
        "found": True,
        "sku": priced["sku"],
        "qty": qty,
        "discount_percent": discount_percent,
        "list_price": priced["list_price"],
        "unit_cost": priced["unit_cost"],
        "unit_price": unit_price,
        "revenue": revenue,
        "cost": cost,
        "margin": round(revenue - cost, 2),
        "margin_percent_of_revenue": round((revenue - cost) / revenue * 100, 1) if revenue else None,
        "below_cost": unit_price < priced["unit_cost"],
        "note": "A calculation only: nothing was changed, and sales revenue is never added to cash.",
    }


def _vendor_summary(con: sqlite3.Connection, vendor: dict[str, Any]) -> dict[str, Any]:
    """A vendor with its open invoices, whether it can ship, and the SKUs its own invoices name (the only
    record of what a vendor supplies)."""
    open_invoices = many(
        con,
        "SELECT id, amount, due_date, description FROM invoices WHERE vendor_id = ? AND lower(status) = 'open' ORDER BY id",
        (vendor["id"],),
    )
    descriptions = [r["description"] or "" for r in many(con, "SELECT description FROM invoices WHERE vendor_id = ?", (vendor["id"],))]
    skus = [r["sku"] for r in many(con, "SELECT DISTINCT sku FROM inventory ORDER BY sku")]
    named = [sku for sku in skus if any(sku.lower() in text_.lower() for text_ in descriptions)]
    return {**vendor, "open_invoices": open_invoices, "can_ship": not open_invoices, "skus_named_in_its_invoices": named}


def list_vendors() -> dict[str, Any]:
    with closing(read_only()) as con:
        today = shop_date(con)
        vendors = [_vendor_summary(con, v) for v in many(con, "SELECT id, name, specialty, lead_days FROM vendors ORDER BY id")]
    return {"date_today": today.isoformat(), "vendors": vendors}


def get_vendor(vendor_id: int) -> dict[str, Any]:
    with closing(read_only()) as con:
        today = shop_date(con)
        vendor = one(con, "SELECT id, name, specialty, lead_days FROM vendors WHERE id = ?", (vendor_id,))
        if vendor is None:
            return {"found": False, "vendor_id": vendor_id, "message": f"No vendor with id {vendor_id}."}
        return {"found": True, "date_today": today.isoformat(), "vendor": _vendor_summary(con, vendor)}


def get_invoice_details(invoice_id: int) -> dict[str, Any]:
    with closing(read_only()) as con:
        today = shop_date(con)
        invoice = one(con, "SELECT id, vendor_id, amount, due_date, status, description FROM invoices WHERE id = ?", (invoice_id,))
        if invoice is None:
            return {"found": False, "invoice_id": invoice_id, "message": f"No invoice with id {invoice_id}."}
        vendor = one(con, "SELECT id, name, specialty, lead_days FROM vendors WHERE id = ?", (invoice["vendor_id"],))

    days_past_due = (today - date.fromisoformat(invoice["due_date"])).days
    overdue = invoice["status"].lower() == "open" and days_past_due > 0
    result: dict[str, Any] = {
        "found": True,
        "invoice": invoice,
        "vendor": vendor,
        "date_today": today.isoformat(),
        "overdue": overdue,
        "days_overdue": days_past_due if overdue else 0,
    }
    if vendor is None:
        result["message"] = (
            f"Invoice {invoice_id} names vendor {invoice['vendor_id']}, but there is no vendor with that id, "
            "so its lead time is unknown."
        )
    return result


def get_lease_details(lease_id: int) -> dict[str, Any]:
    with closing(read_only()) as con:
        today = shop_date(con)
        lease = one(con, "SELECT id, space_name, landlord, monthly_rent, next_due, notes FROM leases WHERE id = ?", (lease_id,))
    if lease is None:
        return {"found": False, "lease_id": lease_id, "message": f"No lease with id {lease_id}."}
    return {
        "found": True,
        "lease": lease,
        "date_today": today.isoformat(),
        "days_until_due": (date.fromisoformat(lease["next_due"]) - today).days,
    }


def get_cash_balance() -> dict[str, Any]:
    with closing(read_only()) as con:
        today = shop_date(con)
        accounts = many(con, "SELECT name, balance, date FROM cash_accounts ORDER BY name")
        totals = {
            r["status"]: r["total"]
            for r in many(con, "SELECT status, round(sum(amount), 2) AS total FROM approval_requests GROUP BY status")
        }
    return {
        "date_today": today.isoformat(),
        "accounts": accounts,
        "waiting_for_approval_total": totals.get("pending", 0),
        "note": "Cash only goes down, when a person approves a payment request, which makes the payment. "
        "Sales revenue is never added.",
    }


def list_approval_requests(status: str | None = None, ticket_id: int | None = None) -> dict[str, Any]:
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status.strip().lower())
    if ticket_id is not None:
        clauses.append("ticket_id = ?")
        params.append(ticket_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with closing(read_only()) as con:
        today = shop_date(con)
        requests = many(
            con,
            "SELECT id, ticket_id, kind, ref_id, amount, account, due_date, reason, requested_by, requested_at, status, "
            f"decided_by, decided_at, decision_note, payment_id, paid_by, paid_at FROM approval_requests{where} ORDER BY id",
            tuple(params),
        )
    return {"date_today": today.isoformat(), "approval_requests": requests}


def list_payments() -> dict[str, Any]:
    with closing(read_only()) as con:
        today = shop_date(con)
        payments = many(con, "SELECT id, kind, ref_id, amount, account, paid_at, approved_by FROM payments ORDER BY id")
    return {"date_today": today.isoformat(), "payments": payments}


def list_board(ticket_id: int | None = None) -> dict[str, Any]:
    with closing(read_only()) as con:
        today = shop_date(con)
        if ticket_id is None:
            drafts = many(con, "SELECT * FROM board_drafts ORDER BY id")
        else:
            drafts = many(con, "SELECT * FROM board_drafts WHERE ticket_id = ? ORDER BY id", (ticket_id,))
    return {"date_today": today.isoformat(), "drafts": drafts, "note": "Every item is a draft. Nothing has been sent."}


# --- Changes ---------------------------------------------------------------------------------------------


def request_payment_approval(
    kind: str, ref_id: int, reason: str, requested_by: str, ticket_id: int | None = None, account: str = "checking"
) -> dict[str, Any]:
    """Ask a person to approve paying an open invoice or a lease's next rent. The amount and due date come
    from the database. Refused when the item isn't owed, is already requested or paid, or cash is short."""
    kind = (kind or "").strip().lower()
    if kind not in ("invoice", "rent"):
        raise Refused("kind must be 'invoice' (a vendor invoice) or 'rent' (a lease's next rent).")
    reason = text(reason, "reason", 500)
    requested_by = text(requested_by, "requested_by", 80)
    with closing(read_write()) as con, transaction(con):
        today = shop_date(con)
        _require_ticket(con, ticket_id)
        if kind == "invoice":
            invoice = one(con, "SELECT id, vendor_id, amount, due_date, status FROM invoices WHERE id = ?", (ref_id,))
            if invoice is None:
                raise Refused(f"No invoice with id {ref_id}.")
            if invoice["status"].lower() != "open":
                raise Refused(f"Invoice {ref_id} is {invoice['status']!r}, not open, so there is nothing to pay.")
            amount, due_date, item_key = invoice["amount"], invoice["due_date"], f"invoice:{ref_id}"
        else:
            lease = one(con, "SELECT id, monthly_rent, next_due FROM leases WHERE id = ?", (ref_id,))
            if lease is None:
                raise Refused(f"No lease with id {ref_id}.")
            amount, due_date, item_key = lease["monthly_rent"], lease["next_due"], f"rent:{ref_id}:{lease['next_due']}"
        what = f"invoice {ref_id}" if kind == "invoice" else f"lease {ref_id}'s rent due {due_date}"
        cash = one(con, "SELECT name, balance FROM cash_accounts WHERE name = ?", (account,))
        if cash is None:
            raise Refused(f"No cash account named {account!r}.")
        existing = one(
            con,
            "SELECT id, status FROM approval_requests WHERE item_key = ? AND status IN ('pending', 'approved', 'paid')",
            (item_key,),
        )
        if existing:
            raise Refused(
                f"Approval request {existing['id']} for {what} already exists (status {existing['status']}). "
                "A payment is never requested twice."
            )
        if amount > cash["balance"]:
            raise Refused(f"Not enough cash: {account} holds {money(cash['balance'])} and {what} is {money(amount)}.")
        cursor = con.execute(
            "INSERT INTO approval_requests (ticket_id, kind, ref_id, item_key, amount, account, due_date, reason, "
            "requested_by, requested_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (ticket_id, kind, ref_id, item_key, amount, account, due_date, reason, requested_by, today.isoformat()),
        )
    return {
        "ok": True,
        "approval_id": cursor.lastrowid,
        "status": "pending",
        "kind": kind,
        "ref_id": ref_id,
        "amount": amount,
        "account": account,
        "due_date": due_date,
        "requested_by": requested_by,
        "requested_at": today.isoformat(),
        "message": "Waiting for a person to approve or reject it on the dashboard. Approving it makes the payment. "
        "Agents can't approve or pay, and nothing is paid until a person approves.",
    }


def _person(approver: str) -> str:
    """The name of the person deciding, refused if it is empty or an agent's or the software's."""
    approver = text(approver, "approver", 80)
    if not_a_person(approver):
        raise Refused(f"{approver!r} is an agent or system name. Only a person can approve or reject a payment.")
    return approver


def approve_and_pay(approval_id: int, approver: str, note: str = "") -> dict[str, Any]:
    """A person approves a pending payment request, and the payment is made in the same transaction. Operator
    server only: agents never reach this. Refused, with nothing changed, when the approver is an agent or the one
    who asked, the request isn't pending (already paid or rejected), the invoice or rent is no longer owed or has
    changed, or the cash doesn't cover it. On success the payments row, the cash balance, the invoice's status or
    the lease's next due date, and the request all change together."""
    approver = _person(approver)
    note = (note or "").strip()[:500]
    with closing(read_write()) as con, transaction(con):
        today = shop_date(con)
        request = one(con, "SELECT * FROM approval_requests WHERE id = ?", (approval_id,))
        if request is None:
            raise Refused(f"No approval request with id {approval_id}.")
        status = request["status"]
        if status == "paid":
            raise Refused(
                f"Approval request {approval_id} was already approved and paid (payment {request['payment_id']}). "
                "It is never paid twice."
            )
        if status == "rejected":
            raise Refused(f"{request['decided_by']} rejected approval request {approval_id}, so it can't be paid.")
        if status != "pending":
            raise Refused(f"Approval request {approval_id} is {status}, so it can't be approved.")
        if same_person(approver, request["requested_by"]):
            raise Refused("Whoever asked for a payment can't approve it.")

        amount, account, kind, ref_id = request["amount"], request["account"], request["kind"], request["ref_id"]
        if kind == "invoice":
            invoice = one(con, "SELECT amount, status FROM invoices WHERE id = ?", (ref_id,))
            if invoice is None:
                raise Refused(f"Invoice {ref_id} no longer exists.")
            if invoice["status"].lower() != "open":
                raise Refused(f"Invoice {ref_id} is already {invoice['status']!r}. It is never paid twice.")
            if abs(invoice["amount"] - amount) > 0.005:
                raise Refused(f"Invoice {ref_id} is now {money(invoice['amount'])}, not the requested {money(amount)}. Ask for a new request.")
        else:
            lease = one(con, "SELECT monthly_rent, next_due FROM leases WHERE id = ?", (ref_id,))
            if lease is None:
                raise Refused(f"Lease {ref_id} no longer exists.")
            if lease["next_due"] != request["due_date"]:
                raise Refused(
                    f"Lease {ref_id}'s rent due {request['due_date']} is no longer the next one owed (next due "
                    f"{lease['next_due']}). It is never paid twice."
                )
            if abs(lease["monthly_rent"] - amount) > 0.005:
                raise Refused(f"Lease {ref_id}'s rent is now {money(lease['monthly_rent'])}, not the requested {money(amount)}.")
        cash = one(con, "SELECT balance FROM cash_accounts WHERE name = ?", (account,))
        if cash is None:
            raise Refused(f"No cash account named {account!r}.")
        if cash["balance"] < amount:
            raise Refused(
                f"Not enough cash: {account} holds {money(cash['balance'])} and this payment is {money(amount)}. "
                "Nothing was paid, and the request is still pending."
            )

        payment = con.execute(
            "INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, ref_id, amount, account, today.isoformat(), approver),
        )
        _must_change_one_row(
            con.execute(
                "UPDATE cash_accounts SET balance = round(balance - ?, 2), date = ? WHERE name = ? AND balance >= ?",
                (amount, today.isoformat(), account, amount),
            ),
            "The cash balance",
        )
        if kind == "invoice":
            _must_change_one_row(
                con.execute("UPDATE invoices SET status = 'paid' WHERE id = ? AND lower(status) = 'open'", (ref_id,)),
                f"Invoice {ref_id}",
            )
            updated = f"invoice {ref_id} marked paid"
        else:
            next_due = add_month(date.fromisoformat(request["due_date"])).isoformat()
            _must_change_one_row(
                con.execute("UPDATE leases SET next_due = ? WHERE id = ? AND next_due = ?", (next_due, ref_id, request["due_date"])),
                f"Lease {ref_id}",
            )
            updated = f"lease {ref_id} next due moved from {request['due_date']} to {next_due}"
        _must_change_one_row(
            con.execute(
                "UPDATE approval_requests SET status = 'paid', decided_by = ?, decided_at = ?, decided_via = ?, "
                "decision_note = ?, payment_id = ?, paid_by = ?, paid_at = ? WHERE id = ? AND status = 'pending'",
                (approver, today.isoformat(), HUMAN_CHANNEL, note or None, payment.lastrowid, approver, today.isoformat(), approval_id),
            ),
            f"Approval request {approval_id}",
        )
        balance = one(con, "SELECT balance FROM cash_accounts WHERE name = ?", (account,))["balance"]
    return {
        "ok": True,
        "status": "paid",
        "approval_id": approval_id,
        "ticket_id": request["ticket_id"],
        "payment_id": payment.lastrowid,
        "kind": kind,
        "ref_id": ref_id,
        "amount": amount,
        "account": account,
        "approved_by": approver,
        "paid_at": today.isoformat(),
        "new_balance": balance,
        "updated": updated,
    }


def reject_request(approval_id: int, approver: str, note: str = "") -> dict[str, Any]:
    """A person rejects a pending payment request. Operator server only. Nothing is paid."""
    approver = _person(approver)
    note = (note or "").strip()[:500]
    with closing(read_write()) as con, transaction(con):
        today = shop_date(con)
        request = one(
            con, "SELECT id, ticket_id, status, requested_by, amount, kind, ref_id FROM approval_requests WHERE id = ?", (approval_id,)
        )
        if request is None:
            raise Refused(f"No approval request with id {approval_id}.")
        if request["status"] != "pending":
            raise Refused(f"Approval request {approval_id} is already {request['status']}.")
        if same_person(approver, request["requested_by"]):
            raise Refused("Whoever asked for a payment can't decide it.")
        con.execute(
            "UPDATE approval_requests SET status = 'rejected', decided_by = ?, decided_at = ?, decided_via = ?, decision_note = ? "
            "WHERE id = ? AND status = 'pending'",
            (approver, today.isoformat(), HUMAN_CHANNEL, note or None, approval_id),
        )
    return {
        "ok": True,
        "approval_id": approval_id,
        "status": "rejected",
        "ticket_id": request["ticket_id"],
        "decided_by": approver,
        "decided_at": today.isoformat(),
        "kind": request["kind"],
        "ref_id": request["ref_id"],
        "amount": request["amount"],
    }


def request_vendor_shipment(
    vendor_id: int, sku: str, size: str, qty: int, requested_by: str, ticket_id: int | None = None, note: str = ""
) -> dict[str, Any]:
    """Save a draft shipment request to a vendor. Refused while the vendor has an open invoice, and for a
    product the database doesn't link to the vendor."""
    if not 1 <= qty <= MAX_QTY:
        raise Refused(f"qty must be between 1 and {MAX_QTY}.")
    requested_by = text(requested_by, "requested_by", 80)
    note = (note or "").strip()[:1000]
    with closing(read_write()) as con, transaction(con):
        today = shop_date(con)
        _require_ticket(con, ticket_id)
        vendor = one(con, "SELECT id, name, specialty, lead_days FROM vendors WHERE id = ?", (vendor_id,))
        if vendor is None:
            raise Refused(f"No vendor with id {vendor_id}.")
        item = one(
            con,
            "SELECT sku, name, size FROM inventory WHERE sku = ? COLLATE NOCASE AND size = ? COLLATE NOCASE",
            (sku.strip(), size.strip()),
        )
        if item is None:
            raise Refused(f"No inventory item {sku} in size {size}.")
        open_invoices = many(
            con, "SELECT id, amount FROM invoices WHERE vendor_id = ? AND lower(status) = 'open' ORDER BY id", (vendor_id,)
        )
        if open_invoices:
            unpaid = ", ".join(f"invoice {i['id']} ({money(i['amount'])})" for i in open_invoices)
            raise Refused(
                f"{vendor['name']} can't ship while it has an open, unpaid invoice: {unpaid}. "
                "It must be paid first, and paying needs a person's approval."
            )
        basis = [
            i["id"]
            for i in many(con, "SELECT id, description FROM invoices WHERE vendor_id = ? ORDER BY id", (vendor_id,))
            if item["sku"].lower() in (i["description"] or "").lower()
        ]
        if not basis:
            raise Refused(
                f"The database doesn't link {vendor['name']} to {item['sku']}: none of its invoices names that SKU. "
                "No supplier is assumed, so no shipment was requested."
            )
        arrival = (today + timedelta(days=vendor["lead_days"])).isoformat()
        subject = f"Shipment request: {qty} x {item['name']} ({item['sku']}), size {item['size']}"
        body = (
            f"Please ship {qty} x {item['name']} ({item['sku']}), size {item['size']}. "
            f"Basis: {', '.join(f'invoice {i}' for i in basis)} from {vendor['name']} names this SKU. "
            f"With the {vendor['lead_days']}-day lead time, expected arrival is {arrival}."
            + (f"\nNote: {note}" if note else "")
        )
        cursor = con.execute(
            "INSERT INTO board_drafts (ticket_id, kind, recipient, subject, body, author, created_at, status, vendor_id, sku, size, "
            "qty, expected_arrival) VALUES (?, 'vendor_order', ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)",
            (ticket_id, vendor["name"], subject, body, requested_by, today.isoformat(), vendor_id, item["sku"], item["size"], qty, arrival),
        )
    return {
        "ok": True,
        "draft_id": cursor.lastrowid,
        "status": "draft",
        "vendor": vendor["name"],
        "sku": item["sku"],
        "size": item["size"],
        "qty": qty,
        "lead_days": vendor["lead_days"],
        "expected_arrival": arrival,
        "basis_invoices": basis,
        "message": "Saved on the board as a draft shipment request. Nothing was sent to the vendor.",
    }


def save_draft(
    recipient_type: str, recipient: str, subject: str, body: str, author: str, ticket_id: int | None = None
) -> dict[str, Any]:
    """Save a message to a customer, vendor or landlord on the board as a draft. Nothing is sent."""
    kind = DRAFT_KINDS.get((recipient_type or "").strip().lower())
    if kind is None:
        raise Refused("recipient_type must be 'customer', 'vendor' or 'landlord'.")
    recipient, subject = text(recipient, "recipient", 120), text(subject, "subject", 200)
    body, author = text(body, "body", 4000), text(author, "author", 80)
    with closing(read_write()) as con, transaction(con):
        today = shop_date(con)
        _require_ticket(con, ticket_id)
        cursor = con.execute(
            "INSERT INTO board_drafts (ticket_id, kind, recipient, subject, body, author, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')",
            (ticket_id, kind, recipient, subject, body, author, today.isoformat()),
        )
    return {
        "ok": True,
        "draft_id": cursor.lastrowid,
        "status": "draft",
        "kind": kind,
        "recipient": recipient,
        "message": "Saved on the board as a draft. Nothing was sent.",
    }


def update_ticket(ticket_id: int, status: str, note: str, agent: str) -> dict[str, Any]:
    """Set a ticket's status and add a note to its history (ticket_updates)."""
    status = (status or "").strip().lower()
    if status not in TICKET_STATUSES:
        raise Refused(f"status must be one of: {', '.join(TICKET_STATUSES)}.")
    note, agent = text(note, "note", 1000), text(agent, "agent", 80)
    with closing(read_write()) as con, transaction(con):
        today = shop_date(con)
        ticket = one(con, "SELECT id, status FROM tickets WHERE id = ?", (ticket_id,))
        if ticket is None:
            raise Refused(f"No ticket with id {ticket_id}.")
        con.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
        cursor = con.execute(
            "INSERT INTO ticket_updates (ticket_id, status, note, agent, created_at) VALUES (?, ?, ?, ?, ?)",
            (ticket_id, status, note, agent, today.isoformat()),
        )
    return {
        "ok": True,
        "ticket_id": ticket_id,
        "previous_status": ticket["status"],
        "status": status,
        "update_id": cursor.lastrowid,
        "date": today.isoformat(),
    }
