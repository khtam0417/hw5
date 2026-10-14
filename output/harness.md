# Harness — Campus Customs Multi-Agent Operations

How the Campus Customs operations system works, end to end. It covers:
- the shop database: its tables and the three tickets;
- the MCP server and every tool it offers;
- the five agents;
- the backend's API routes;
- the React dashboard;
- the business safeguards and limits;
- the audit trail;
- how each part was checked;
- the results of the full run on all three tickets (Problem 9).

## Database

- **Original:** `data/campus_customs.db`, from the course data pack. It is read-only on disk and is never changed.
- **Working copy:** `data/campus_customs_new.db`. The MCP server and the backend use only this file, and `python backend/reset_db.py` (or the backend's `POST /api/reset`) restores it from the original. It now holds the results of the full run (see The full run below): tickets 101, 102 and 103 all `resolved` (101 for internal processing, after the owner's clarification), two payments, and $160.00 in checking. The original is unchanged.
- **The shop's current date** is `desk.date_today`: **2026-08-31**. All date comparisons below use it.

### Tables

| Table | Rows | Fields | Why it matters to the agents |
|---|---|---|---|
| `desk` | 1 | `date_today`, `notes` | It sets the shop's current date (2026-08-31), which every agent uses instead of the computer's clock. |
| `inventory` | 10 | `sku`, `name`, `size`, `qty`, `location` | Inventory checks stock here, one row per product and size. |
| `pricing` | 4 | `sku`, `unit_cost`, `list_price` | Accounting uses each product's cost and list price to check margins, for example on a discount. |
| `vendors` | 3 | `id`, `name`, `specialty`, `lead_days` | It gives each vendor's specialty and lead time in days, which Inventory needs to plan restocking. |
| `leases` | 1 | `id`, `space_name`, `landlord`, `monthly_rent`, `next_due`, `notes` | Facilities sees what rent is owed, to whom, and when it is next due. |
| `cash_accounts` | 1 | `name`, `balance`, `date` | Accounting checks the cash available before any payment, and the balance must never go negative. |
| `payments` | 0 | `id`, `kind`, `ref_id`, `amount`, `account`, `paid_at`, `approved_by` | It records each payment and who approved it, and is empty so far. |
| `invoices` | 1 | `id`, `vendor_id`, `amount`, `due_date`, `status`, `description` | Accounting tracks what the shop owes vendors, and an open invoice stops that vendor from shipping. |
| `tickets` | 3 | `id`, `type`, `requester`, `subject`, `sku`, `size`, `qty`, `lease_id`, `invoice_id`, `status`, `notes`, `created_at` | It is the work queue the Boss coordinates: each ticket is a request for the agents to resolve. |

### How the tables connect

- **Foreign keys:** `tickets.invoice_id` → `invoices.id`, `tickets.lease_id` → `leases.id`, and `invoices.vendor_id` → `vendors.id`.
- **Matched by value, with no foreign key:** `tickets.sku` + `tickets.size` → `inventory` (whose key is `sku` + `size`), and `tickets.sku` and `inventory.sku` → `pricing.sku`. Every SKU in `inventory` has a price, and every priced SKU is in `inventory`.
- **`payments`** has no foreign keys. Going by its fields, `kind` says what was paid, `ref_id` names that record (such as an invoice or lease id), and `account` names a `cash_accounts` row.
- **No table links a product to a vendor.** Which vendor supplies a product can only be inferred from `vendors.specialty`, and such an inference is not a database relationship. The one recorded link between a vendor and a product is invoice 501 (vendor 1), whose description names the tee `CC-TEE-WHITE` size S.

## The three open tickets

All three have status `open` and were created on 2026-08-31, the shop's current date (at 09:15, 11:05 and 16:55). This section describes the tickets before the agents ran; their final states are in The full run below.

**Ticket 101: customer order from Tauhid Zaman for 1 Classic Bulldog Tee, size S**

- `inventory`: `CC-TEE-WHITE` size S has **0** in stock (Aisle B). M has 5, L 3 and XL 2.
- `pricing`: unit cost $8.00, list price $28.00.
- `invoices`, through `invoice_id` 501: $840.00 owed to vendor 1 for "Rush reprint CC-TEE-WHITE S", due 2026-08-28 and still `open`, so **3 days overdue**.
- `vendors`: vendor 1 is Bulldog Print Co (apparel reprint, 5-day lead time). It cannot ship while invoice 501 is open.

**Ticket 102: rent notice from Elm City Properties**

- `leases`, through `lease_id` 1: the Chapel Street shop, landlord Elm City Properties, $2,400.00 a month, next due **2026-09-02**. That is **2 days** after the shop date, matching the notice's "due in 2 days".
- `payments` has no rent payment yet.

**Ticket 103: price override for the Yale AI Club, 20 Basic Hoodie Big Yale in size M at a bulk discount**

- `inventory`: `CC-HOOD-NAVY` size M has **8** in stock (Aisle A), 12 short of the 20 requested. S has 4, L 14 and XL 6.
- `pricing`: unit cost $22.00, list price $58.00, so the margin is $36.00 per hoodie at list price.
- No invoice or vendor is linked to the ticket, and no table records who supplies hoodies. Bulldog Print Co ("apparel reprint") is the only vendor whose specialty fits, but that is an inference, not a database relationship.

### How the tickets relate

- **101 depends on Bulldog Print Co, and 103 may too.** Ticket 101 reaches Bulldog Print Co through database links (invoice 501 → vendor 1), and the vendor cannot ship while that invoice ($840.00, 3 days overdue) is open. Ticket 103 has no recorded vendor. If Bulldog Print Co turns out to supply the hoodies, which is inferred only from its specialty, the same unpaid invoice would block restocking the 12 missing hoodies.
- **101 and 102 draw on the same cash.** Checking holds $3,400.00 as of 2026-08-31. Paying invoice 501 and the rent ($840.00 + $2,400.00 = $3,240.00) fits, but leaves only $160.00, and each payment needs human approval first.
- **103 brings in no cash.** Money from the hoodie sale is incoming revenue, which is never added to the cash balance.

## MCP server

`mcp_server/server.py` is a FastMCP server, `campus-customs`, whose tools all five agents share. It opens only the working copy, `data/campus_customs_new.db`. Its lookups open the database read-only. Since Problem 5 it also makes changes (payment requests, payments, drafts and ticket updates), and each one goes through the rule checks in `mcp_server/shop.py` in a single transaction. Since Problem 7, payments are made only by the operator tool `approve_and_pay`, when a person approves a request. Every query is parameterized. A missing record comes back as `found: false` with a message instead of made-up values. Overdue status and days until a due date are measured from `desk.date_today`. `mcp_server/README.md` lists every field each tool returns.

