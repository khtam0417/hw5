# Campus Customs MCP server

`server.py` is a FastMCP server named `campus-customs`. It is the one way the five Campus Customs agents (Boss, Inventory, Accounting, Facilities and Customer Service) read and change the shop database. The tools are thin. Each one calls a function in `shop.py`, where the shop rules are enforced in code.

## Working database

- The server uses the working copy, **`data/campus_customs_new.db`** (relative to the project folder).
  - The checks point it at an isolated copy instead, through the `CAMPUS_DB_PATH` environment variable.
  - It never opens the original, `data/campus_customs.db`, and refuses it if `CAMPUS_DB_PATH` names it.
- Lookups open the database read-only (`mode=ro`). Each change runs in one short transaction (`BEGIN IMMEDIATE`). Every query is parameterized.
- When it starts, the server adds three tables if they're missing: `approval_requests`, `board_drafts` and `ticket_updates`. The original tables keep their schema. A reset (`python backend/reset_db.py`, or the dashboard's `POST /api/reset`) removes the added tables, and with them any stale payment requests, drafts and ticket updates. The server creates the tables again, empty.
- If the database is missing, the server stops with a message instead of creating data. `python backend/reset_db.py` creates the working copy from the original.
- Dates are compared with `desk.date_today`, the shop's current date, never with the computer's clock.

## Agent tools

Every lookup returns stored values exactly as they are in the database. A missing record comes back as `found: false` with a `message`. A change the rules turn down comes back as `{"ok": false, "refused": "<why>"}`.

| Tool | Reads | Changes | Returns |
|---|---|---|---|
| `get_shop_date` | `desk` | none | `date_today` and the desk notes. |
| `list_tickets(status)` | `tickets` | none | Every ticket, or those with one status. |
| `get_ticket(ticket_id)` | `tickets`, `ticket_updates`, `approval_requests`, `board_drafts` | none | The ticket as stored, its status updates, payment requests and drafts. |
| `get_inventory(sku, size)` | `inventory` | none | `sku`, `name`, `size`, `qty` and `location`. SKU and size match regardless of case. An unknown size lists the sizes on record. |
| `get_pricing(sku)` | `pricing` | none | `unit_cost`, `list_price`, margin per unit and margin as a percent of list price. |
| `evaluate_discount(sku, qty, discount_percent)` | `pricing` | none | Unit price, revenue, cost, margin and `below_cost`. A calculation only. |
| `list_vendors`, `get_vendor(vendor_id)` | `vendors`, `invoices`, `inventory` | none | `lead_days`, open invoices, `can_ship` (false while an invoice is open) and `skus_named_in_its_invoices`. |
| `get_invoice_details(invoice_id)` | `invoices`, `vendors`, `desk` | none | The invoice, its vendor and lead time, `overdue` and `days_overdue`. |
| `get_lease_details(lease_id)` | `leases`, `desk` | none | The lease and `days_until_due`, which is negative once the date has passed. |
| `get_cash_balance` | `cash_accounts`, `approval_requests` | none | Each account's balance and date, plus the total of payment requests waiting for a person's approval. |
| `list_approval_requests(status, ticket_id)` | `approval_requests` | none | Payment requests and their status: `pending`, `paid` (with its `payment_id` and who approved it) or `rejected`. |
| `list_payments` | `payments` | none | Payments made. |
| `list_board(ticket_id)` | `board_drafts` | none | Drafts on the board. None has been sent. |
| `request_payment_approval(kind, ref_id, reason, requested_by, ticket_id)` | `invoices` or `leases`, `cash_accounts`, `approval_requests` | Adds a pending `approval_requests` row. | Asks a person to approve paying an open invoice (`kind` `invoice`, such as a purchase from a vendor) or a lease's next rent (`kind` `rent`). The amount and due date come from the database. A person's approval makes the payment. |
| `request_vendor_shipment(vendor_id, sku, size, qty, requested_by, ticket_id, note)` | `vendors`, `invoices`, `inventory` | Adds a `board_drafts` row of kind `vendor_order`. | A draft shipment request. Its `expected_arrival` is an estimate: the shop date plus `lead_days`. It holds only if the vendor has been paid and fulfills the request. Saving it does not send an order or confirm a shipment. |
| `save_draft(recipient_type, recipient, subject, body, author, ticket_id)` | `tickets` | Adds a `board_drafts` row. | A customer, vendor or landlord message, saved as a draft. |
| `update_ticket(ticket_id, status, note, agent)` | `tickets` | Sets `tickets.status` and adds a `ticket_updates` row. | The ticket's previous and new status. |

No agent tool approves or makes a payment. Problem 7 removed the agents' `pay_approved_request`: agents now only prepare payment requests, and a person's approval makes the payment through the operator tools below.

## Operator tools: for a person only

`python mcp_server/server.py --operator` starts a separate server, `campus-customs-operator`. The backend's dashboard routes (`backend/main.py`) and `backend/approve.py` use it. Agents are never connected to it, so no agent can approve or make a payment.

| Tool | Changes | Returns |
|---|---|---|
| `get_shop_date`, `list_tickets(status)`, `get_ticket(ticket_id)`, `get_cash_balance` | none | The same reads as the agent tools of those names, for the dashboard. |
| `list_requests(status)` | none | Payment requests, for a person to review. |
| `approve_and_pay(approval_id, approver, note)` | Adds a `payments` row (`approved_by` = the approver, `paid_at` = the shop date), lowers `cash_accounts.balance`, sets the invoice to `paid` or moves `leases.next_due` a month on, and marks the request `paid`, all in one transaction. | The payment id, the new balance and what was updated. Refused, with nothing changed, if the approver is an agent or system name or whoever asked, the request isn't pending (a paid request is never paid again), the invoice or rent is no longer owed or its amount changed, or the cash doesn't cover it. |
| `reject_request(approval_id, approver, note)` | Sets a pending request to `rejected`, with who decided and when. | The decision. Nothing is paid. |

## Rules enforced in `shop.py`

- **Vendors.** A vendor can't ship while it has an open, unpaid invoice. A shipment is also refused for a product that none of the vendor's invoices names, so no supplier relationship is assumed.
- **Approval.** Agents can only ask for a payment. A payment is made only when a person approves the request through the operator server's `approve_and_pay`, which checks the request again before paying.
- **Funds.** A payment request or payment is refused when the cash doesn't cover it, so the balance never goes below zero. A refused approval leaves the request pending.
- **No duplicates.** There is one live request per invoice, or per lease rent due date. Nothing is paid twice: approving a paid request again is refused.
- **One transaction.** The payment row, cash balance, invoice or lease, and request change together, or not at all.
- **No revenue.** Nothing adds money to cash.
- **Drafts only.** Messages are saved on the board as drafts and are never sent.

## Run

From the project folder:

```bash
.venv/bin/python mcp_server/server.py              # stdio, for the agents and other MCP clients
.venv/bin/python mcp_server/server.py --http       # Streamable HTTP at http://127.0.0.1:8006/mcp
.venv/bin/python mcp_server/server.py --operator   # stdio, the operator tools (used by backend/main.py and backend/approve.py)
```

The agents start the server themselves for each run (`backend/agents.py`). Claude Code connects to it through `.mcp.json`. After a code change, restart the Claude Code session to load the new tools there.
