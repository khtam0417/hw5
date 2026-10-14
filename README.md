# Campus Customs Multi-Agent Operations — Homework 5

Five AI agents run the back office of Campus Customs: they work through the shop's tickets, hand work to each other, and ask a person to approve anything that spends money.

- **MCP server** (`mcp_server/server.py`): the agents' only way into the shop database, built with FastMCP. It has lookups, plus changes checked against the shop rules in code (`mcp_server/shop.py`).
- **Agents** (`backend/agents.py`): five PydanticAI agents: Boss, Inventory, Accounting, Facilities and Customer Service. Claude Opus 5.5 chooses each step, any agent can delegate to any other, and every step is recorded in `output/audit_trail.json`.
- **Backend** (`backend/main.py`): a FastAPI app with the dashboard's routes. It lists the tickets, runs the agents on a ticket in the background, serves their messages and tool events for polling, lists payment requests, makes a payment when a person approves one, reports the checking balance, and resets the working copy.
- **Dashboard** (`frontend/`): a black-and-pink operations desk built with React, TypeScript and Vite. It shows the tickets, the agents' live work and a summary of each run, the payment requests with an Approve button, and the checking balance. `output/design.md` explains its design.

**Status:** complete (Problems 1–11). The full run on all three tickets is done: all three are resolved for internal processing, and checking holds $160.00 after two payments a person approved. See `output/resolved_tickets.json` and `output/resolved_board.html`, and the Reflection tab of `output/desk_tickets.html`. The repository's URL is in `output/github_url.txt`.

```
hw5/
├── AGENTS.md              project rules for AI coding agents, including the shop rules
├── AI_prompts.md          prompts used to build the project, one section per problem
├── README.md              this file
├── requirements.txt       Python packages for the backend and MCP server
├── .mcp.json              connects the MCP server to Claude Code in this folder
├── .env.example           settings template (copy to .env)
├── .gitignore             keeps .env, local settings, dependencies and build output out of Git
├── mcp_server/
│   ├── server.py          MCP server: python mcp_server/server.py (stdio), --http, or --operator
│   ├── shop.py            the shop rules and every database read and change the agents make
│   └── README.md          the server's purpose, database and tools
├── backend/
│   ├── agents.py          the five PydanticAI agents, delegation, shared limits, MCP access, auditing
│   ├── prompts/           one prompt per agent: boss.md, inventory.md, accounting.md, facilities.md, customer_service.md
│   ├── models.py          shared data types: AgentReport, RunLimits, RunDeps, TicketRunResult
│   ├── claude_bridge.py   the Homework 4 adapter: each PydanticAI step is one Claude call
│   ├── claude_config.py   the agents' Claude model and effort, and the sign-in from .env
│   ├── mcp_client.py      starts the MCP server for a run (agent tools) or for main.py and approve.py (operator tools)
│   ├── audit.py           the append-only audit trail
│   ├── run_ticket.py      run the agents on one ticket
│   ├── approve.py         a person approves (which pays) or rejects payment requests, in a terminal
│   ├── check_agents.py    simulated and live checks on isolated copies of the database
│   ├── check_api.py       checks of the backend's routes over HTTP, on an isolated copy
│   ├── check_claude.py    one short Claude call to confirm the sign-in works
│   ├── main.py            FastAPI app and its routes: uvicorn main:app --reload --port 8000
│   ├── sandbox.py         the backend on a temporary copy of the database, for trying the dashboard safely
│   ├── db.py              the backend's database access: the original and the working copy
│   └── reset_db.py        restores the working copy from the original
├── frontend/              the dashboard: Vite + React + TypeScript (npm run dev)
│   └── src/               App.tsx, useDesk.ts (polling and actions), runs.ts, components/, index.css
├── output/
│   ├── harness.md         how the system works: tables, tickets, MCP tools, agents, safeguards and limits
│   ├── design.md          the dashboard's layout, agent identities, status styles, cash display and choices
│   ├── resolved_tickets.json  the full run's outcome per ticket: status, agents, approvals and cash
│   ├── resolved_board.html    screenshots of the dashboard after the full run
│   ├── resolved_board/    the 10 screenshots that page shows
│   ├── mcp_smoke.json     the three MCP tool tests run through Claude Code, checked against the database
│   ├── audit_trail.json   one record for every agent-loop step and approval decision (append-only)
│   ├── desk_tickets.html  expected plans, actual results, the cash and the reflection; open it by double-clicking
│   └── github_url.txt     this repository's URL
└── data/                  both databases are in the repository
    ├── campus_customs.db       the original database, never changed
    └── campus_customs_new.db   the working copy, as the full run left it
```