The first three tools (Problem 3):

| Tool | Tables it reads | Tickets | How it helps |
|---|---|---|---|
| `get_inventory(sku, size)` | `inventory` | 101 and 103 | It shows that `CC-TEE-WHITE` size S has 0 in stock for ticket 101 and that `CC-HOOD-NAVY` size M has 8 of the 20 hoodies ticket 103 asks for. |
| `get_invoice_details(invoice_id)` | `invoices`, `vendors`, `desk` | 101 | It returns invoice 501 with its vendor, Bulldog Print Co, and that vendor's 5-day lead time, and shows the invoice is open and 3 days overdue, which is why the reprint for ticket 101 can't ship yet. |
| `get_lease_details(lease_id)` | `leases`, `desk` | 102 | It returns lease 1's $2,400.00 rent and its 2026-09-02 due date, 2 days after the shop date, so the agents know what ticket 102 asks the shop to pay and by when. |

No tool assumes which vendor supplies a product. The only link the tools use is an invoice from the vendor that names the SKU; see `list_vendors` and `request_vendor_shipment` below.

### Every MCP tool and its tables (Problem 5, payment flow updated in Problem 7)

**Agent tools** (`campus-customs`, 18 tools). Each agent sees only the tools of its role; see Agents below. None of them approves or pays.

| Tool | Reads | Changes | Tickets | What it's for |
|---|---|---|---|---|
| `get_shop_date` | `desk` | none | all | The shop's current date, `desk.date_today`. |
| `list_tickets(status)` | `tickets`, `desk` | none | all | Every ticket, or those with one status. |
| `get_ticket(ticket_id)` | `tickets`, `ticket_updates`, `approval_requests`, `board_drafts`, `desk` | none | all | One ticket as stored, with its updates, approval requests and drafts, so work isn't repeated. |
| `get_inventory(sku, size)` | `inventory` | none | 101, 103 | Units on hand for one product size. |
| `get_pricing(sku)` | `pricing` | none | 103 | Unit cost, list price and margin. |
| `evaluate_discount(sku, qty, discount_percent)` | `pricing` | none | 103 | The exact price, revenue, cost and margin of a discount, and whether it falls below cost. A calculation only. |
| `list_vendors`, `get_vendor(vendor_id)` | `vendors`, `invoices`, `inventory`, `desk` | none | 101, 103 | Lead times, open invoices, `can_ship`, and the SKUs each vendor's own invoices name. |
| `get_invoice_details(invoice_id)` | `invoices`, `vendors`, `desk` | none | 101 | An invoice with its vendor, lead time and overdue days. |
| `get_lease_details(lease_id)` | `leases`, `desk` | none | 102 | Rent, landlord and next due date, with days until due. |
| `get_cash_balance` | `cash_accounts`, `approval_requests`, `desk` | none | 101, 102 | Balances, plus the total of payment requests waiting for a person's approval. |
| `list_approval_requests(status, ticket_id)` | `approval_requests`, `desk` | none | 101, 102 | Payment requests and where each one stands: `pending`, `paid` (with its payment id) or `rejected`. |
| `list_payments` | `payments`, `desk` | none | 101, 102 | Payments already made. |
| `list_board(ticket_id)` | `board_drafts`, `desk` | none | all | The drafts on the board. None has been sent. |
| `request_payment_approval(kind, ref_id, reason, ticket_id)` | `invoices` or `leases`, `cash_accounts`, `approval_requests`, `tickets`, `desk` | `approval_requests`: adds a pending row | 101, 102 | Asks a person to approve paying an open invoice or a lease's next rent. The amount and due date come from the database. A person's approval makes the payment. |
| `request_vendor_shipment(vendor_id, sku, size, qty, ticket_id, note)` | `vendors`, `invoices`, `inventory`, `tickets`, `desk` | `board_drafts`: adds a `vendor_order` draft | 101 (103 is refused) | A draft restock request. Its `expected_arrival` (shop date plus lead time) is an estimate that depends on payment and on the vendor fulfilling the request. The draft sends no order and confirms no shipment. |
| `save_draft(recipient_type, recipient, subject, body, ticket_id)` | `tickets`, `desk` | `board_drafts`: adds a draft | all | A customer, vendor or landlord message, kept as a draft. |
| `update_ticket(ticket_id, status, note)` | `tickets`, `desk` | `tickets.status`, and `ticket_updates`: adds a row | all | The Boss records its decision and the reason. |

Problem 7 removed the agents' `pay_approved_request`. In Problem 5, a person's approval marked a request approved and Accounting then paid it. Now the person's approval makes the payment through the operator tool `approve_and_pay`, so no agent tool moves money.

**Operator tools** (`campus-customs-operator`, started with `--operator`). These are for a person only: the backend's dashboard routes (`backend/main.py`) and `backend/approve.py` use them, and agents are never connected to this server.

| Tool | Reads | Changes | What it's for |
|---|---|---|---|
| `get_shop_date`, `list_tickets(status)`, `get_ticket(ticket_id)`, `get_cash_balance` | as the agent tools of the same names | none | The dashboard's reads: the shop date, the tickets and the cash. |
| `list_requests(status)` | `approval_requests`, `desk` | none | Payment requests, for a person to review. |
| `approve_and_pay(approval_id, approver, note)` | `approval_requests`, `invoices` or `leases`, `cash_accounts`, `desk` | `payments`: adds a row, with `approved_by` the person. `cash_accounts`: lowers the balance and sets the date. `invoices.status` becomes `paid`, or `leases.next_due` moves a month on. `approval_requests`: marked `paid`, with who decided, when, and the payment id. | A person's approval, which makes the payment in one transaction. Refused, with nothing changed, when the approver is an agent or system name or whoever asked, the request isn't pending, the item is no longer owed or its amount changed, or cash is short. |
| `reject_request(approval_id, approver, note)` | `approval_requests`, `desk` | `approval_requests`: `rejected`, by whom and when | A person rejects a pending request. Nothing is paid. |

The server adds three tables to the working copy and creates them when it starts: `approval_requests`, `board_drafts` and `ticket_updates`. The original tables keep their schema.

## Agents

The five agents are PydanticAI agents in `backend/agents.py`. Each has its own prompt in `backend/prompts/`, and their shared data types are in `backend/models.py`.

PydanticAI runs every agent loop and every delegation. Claude Opus 5.5 (`claude-opus-5-5`, effort `high`, set in `backend/claude_config.py`) chooses each step through the Homework 4 adapter in `backend/claude_bridge.py`. The adapter is needed because PydanticAI's built-in Anthropic model calls the Anthropic API with an API key, and `CLAUDE_CODE_OAUTH_TOKEN` signs in Claude Code instead. So each step is one Claude call through the Claude Agent SDK, with Claude Code's own tools, settings and MCP servers switched off. The adapter reports each call's real token usage back to PydanticAI.

| Agent | Role | Prompt | MCP tools, besides `get_shop_date` and `get_ticket` |
|---|---|---|---|
| Boss | Coordinates the ticket, delegates, makes the final decision and records it | `boss.md` | `list_tickets`, `list_approval_requests`, `list_board`, `update_ticket` |
| Inventory | Stock, vendors, lead times and draft shipment requests | `inventory.md` | `get_inventory`, `list_vendors`, `get_vendor`, `get_invoice_details`, `request_vendor_shipment` |
| Accounting | Cash, invoices, pricing and margins, and preparing payment requests (it never pays) | `accounting.md` | `get_cash_balance`, `get_invoice_details`, `get_lease_details`, `get_pricing`, `evaluate_discount`, `list_approval_requests`, `list_payments`, `request_payment_approval` |
| Facilities | Leases and rent | `facilities.md` | `get_lease_details`, `list_approval_requests`, `list_payments` |
| Customer Service | Drafts of customer messages, and of vendor or landlord messages when asked | `customer_service.md` | `list_board`, `save_draft` |

Every agent also has two tools that don't touch the database:
- `ask_agent(agent, request)` delegates to any other agent and returns that agent's report.
- `final_report` finishes with an `AgentReport`: a summary, facts with their sources, actions taken with ids, and what a person must do.

How a run works:

1. `python backend/run_ticket.py 101`, or the backend's `POST /api/tickets/101/run`, starts one run. The MCP server starts as a subprocess, and the Boss gets the ticket.
2. At each step, PydanticAI sends Claude the agent's prompt, its tool list and the conversation so far, and Claude picks one tool call. PydanticAI validates the call and runs it: an MCP tool through the server, or `ask_agent`. Then it adds the result to the conversation.
3. `ask_agent` runs the other agent on the request inside the same run, one level deeper and on the same budget, and hands its report back to the agent that asked.
4. The run ends when the Boss calls `final_report`, or when a limit or an error stops it. Every step is appended to `output/audit_trail.json`.
5. A payment request waits for a person. Approving it on the dashboard (`POST /api/approvals/<id>/approve`) or with `python backend/approve.py approve <id> --by "Name"` makes the payment at once, through the operator tool `approve_and_pay`. Running the ticket again lets the agents confirm the payment and carry on, for example with the restocking draft for ticket 101. Until Problem 7, the approval only marked the request approved, and Accounting paid it in the next run.

## Backend API (Problem 7)

`backend/main.py` is the FastAPI app the dashboard uses. Start it from `backend/` with `uvicorn main:app --reload --port 8000`; it answers at http://localhost:8000. CORS lets the local React dashboard call it (http://localhost:5175 and http://127.0.0.1:5175), and no other website. `CAMPUS_FRONTEND_ORIGINS` (comma-separated) adds another address, for example a dashboard on another port. `CAMPUS_RUN_LABEL`, for example "Problem 9 full run", is recorded with every run, approval and reset this backend writes to the audit trail, so one session's records stand apart from earlier tests.

- **Through MCP.** Every shop read and change goes through the MCP server. The routes use the operator tools over one shared connection, and agent runs use the agent tools. Only the reset uses the backend's own database code, `db.reset_working_copy()`, the same mechanism as `backend/reset_db.py`.
- **Background runs.** Starting a run answers at once (202) with a run id, and the run goes on in the background. Its steps are appended to the audit trail as they happen, and the dashboard polls `/api/events` with `after` set to the last `next_after`, so each event arrives once. A run ends as `finished` (the Boss handed in its report), `stopped` (a limit or an error the run recorded), or `failed` (an error outside the run, recorded as `run_failed`).
- **Errors.** An unknown ticket, run or approval request gets 404. A ticket id that isn't a number, or an empty approver name, gets 422. A second run on a ticket that is already running gets 409 with that run's id. A refused approval gets 409 with the reason. A failed MCP call gets 502.

| Method | URL | Purpose |
|---|---|---|
| GET | `/` | The service, the database in use, and the list of routes. |
| GET | `/api/health` | The database in use, the shop's current date (`desk.date_today`), which agents run (Claude, or simulated stand-ins), and the session's run label, if any. |
| GET | `/api/tickets` | The three tickets with their current status, whether each is resolved, their details (item, invoice or lease, notes), and any run in progress. |
| POST | `/api/tickets/{ticket_id}/run` | Starts the agent team on a ticket in the background, with an optional `note` for the Boss, and returns a run id; refused for an unknown ticket, a ticket already running, or during a reset. |
| GET | `/api/runs/{run_id}` | A run's status (`running`, `finished`, `stopped` or `failed`) and, once it ends, the Boss's report and usage. |
| GET | `/api/events?run_id=&after=&limit=` | Recent agent messages and tool events from the audit trail, for polling; `after=next_after` returns only new ones. |
| GET | `/api/approvals?status=` | Payment requests waiting for a person (`pending` by default; also `paid`, `rejected` or `all`). |
| POST | `/api/approvals/{approval_id}/approve` | A person approves a request (`approver`, `note`), and the operator tool `approve_and_pay` checks it and makes the payment in one transaction. |
| POST | `/api/approvals/{approval_id}/reject` | A person rejects a request (`approver`, `note`); nothing is paid. |
| GET | `/api/cash` | The checking balance and its date, with the total of requests waiting for approval. |
| POST | `/api/reset` | Restores the working copy from the original and clears stale payment requests; refused during an agent run or a payment. |

## Dashboard (Problem 8)

`frontend/` is the operations desk: React, Vite and TypeScript, in the black-and-pink style. `npm run dev` serves it at http://localhost:5175. It calls the backend's routes directly at http://localhost:8000 (`VITE_API_URL` changes the address), and the backend's CORS settings allow that origin. `output/design.md` explains the layout, the agents' identities, the status styles and the cash display.