## What you need

- **Python** 3.11 or newer (tested with 3.12).
- **Node.js** 20.19 or newer, or 22.12 or newer, with npm (tested with Node 24).
- **Git**, to clone the repository. The databases are included, so there is no data pack to unzip.
- **A Claude login:** a Claude subscription (Pro, Max, Team or Enterprise) or an Anthropic API key.

## 1. Install dependencies

Clone the repository, then install from its folder. Every command in this README runs from that folder, with paths relative to it:

```bash
git clone https://github.com/khtam0417/hw5.git
cd hw5
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm --prefix frontend ci
```

On Windows, use `.venv\Scripts\pip` instead of `.venv/bin/pip`, and `.venv\Scripts\python` instead of `.venv/bin/python` below. The Python packages are FastAPI, Uvicorn, python-dotenv, FastMCP, PydanticAI and the Claude Agent SDK, pinned in `requirements.txt`. The Claude Agent SDK package includes the Claude Code CLI that it runs, so the agents don't need a separate Claude Code install.

## 2. The databases

Both databases are in the repository:

- **`data/campus_customs.db` is the original** from the course data pack. Nothing writes to it; the code only reads it to make copies.
- **`data/campus_customs_new.db` is the working copy.** The backend and the MCP server use only this file. As submitted, it holds the end of the full run: all three tickets resolved, two approved payments, and $160.00 in checking. The backend creates a working copy from the original only when none exists, and never replaces one that does.

If the original is missing, the backend and the reset command stop with a message saying so. If the working copy is missing, the MCP server stops and says to run the reset command. None of them create replacement data.

### Reset before a fresh three-ticket run

The submitted working copy and `output/audit_trail.json` are the evidence for the saved results. To try the agents without changing them, use the sandbox (step 4), which works on a temporary copy. For a fresh three-ticket run on the real working copy, first restore it from the original:

```bash
.venv/bin/python backend/reset_db.py
```

This replaces everything in the working copy, including earlier agent work and any payment requests still waiting, with a fresh copy of the original. Run it while no run is in progress. If another program keeps the database busy for 10 seconds, the reset stops without changing anything.

With the backend running, `POST /api/reset` does the same reset. It is refused while an agent run or a payment is in progress.

A fresh run also appends to `output/audit_trail.json`. To return to the submitted evidence afterwards, restore both files from Git:

```bash
git checkout -- data/campus_customs_new.db output/audit_trail.json
```

## 3. Configure the Claude sign-in

Copy the settings template:

```bash
cp .env.example .env
```

Then set one credential in `.env`:

- **`CLAUDE_CODE_OAUTH_TOKEN`:** a Claude subscription token. Create it with `claude setup-token`, or, without Claude Code installed, with `.venv/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude setup-token` (use `python3.11` in the path for Python 3.11). Sign in in the browser it opens, then paste the printed token into `.env`.
- **`ANTHROPIC_API_KEY`:** or uncomment this line and paste a key from https://console.anthropic.com. It is used only when no token is set.

`PORTKEY_API_KEY` is not used. Never commit `.env`; it is git-ignored.

The agents use Claude Opus 5.5 (`claude-opus-5-5`) at high effort, set in `backend/claude_config.py`. They take the credential from `.env` (or from a `CLAUDE_CODE_OAUTH_TOKEN` already set in your shell) and ignore any other Claude login or API key in the environment. To confirm the sign-in works, run this from the project folder. It makes one short Claude call and prints the model that answered:

```bash
.venv/bin/python backend/check_claude.py
```

## 4. Run

**Backend.** In a terminal, from the project folder:

```bash
cd backend
../.venv/bin/uvicorn main:app --reload --port 8000
```

The API is at http://localhost:8000, with docs at http://localhost:8000/docs. `GET /` lists every route, and `output/harness.md` describes each one. `GET /api/health` reports the database in use, the shop's current date, and whether the agents are Claude or simulated. The backend accepts browser requests from the dashboard at http://localhost:5175.

**Dashboard.** In a second terminal:

```bash
cd frontend
npm run dev
```

Open **http://localhost:5175**. The dashboard calls the backend at http://localhost:8000. To use it:
1. Select a ticket on the left, then click **Start agent team**. The middle column shows each agent's messages, delegations and tool calls as they happen, and a summary of what each agent did when the run ends.
2. When the agents ask for a payment, it appears on the right with its purpose and amount. Enter your name under **Approving as**, then click **Approve & pay** and **Confirm payment**. The approval makes the payment, and the checking balance drops.
3. Click **Run the agent team again** so the Boss can take the next step. A ticket is shown as Resolved only when the Boss has recorded it resolved.
4. To give the Boss a decision only a person can make, such as accepting a proposed discount, click **Add a note for the Boss** before running. The note goes with the next run and is recorded in the audit trail.

To mark one session's records in `output/audit_trail.json`, start the backend with a label, for example `CAMPUS_RUN_LABEL="Problem 9 full run" ../.venv/bin/uvicorn main:app --port 8000`.

This runs the agents on the working database with real Claude usage. To try the dashboard without changing the working copy, use the sandbox below.

**Sandbox: the dashboard on a temporary copy.** Stop the backend first; the sandbox uses the same port. Then, from the project folder:

```bash
.venv/bin/python backend/sandbox.py
```

This runs the same backend at http://localhost:8000, on a fresh copy of the original database in a temporary folder, with its own temporary audit file. By default the agents are SIMULATED: scripted stand-ins follow a fixed script per ticket, and Claude is never called. Everything else is real, including the rules, approvals and payments on the copy. The dashboard shows a striped "Simulated agents" banner. Add `--live` to use the real Claude agents on the copy (real Claude usage), or `--fail-ticket 103` to see how a failed run looks. Ctrl+C stops the sandbox and deletes the copy. The working copy and `output/audit_trail.json` are never touched.

**MCP server.** Its tools are described in `mcp_server/README.md`. You don't need to start it for the app: each agent run starts it as a subprocess over stdio with the agents' 18 tools, and each approval starts it with `--operator`, the tools for a person (`backend/mcp_client.py`). Two other ways to use it:

- **Connect it to Claude Code.** Start `claude` in the repository folder. `.mcp.json` registers the server as `campus-customs` and runs it with `.venv/bin/python` (on Windows, change that to `.venv\Scripts\python.exe`). Approve the project server when Claude Code asks; `/mcp` shows its status. It works on the working copy.
- **Run it on its own:**

  ```bash
  .venv/bin/python mcp_server/server.py              # stdio: the agents' tools
  .venv/bin/python mcp_server/server.py --http       # Streamable HTTP at http://127.0.0.1:8006/mcp
  .venv/bin/python mcp_server/server.py --operator   # stdio: the tools for a person, which approve and pay
  ```

**If a port is busy:** run the backend on another port, for example `--port 8015`, and start the dashboard with `VITE_API_URL=http://localhost:8015 npm --prefix frontend run dev`. If port 5175 is busy, run the dashboard with `npm --prefix frontend run dev -- --port 5176`, and start the backend with `CAMPUS_FRONTEND_ORIGINS=http://localhost:5176` so it accepts that address.

## 5. Run the agents on a ticket

From the project folder:

```bash
.venv/bin/python backend/run_ticket.py 101
```

With the backend running, `POST /api/tickets/101/run` starts the same run in the background and returns a run id. `GET /api/events?run_id=<id>` then shows each step as it happens.

The Boss reads the ticket, asks the other agents for help, records a decision on the ticket, and prints its report. Shop facts and changes go through the MCP server, on the working copy. Every step is appended to `output/audit_trail.json`. A run stops at 40 Claude calls, 60 tool calls or 500,000 tokens, counted across all five agents. `--max-steps` changes the first limit.

The agents can only ask for a payment. The request waits for a person, and a person's approval makes the payment: on the dashboard (the backend's `POST /api/approvals/{id}/approve`), or in a terminal:

```bash
.venv/bin/python backend/approve.py
```

That lists the requests waiting for a decision. Then approve (which pays) or reject one, typing the confirmation it asks for:

```bash
.venv/bin/python backend/approve.py approve 1 --by "Your Name"
```

The approval is refused, with nothing changed, if the cash doesn't cover the payment or the request was already paid. Then run the ticket again, with a note, so the agents can confirm the payment and carry on:

```bash
.venv/bin/python backend/run_ticket.py 101 --note "Payment request 1 was approved and paid."
```