- **One polling loop** (`frontend/src/useDesk.ts`). It runs every second while a run is going, every 5 seconds otherwise, and every 15 when the tab is hidden. Each time it reads:
  - `/api/events?after=<last index seen>`, so each audit record arrives once and is stored by its index;
  - `/api/runs/{id}` for the runs in progress;
  - `/api/tickets`, `/api/approvals?status=all` and `/api/cash`;
  - `/api/health`, on every idle poll and every fifth poll during a run.

  Starting a run or deciding a payment refreshes everything right away.
- **Runs from the audit trail** (`frontend/src/runs.ts`).
  - Records are grouped by run id, and each run belongs to one ticket, so tickets never mix.
  - Runs on another database are left out, such as isolated copies recorded in the same audit file. So are runs from before the last database reset.
  - A run's status comes from its `run_finished` or `run_failed` record, the backend's run status, or the ticket's `active_run_id`.
  - A run with no final record that nothing is tracking is shown as interrupted.
- **Statuses.**
  - The ticket badge is the backend's status, and "Resolved" is shown only when the backend says `resolved`.
  - A stopped, failed or interrupted run gets its own badge next to the ticket's status, so an unfinished ticket never looks resolved.
- **Approving.**
  - A person enters their name, clicks Approve & pay, and confirms.
  - The dashboard then calls `POST /api/approvals/{id}/approve`, and the backend's operator tool checks the request and makes the payment.
  - Only one decision is processed at a time, and a repeated click sends nothing.
  - The backend's refusal is shown in the request's card.