Before a full ticket-resolution run, reset the working copy with `backend/reset_db.py` (step 2).

## 6. Check the agents

```bash
.venv/bin/python backend/check_agents.py
```

These are SIMULATED checks. Scripted models stand in for Claude, so they cost nothing, and everything else is the real code. They run on an isolated copy of the original database and check delegation, MCP access, the audit trail and the payment safeguards.

```bash
.venv/bin/python backend/check_agents.py --live
```

This adds LIVE checks: Claude Opus 5.5 handles ticket 101 twice on another isolated copy, with a simulated person's approval, which pays, in between. These use real Claude usage, and their steps are appended to `output/audit_trail.json`. Neither command touches the working copy.

**Check the backend.**

```bash
.venv/bin/python backend/check_api.py
```

This starts the backend on port 8010, pointed at an isolated copy of the original database and a temporary audit file, and calls its routes over HTTP. Its ROUTE checks cover the reads, invalid ids, the approval safeguards (including insufficient funds and a repeated approval) and the reset. Its SIMULATED checks run scripted agents in the background: polling for events, refusing duplicate runs, and refusing a reset during a run. `--live` adds one LIVE run of ticket 102 with Claude through the API, which uses real Claude usage. The working copy, the original and `output/audit_trail.json` are never touched.

**Check the dashboard.**

```bash
npm --prefix frontend run build
npm --prefix frontend run lint
```

The build type-checks the TypeScript and bundles the dashboard into `frontend/dist/`, which is git-ignored. `npm --prefix frontend run preview` serves that build at http://localhost:5175.

## Completion criteria and limitations

**What "resolved" means.** This is a draft-only simulation: sending vendor or customer messages is outside its scope, and never required to complete the shop's internal work. The Boss resolves a ticket *for internal processing* when four checks pass:
- every payment the ticket needs is paid;
- every vendor draft it needs is complete;
- the latest customer draft describes the situation accurately;
- no decision is open.

Its note then says that the drafts remain unsent and that no order, shipment or delivery is confirmed. Resolved never means that anything was sent, ordered, shipped, delivered or sold, or that a customer accepted an offer. The rule is in `backend/prompts/boss.md` ("When a ticket is complete"), and `output/harness.md` (Completion criteria) explains how it was set.

**The full run's results.**

| Ticket | Result |
|---|---|
| 101 | Resolved for internal processing. Invoice 501 was paid, so the vendor can ship. The reprint request and the customer update are unsent drafts. Size S is still 0 on hand, and the customer's order is not fulfilled. |
| 102 | Resolved. The rent was paid once, before it was due. |
| 103 | Resolved. The owner declined the request for 20 hoodies. A 10% offer on the 8 in stock is kept as an unsent alternative, not a sale. |

Checking went from $3,400.00 to $160.00 after the two approved payments.

**Limitations.**
- **Nothing is ever sent.** Customer messages, vendor orders and landlord replies stay drafts on the board, and nobody is contacted.
- **Time doesn't pass.** The shop's date is fixed at `desk.date_today` (2026-08-31), so estimated arrivals stay estimates and stock never arrives.
- **Only a person moves money.** The agents can only ask for a payment. A person's approval pays it, and sales revenue is never added to cash.
- **Suppliers come only from the database.** A vendor supplies a product only if one of its invoices names it. No tool adds a vendor, sources stock, refunds a customer or forecasts demand.
- **Owner decisions travel as notes.** A decision only a person can make, such as accepting a discount, reaches the Boss as the run's note, and the Boss records it on the ticket.
- **Results can vary.** Claude chooses each step, so running a ticket again can take different steps or wording.
- **Real runs cost Claude usage.** The full run used 623,877 tokens over nine runs. The simulated checks and the sandbox don't call Claude.
- **The audit trail mixes test and full-run records.** It is append-only: records 0–40 are earlier checks on isolated copies, and records 41–156 are the labeled full run.

## Shop rules

The rules the agents must follow are listed in `AGENTS.md`, and `output/harness.md` describes how the code enforces them:
- the shop's current date comes from `desk.date_today`;
- vendor lead times come from the `vendors` table, and a vendor with an open unpaid invoice cannot ship;
- every payment needs human approval and enough cash;
- cash never goes negative, and incoming revenue is never added;
- customer and vendor messages stay as drafts on the board.