- **A note for the Boss** (added in Problem 9). "Add a note for the Boss" sends a note with the next run (the route's `note`). The Boss reads it as "Note from the operator", and the run's first record keeps it. The full run used it to pass the owner's decision on ticket 103 word for word.
- **Links and labels.** `#ticket-N` selects a ticket, and `#ticket-N/run-<run id>` shows one of its runs. The header shows the backend's run label, for example "Problem 9 full run".
- **Sandbox** (`backend/sandbox.py`). It runs the backend at http://localhost:8000 on a fresh copy of the original database in a temporary folder, with a temporary audit file.
  - By default the agents are scripted stand-ins: each ticket has a fixed script chosen from the copy's state, for example before or after invoice 501 is paid. Everything else is real.
  - `/api/health` reports the agents as simulated, and the dashboard labels them with a striped banner.
  - `--live` uses Claude, and `--fail-ticket N` makes a ticket's first run fail.
  - Stopping the sandbox deletes the copy.

## Business safeguards and token limits

These rules are enforced in code, in `mcp_server/shop.py` unless the list says otherwise. The prompts repeat them, but nothing relies on the prompts.

- **Shop date.** Every date the tools compute comes from `desk.date_today`: overdue days, days until rent is due, `paid_at`, and the estimated `expected_arrival`.
- **Vendor shipments.** A shipment request is refused while the vendor has an open invoice. It is also refused for a SKU that none of the vendor's invoices names, so no supplier is ever invented.
- **Human approval.** Agents can only ask for a payment: no agent tool approves or pays. The one tool that pays, `approve_and_pay`, is on the operator server, which agents are never connected to, and only a person's approval calls it.
  - People reach it through the dashboard's approval route (`POST /api/approvals/{id}/approve`) or through `backend/approve.py`, which needs a confirmation typed at a terminal.
  - Agent and system names are refused as approvers, and so is whoever asked for the payment.
  - Before paying, `approve_and_pay` checks again that the request is still pending, that the invoice is still open for the same amount or the rent still due on the same date, and that the cash covers it.
- **Who acted.** `backend/agents.py` fills in `requested_by`, `author` and `agent` from the run, not from what the model wrote. Each agent can only call its own role's tools. A payment records the person who approved it as `approved_by`.
- **Funds.** Requests and payments are refused when cash is short, and a refused approval leaves the request pending. The balance only changes under a `balance >= amount` guard, so it never goes negative.
- **No duplicates.** There is one live request per invoice, or per lease rent due date, enforced by a unique index. Paid requests, paid invoices and rent that has already moved on are refused, so approving the same request twice pays once.
- **Atomic payments.** The `payments` row, the cash balance, the invoice status or lease due date, and the request's status change in one `BEGIN IMMEDIATE` transaction. Any failure rolls all of them back.
- **No revenue.** No tool adds money to cash. `evaluate_discount` is a calculation only.
- **No contact.** Messages and shipment requests are drafts on the board; the `board_drafts.status` column only allows `draft`. The server has no network or email tool, and the agents' Claude calls have Claude Code's own tools and MCP servers switched off.
- **Data.** The server refuses the original database, and checks use isolated copies.
- **Runs, payments and reset** (`backend/main.py`). One run per ticket at a time, and payments take turns under a lock. A reset is refused while any run or payment is in progress, and runs and approvals are refused while a reset is underway. The reset restores the original, which clears stale pending requests (the response lists them). It adds a `database_reset` record to the audit trail and rewrites none.

**Limits.** These are set in `RunLimits` in `backend/models.py` and shared by every agent in a run through PydanticAI's usage limits. Delegated agents add to the same counts.

| Limit | Default |
|---|---|
| Steps (Claude calls) for the whole run | 40 |
| Tool calls for the whole run | 60 |
| Tokens for the whole run (Claude's reported input plus output) | 500,000 |
| Delegation depth | 2 (Boss → agent → agent) |
| One Claude call | 240-second timeout |

Reaching any limit stops the whole run, and the audit trail records why. The database stays as the last completed tool call left it.

## Audit trail

`output/audit_trail.json` is one JSON array. New records are appended to it, and earlier ones are never rewritten. Each record has:
- `time`, `run`, `ticket`, `agent`, `depth` and `chain` (for example `Boss -> Facilities -> Accounting`), and `step`;
- `action`: `run_started`, `tool`, `delegation_started`, `delegation`, `final_report` or `run_finished`; `human_decision`, which `backend/approve.py` and the dashboard's approval routes write; `database_reset`, which `POST /api/reset` writes; or `run_failed`, for an error the run itself couldn't record;
- `tool`, `input`, and either `result` or `error`, with `outcome` set to `ok`, `refused` or `error`;
- `approval`, for steps that touch a payment request;
- `usage`: steps, tool calls and tokens used so far, and how much of each is left;
- on `run_started`, the `trigger` (`command line` or `dashboard`) and the `database` used. Each run has its own `run` id, which `/api/events?run_id=` filters on, so separate runs stay apart;
- `label`, when the backend runs with `CAMPUS_RUN_LABEL`. It appears on `run_started`, `run_finished`, `human_decision` and `database_reset`, and every other record of a labeled run carries that run's id.

Strings that look like keys or email addresses are blanked, and long values are shortened.

## How it was checked

`backend/check_agents.py` runs every check on an isolated copy of the original database, never on the working copy. It compares the working copy's SHA-256 before and after the checks. These checks were written in Problem 5 and updated in Problem 7 for the new payment flow. The backend's own checks are in "How the backend was checked" below.

**SIMULATED checks.** Scripted PydanticAI models stand in for Claude. The agent loops, delegation, MCP server, rules and audit code are the real ones. All 44 checks pass.
- **Delegation.**
  - The Boss delegates to Inventory, Accounting and Customer Service, and gets each report back.
  - Boss → Facilities → Accounting nests two levels deep.
  - Usage is shared: the run counts 17 steps across 4 agents, once.
- **MCP access.**
  - Shop facts and changes come from the isolated copy, through the MCP server.
  - The agent modules never import `sqlite3` or `db.py`, and every agent tool is an MCP server tool.
  - An agent can't call another role's tool.
- **Payment safeguards** (updated in Problem 7).
  - Accounting has no tool that pays. A call to the old `pay_approved_request` fails as an unknown tool, and no agent can reach `approve_and_pay`. None of the agent server's 18 tools approves or pays.
  - "Accounting", "Claude" and the requester can't approve, and refused approvals pay nothing.
  - A simulated person's approval through `approve_and_pay` makes the payment, dated with the shop date, and updates cash, the invoice and the request together.
  - A request is paid once: approving it again is refused, and so is requesting the paid invoice again.
  - When cash is short the approval is refused, nothing changes, and the request stays pending.
  - A write failing mid-payment rolls everything back.
  - Approving rent moves the lease's due date a month on, and the next rent is refused for insufficient funds.
  - Cash ends at $160.00, which is $3,400.00 minus $3,240.00 paid: never negative, and no revenue added.
- **Vendors.** The shipment request is refused while invoice 501 is open, and saved as a draft once it is paid, with an estimated arrival of 2026-09-05 (the shop date plus 5 days). A saved draft doesn't send an order or confirm a shipment. The hoodie shipment is refused because no supplier is recorded.
- **Limits.** The checks cover the delegation depth limit, delegating to itself, the step limit (also inside a delegated agent) and the token limit.
- **Audit trail.** Appends keep the file valid JSON and leave earlier bytes unchanged. Every step record has the required fields, and records show the agent name actually sent.

Steps that stand in for a person, or that change the test data directly, are marked SETUP in the script. Two of them: lowering the cash to $1,000.00, and a trigger that makes the lease update fail.

**LIVE checks** (`--live`, run in Problem 5 under the earlier payment flow). Claude Opus 5.5 chooses every step. All 9 checks pass. The steps are in `output/audit_trail.json`, marked with the database `isolated copy live_copy.db`.
- **L1, ticket 101.**
  - The Boss delegated to Inventory, Accounting and Customer Service.
  - Accounting asked for approval request 1 for invoice 501 ($840.00), and nothing was paid.
  - A customer draft was saved, and the ticket was set to `waiting_on_approval`.
  - 18 steps, 14 tool calls, 116,746 tokens.
- **Simulated approval.** A simulated person, Pat Lee, approved request 1 through the operator tool.
- **L2, ticket 101 again.**
  - Accounting paid request 1 once as payment 1, and checking went from $3,400.00 to $2,560.00.
  - Invoice 501 is paid, and Inventory saved a draft shipment request for the reprint, with an estimated arrival of 2026-09-05 that depends on the vendor fulfilling it.
  - The ticket was set to `waiting_on_vendor`: it awaits vendor action, because the shipment request is still a draft. That was the status rule at the time. Problem 9's clarification changed it (see Completion criteria under The full run).
  - 14 steps, 11 tool calls, 89,341 tokens.

The live runs were recorded before one audit fix. In L2, the `request_vendor_shipment` record's input shows `requested_by: "Boss"`, as the model wrote it, but the run sent "Inventory", which is the draft's author in the database. Records now show the name actually sent, and the simulated checks confirm it. Earlier records are never rewritten, so that one stays as it was.

## How the backend was checked (Problem 7)

**Startup on port 8000.** From `backend/`, `uvicorn main:app --reload --port 8000` started ("Application startup complete") and answered at http://localhost:8000. `GET /`, `/api/health`, `/api/tickets`, `/api/cash` and `/api/approvals` all returned 200, using `data/campus_customs_new.db` with shop date 2026-08-31. A CORS preflight from http://localhost:5175 was allowed. Only read routes were called. Afterwards the working copy, the original and `output/audit_trail.json` had the same SHA-256 as before, and no ticket was run.

**`backend/check_api.py`.** It starts the backend inside the check at http://127.0.0.1:8010. `CAMPUS_DB_PATH` points it at an isolated copy of the original, and `CAMPUS_AUDIT_PATH` at a temporary audit file. Every request goes over real HTTP. Steps that change the test copy directly are marked SETUP: setting the cash to $500.00 for the insufficient-funds check and back to $3,400.00, slowing one payment down, and raising the cash before the stale request.

- **ROUTE checks: all 22 pass.**
  - Reads: the route list (11 routes), the health check on the isolated copy, the three tickets open and unresolved, $3,400.00 in checking, and no requests. CORS allows http://localhost:5175 and refuses http://example.com.
  - Invalid requests: an unknown ticket gets 404, a non-numeric ticket id 422, an unknown run 404, and an unknown approval request 404.
  - Approval safeguards:
    - An agent name ("Accounting") can't approve (409), and an empty approver name gets 422.
    - Insufficient funds: with $500.00 in checking, approving the $840.00 request gets 409. No payment is made, cash stays $500.00, and the request stays pending.
    - A person's approval pays: payment 1, checking $2,560.00 (also shown by `/api/cash`), and invoice 501 paid, all together.
    - Repeated approval: approving the same request again gets 409 ("already approved and paid (payment 1)"), and there is still one payment.
  - Reset during a payment gets 409, and the payment itself completes (rent paid, checking $160.00).
  - Reset: it restores the copy and clears the stale pending request (`cleared_pending_requests: [3]`). Afterwards the tickets are open, cash is $3,400.00, and there are no requests or payments. The audit file keeps its earlier bytes and gains a `database_reset` record.
  - Afterwards the working copy, the original and `output/audit_trail.json` are unchanged.
- **SIMULATED agent checks: all 11 pass.** Scripted PydanticAI models stand in for Claude, and the run, MCP server, rules and audit code are the real ones. These are not live agent results.
  - `POST /api/tickets/101/run` returns 202 with a run id. A second run on ticket 101 gets 409 with the running run's id, a reset during the run gets 409, and `/api/tickets` shows the run in progress.
  - Polling `/api/events?run_id=…&after=…` returned 15 events over 12 polls, each once, from `run_started` to `run_finished`. They include delegations, tool calls and the final report.
  - The run finished with the Boss's report. Ticket 101 shows `waiting_on_approval` and is not resolved. The agents' $840.00 request waits for a person, and nothing is paid.
  - A run whose model fails ends as `stopped: RuntimeError: simulated failure in the Boss's model`, and the backend keeps answering.
  - Two runs' events stay apart when filtered by `run_id` (15 and 2 events).
- **LIVE check (`--live`): all 5 pass.** Claude Opus 5.5 chose every step of one run of ticket 102 through the API, on the isolated copy. It used 15 steps, 12 tool calls and 92,399 tokens.
  - The run finished in the background. The Boss delegated to Facilities and Accounting, which used MCP tools.
  - Accounting only asked for the rent: request 1, $2,400.00, due 2026-09-02, requested by Accounting. Nothing was paid.
  - A simulated person (Pat Lee) approved it through the route. Checking went from $3,400.00 to $1,000.00, and lease 1's next due date moved to 2026-10-02.
  - Approving it again got 409, with still one payment.
  - Its audit records went to the temporary file, so `output/audit_trail.json` is unchanged.

**`backend/check_agents.py`.** All 44 SIMULATED checks pass with the new payment flow (see "How it was checked" above). `check_agents.py --live` was updated to the new flow but has not been run live again since Problem 5.

## How the dashboard was checked (Problem 8)

**Build and lint.** `npm run build` (`tsc -b`, now in strict mode, then `vite build`) and `npm run lint` (oxlint) pass with no errors or warnings. After the backend changes in Problem 8, `backend/check_api.py` still passes: 22 of 22 ROUTE and 11 of 11 SIMULATED checks.

**In the browser.** I started the dashboard with `npm run dev` and opened http://localhost:5175 in the desktop app's browser. The backend each time was one of:
- the sandbox, with simulated or live agents;
- the real backend, for a read-only look.

**SIMULATED sandbox** (scripted agents, labeled as simulated on the page; isolated copy):
- **Selecting a ticket** works by click, by keyboard and from a `#ticket-N` link.
- **Starting a run** sends one `POST /api/tickets/{id}/run`, even when Start is clicked three times at once. The backend refuses a second start while a run is going (409).
- **Live updates.**
  - Events appear while the run goes on, each once and in order: 23 for ticket 101's first run.
  - Run 1 holds records 0–22 and run 2 holds records 26–40, with no overlap. The three approval decisions between them belong to no run, so they appear in neither feed.
- **The roster** follows the run: "Working now", "Waiting on a teammate", "Done" and "Not involved".
- **Summaries.** After each run the outcome and a "What each agent did" card per agent are shown. For example, after ticket 101's second pass: "Saved draft shipment request #2 to Bulldog Print Co, estimated arrival Sep 5 (not sent)".
- **Approval safeguards.**
  - No approver name: nothing is sent, and focus moves to the name field.
  - "Accounting" as approver: refused, with the backend's reason shown in the card.
  - **Insufficient funds**, with checking set to $500.00 on the sandbox copy (a setup step): refused with "Not enough cash … Nothing was paid, and the request is still pending". The over-commitment warnings showed.
  - Three clicks on Confirm payment sent one request.
- **Cash decrease.**
  - Approving invoice 501 took checking from $3,400.00 to $2,560.00, with "Paid out −$840.00", and moved the request to "Recently decided" as payment #1.
  - Approving the rent took it to $160.00.
- **Final states, all distinct:**
  - ticket 101 "Waiting for vendor" after its second run;
  - ticket 102 "Resolved" after the rent was paid and its second run (solid pink badge and banner);
  - ticket 103 "Run stopped" with "Open" after its scripted failure, then "Needs a person" after a rerun.

**LIVE sandbox** (Claude Opus 5.5 on an isolated copy):
- **The run.** Ticket 101's run streamed 24 events live (19 steps, 15 tool calls, 151,255 tokens). The Boss asked Inventory, Accounting and Customer Service, and Accounting asked for approval of request 1 ($840.00). The page showed each agent's summary and the Boss's next steps.
- **The approval.** A simulated person, Pat Lee, approved the request through the dashboard. Checking went from $3,400.00 to $2,560.00.

**Real backend, read only** (working database):
- The desk showed the working database: three open tickets, $3,400.00, no requests, and no earlier runs. The runs in `output/audit_trail.json` were on isolated copies, so they are left out.
- It labeled the backend "Working database" and the agents "Claude Opus 5.5". No run was started.

**Layout and access.**
- **No horizontal scrolling** at 375, 768, 1024 or 1440 px wide:
  - one column on a phone, with approvals before the activity;
  - three columns on a wide screen.
- **Keyboard:** Tab reaches every control in order with a visible focus ring, and the skip link moves focus to the approvals. Escape cancels a payment confirmation and returns focus to the Approve button, and Space reopens it.
- **Screen readers:** the page announced "The agent team started on ticket 102.", then "The run on ticket 102 finished. The ticket is Waiting for approval." and "New request: Pay rent for lease 1, $2,400.00."
- **Contrast:** every text color is at least 5.4:1.

**Data.**
- The working copy, the original and `output/audit_trail.json` had the same SHA-256 before and after all of this.
- Each sandbox deleted its temporary copy when stopped. That needed one fix in Problem 8: a stop by SIGTERM now cleans up too.
- The test approver's name was cleared from the browser afterwards.

## The full run (Problem 9)

The real system ran all three tickets on the working database: the real backend, the five agents with Claude Opus 5.5, and the React dashboard. Every payment was approved by a person on the dashboard. The evidence is in four places:
- each ticket's **Actual** section and the **Cash** tab of `output/desk_tickets.html`;
- `output/resolved_tickets.json`;
- `output/resolved_board.html`, with the screenshots in `output/resolved_board/`;
- `output/audit_trail.json`.

**How it ran.**
- **Start.** The backend started with `CAMPUS_RUN_LABEL="Problem 9 full run"` (port 8000, working copy) and the dashboard with `npm run dev` (port 5175).
- **Reset.** The working copy was reset once from the original with `POST /api/reset` (audit record 41). Every original table then matched the original, and the starting checking balance in `cash_accounts` was **$3,400.00** (as of 2026-08-31).
- **Runs.** Each run was started with **Start agent team** / **Run the agent team again** on the dashboard. The tickets shared one database throughout. In order:
  1. **101, run 1:** the agents asked for approval of invoice 501 (request #1, $840.00). Ticket set to `waiting_on_approval`.
  2. **102, run 1:** Facilities → Accounting asked for approval of the rent (request #2, $2,400.00). Ticket set to `waiting_on_approval`.
  3. **103, run 1:** 8 of 20 hoodies in stock and no recorded supplier. The Boss had a 10% offer on the 8 drafted, unsent, and set the ticket to `waiting_on_customer`.
  4. **The owner's decision on 103.** The owner, Ken Tam, approved the 10% discount on the 8 hoodies "as a draft offer … not confirmation that the customer accepted it". This went to the Boss word for word as the run's operator note. **103, run 2** recorded it as ticket update #4, still `waiting_on_customer`.
  5. **Ken Tam approved request #1 on the dashboard** (payment #1; checking $2,560.00). **101, run 2:** Inventory saved shipment request draft #3, with an estimated arrival of 2026-09-05 if it is sent and fulfilled. Ticket set to `waiting_on_vendor`.
  6. **Ken Tam approved request #2 on the dashboard** (payment #2; checking $160.00). **102, run 2:** the Boss confirmed the payment and set the ticket to `resolved`.
- **Completion pass.** The owner then asked to tell the resolution of the shop's ticket apart from physical delivery or customer acceptance. Two more runs were started from the board, each with the owner's words as the operator note:
  7. **101, run 3**, with the owner's instruction: decide whether paying the invoice and preparing the reprint request and customer update complete the shop-side work, and resolve only if so. Customer Service replaced the out-of-date customer draft #1 with **draft #4**. The Boss judged the work **incomplete**: no order exists until a person sends draft #3. It kept `waiting_on_vendor` (update #7). One update was first refused because its note was over 1,000 characters, then retried.
  8. **103, run 3**, with the owner's decision: decline the original request for 20 because the shop can't supply it, and keep the approved 10% offer for 8 as an unsent alternative, not an accepted sale. The Boss recorded this and found no internal decision open. It set **`resolved`** (update #8).
- **Clarification.** The owner then clarified that, for this draft-only simulation, sending vendor or customer messages is outside scope and must not be required to complete the internal workflow. The Boss's prompt and the completion criteria below were changed to match. Then one more run was started from the board, with the owner's words as its note:
  9. **101, run 4**, with the owner's clarification: verify that invoice 501 is paid, the vendor request draft is complete and the replacement customer draft is accurate, and if so resolve the ticket for internal processing.
     - The Boss checked all three itself with `get_ticket`, `list_board` and `list_approval_requests`, found no decision open, and set **`resolved`** (update #9).
     - Its note says the drafts remain unsent, that no order, shipment or delivery is confirmed, and that the customer's order is not fulfilled.
     - One update was first refused because its note was over 1,000 characters, then retried.
- **No more runs.** 102 and 103 were not rerun, because their recorded outcomes already fit the clarified rule. 101 was rerun once, to apply the owner's clarification, not to get a different answer. Sending the drafts is outside this simulation.

### Completion criteria

**The rule now in force.** It comes from the owner's clarification, and is written into the Boss's prompt (`backend/prompts/boss.md`, "When a ticket is complete") with the matching status definitions.
- **A draft-only simulation.** Sending vendor or customer messages is outside the scope of this simulation and is never required to complete the shop's internal work. No ticket waits for a draft to be sent, and the agents never send anything.
- **Resolved means resolved for internal processing.** The Boss resolves a ticket when every check passes, each confirmed with the MCP tools:
  - every payment the ticket needs is paid, with a payment id;
  - every vendor draft it needs is complete: the vendor, SKU, size and quantity, with the arrival given as an estimate;
  - the latest customer draft accurately describes the situation: what was paid, that any vendor request is an unsent draft, that no order has been placed and nothing has shipped, and that any date is an estimate;
  - no decision is open for a person or the customer.
- **What the Boss's note must say:** the drafts remain unsent, and no order, shipment or delivery is confirmed. It never claims that a message was sent, stock arrived, the customer accepted, or the customer's order was fulfilled.
- **What resolution never requires:** sending, physical delivery, stock arriving or a customer's acceptance.
- **`waiting_on_vendor`** means the shop's own work can't go on until something changes on the vendor's side. A complete shipment request that is only unsent is not a reason to wait.

**How the rule got here.**
- **The completion pass (runs 7 and 8)** used the owner's earlier instruction, relayed word for word in the notes of audit records 132 and 144:
  - a ticket is resolved when the shop's own work on it is complete, as the Boss judges it;
  - resolution never requires physical delivery, stock arriving or a customer's acceptance;
  - internal work can't be skipped.

  The Boss counted sending the vendor order (draft #3) as internal work still to do, and kept 101 `waiting_on_vendor`. At that time the Boss's prompt gave "a shipment request still saved as a draft for a person to send" as an example of `waiting_on_vendor`.
- **The clarification.** The owner then clarified that sending is outside the simulation's scope. The Boss's prompt and these criteria were changed to match before run 9, which carried the owner's words as its note (audit record 149).

In every run, the Boss applied the rule itself through the agent and MCP workflow. No status was set by hand.

| Ticket | Shop-side work required | Done? | Result |
|---|---|---|---|
| 101 | Pay the blocking invoice with a person's approval; prepare a complete reprint request and an accurate customer update. | Yes, as the Boss checked in run 4: payment #1, draft #3 complete, and draft #4 accurate (it replaces #1). Sending them is outside this simulation. | Resolved for internal processing (update #9). The drafts remain unsent, and no order, shipment or delivery is confirmed. |
| 102 | Pay the rent once, with a person's approval, before it is due. | Yes: payment #2. | Resolved. |
| 103 | Decide the request; record the disposition; keep the approved alternative as an unsent draft. | Yes. The owner declined the 20, and the Boss recorded it (update #8). No supplier search is needed after the decline. | Resolved. Sending draft #2 is optional, and a sale would be new work. |

| Ticket | Final status | Payments | Unsent drafts | Blocker, and the human decision needed |
|---|---|---|---|---|
| 101 | `resolved` | #1 $840.00 (invoice 501) | #3 shipment request, #4 customer update, #1 out-of-date update | None for the shop's internal processing. Optional, outside this simulation: send drafts #3 and #4 (check the date in #4 first) and discard draft #1. No order exists until draft #3 is sent. |
| 102 | `resolved` | #2 $2,400.00 (rent, due 2026-09-02) | none | None. Lease 1 is next due 2026-10-02. |
| 103 | `resolved` | none ($0.00) | #2, the alternative offer to the Yale AI Club | None required. The request for 20 is declined. Sending draft #2 is optional. |

**Cash.**
- $3,400.00 − $840.00 (101) − $2,400.00 (102) − $0.00 (103) − $0.00 (completion pass) − $0.00 (clarification run) = **$160.00**, the same as `cash_accounts`.
- Each payment belongs to one ticket and is counted once.
- No revenue was added, and the balance never went below zero.

**Audit trail.**
- Records 0–40 are the earlier tests (Problem 5's live checks on isolated copies). They are byte-identical to the file before the run.
- Records 41–156 are this run, including the completion pass (132–148) and the clarification run (149–156). The reset, every `run_started` and `run_finished`, and both `human_decision` records carry `"label": "Problem 9 full run"`. Every other record carries its run's id.
- The nine runs used 85 steps, 66 tool calls and 623,877 tokens.

| Run | Ticket | Records | Steps · tool calls · tokens |
|---|---|---|---|
| `20261014-070708-df0364` | 101 | 42–64 | 18 · 14 · 125,866 |
| `20261014-070905-2871b1` | 102 | 65–84 | 16 · 13 · 101,368 |
| `20261014-071040-ad6c6d` | 103 | 85–106 | 17 · 13 · 121,371 |
| `20261014-071528-81f44f` | 103 | 107–111 | 3 · 2 · 26,463 (with the owner's note) |
| `20261014-071732-497bfa` | 101 | 113–124 | 9 · 7 · 57,764 |
| `20261014-072123-c99d07` | 102 | 126–131 | 4 · 3 · 23,486 |
| `20261014-073908-0bfcd4` | 101 | 132–143 | 9 · 7 · 81,363 (with the owner's instruction) |
| `20261014-074130-3c9e3f` | 103 | 144–148 | 3 · 2 · 19,902 (with the owner's decision) |
| `20261014-080030-3a7c0a` | 101 | 149–156 | 6 · 5 · 66,294 (with the owner's clarification) |

The payment approvals are records 112 (request #1) and 125 (request #2), both by Ken Tam through the dashboard's approval route. The owner's decisions, instruction and clarification are recorded word for word as the operator notes of the runs that carried them: records 107, 132, 144 and 149, each in `run_started`'s `input.request`. The Boss then wrote them to the ticket history (updates #4, #7, #8 and #9).

**Changes made for the run.**
- The run label (`CAMPUS_RUN_LABEL` in `backend/main.py`, and `label` in `run_ticket`).
- The dashboard's **Add a note for the Boss** field, which uses the existing `note` of `POST /api/tickets/{id}/run`.
- Run links (`#ticket-N/run-<id>`).
- A fix to the outcome banner of earlier runs: it had read "ticket not resolved" for a ticket resolved by a later run.
- For the completion pass, the dashboard's Resolved wording. It now says the shop's work on the ticket is complete, and points to "Next for a person" when steps outside the system remain, instead of always saying "Nothing is left to do".
- For the clarification, the completion rule in the Boss's prompt (`backend/prompts/boss.md`):
  - a new section, "When a ticket is complete";
  - `resolved` defined as internal processing complete under that rule;
  - `waiting_on_vendor` no longer covering a complete but unsent shipment request;
  - sending listed as optional, outside this simulation.

  A code comment in `frontend/src/format.ts` was reworded to match. The dashboard's behavior did not change.

After the completion-pass changes, `check_agents.py` passed 44 of 44, `check_api.py` passed 22 of 22 route and 11 of 11 simulated checks, and the dashboard's build and lint were clean. After the prompt change for the clarification:
- `check_agents.py` passed 44 of 44 again on an isolated copy, and the working copy, the original and the audit trail were unchanged;
- the dashboard's build and lint were clean.

**Agreement check.** A read-only script compared the outputs with the working copy and the audit trail after the clarification run. All 48 checks passed:
- **Database:**
  - the original is unchanged;
  - all three tickets are `resolved`;
  - there are still exactly two payments, and checking is $160.00;
  - there is no new payment request;
  - there are four drafts, all still drafts;
  - the latest ticket updates are #7 to #9. Update #9's note says the drafts remain unsent and no order, shipment or delivery is confirmed, and claims no fulfillment;
  - size S is still 0 on hand.
- **`resolved_tickets.json`:** checked against all of these. This includes the clarified and earlier completion criteria, both owner decisions on 103, and both owner messages on 101, with the earlier "not complete" assessment kept.
- **Audit trail:**
  - records 0–40 are byte-identical to the file before the run, 0–131 to the file before the completion pass, and 0–148 to the file before the clarification run;
  - there are 9 labeled runs, all finished, with the owner's words in the notes of runs 7, 8 and 9;
  - run 9 is records 149–156: the Boss alone, with read tools and `update_ticket` only;
  - there are no payment decisions after the two approvals;
  - the JSON's run totals match the audit trail.
- **`desk_tickets.html`:**
  - the Expected sections are byte-identical to before Problem 9;
  - the Actual sections give each final status and keep the earlier runs, including run 3's assessment of 101;
  - the Cash tab includes all three zero changes.
- **Prompt and log:**
  - the Boss's prompt has the completion rule, without the old wording;
  - `AI_prompts.md` has the sixth follow-up word for word;
  - this harness names run 9.
- **Screenshots:**
  - the six earlier screenshots are unchanged, and run 3's former final screenshot is kept byte-identical as `ticket-101-run-3.png`;
  - `resolved_board.html` has one Resolved group, with 101, 102 and 103;
  - each of the three final screenshots was taken after the clarification run. The dashboard at the same address shows that run, its banner, $160.00 and all three tickets resolved.

The final working database was kept as the run left it.
