# Homework 5 — Campus Customs Multi-Agent Operations

Instructions for AI coding agents working in this folder. They add to the course-level `AGENTS.md` one folder up, and override it where the two differ.

## What we are building

- An **MCP server** (`mcp_server/server.py`) that gives all five agents one shared set of tools for the shop database.
- A **FastAPI backend** (`backend/main.py`) that runs five agents:
  - **Boss** coordinates tickets and makes final decisions.
  - **Inventory** checks stock and restocking.
  - **Accounting** checks cash, invoices and margins, and prepares approval requests.
  - **Facilities** handles leases and rent.
  - **Customer Service** drafts customer messages.

  Any agent can delegate to any other agent.
- A **React dashboard** (`frontend/`) for monitoring the agents' work and approving requests, in the black-and-pink style.

Build it one problem at a time, following the user's instructions. Check what already exists before creating or changing files.

## Prompt log

Keep `AI_prompts.md` updated as we work, one section per problem: the problem number and title, the user's actual prompt copied word for word (typos included), and, only if a follow-up was needed, the actual follow-up prompt with one sentence on what the first result was missing. Never invent prompts, follow-ups or completed work.

## Harness document

`output/harness.md` describes how the system works, starting with the database tables and the open tickets. Later problems expand it: add to it and keep the existing content.

## AI calls

- The agents use Claude through the Claude Agent SDK, the same setup as Homework 4, signing in with the credential in `.env` (`CLAUDE_CODE_OAUTH_TOKEN`, or `ANTHROPIC_API_KEY`).
- Model: Claude Opus 5.5 (`claude-opus-5-5`) at effort `high`. Both are set once in `backend/claude_config.py`, with the sign-in (`use_claude_login()`); every agent uses them. `python backend/check_claude.py` makes one short call to confirm the sign-in works.
- The course-level instruction to use the OpenAI API with GPT-5.6 Luna through Portkey does not apply to Homework 5. `PORTKEY_API_KEY` appears in `.env.example` only as an unused placeholder.

## Secrets and Git

- The final repository will be public. Secrets live only in `.env`, which is git-ignored; `.env.example` holds placeholders only.
- Never print, log or commit tokens or keys.
- Both databases are committed, because the submission requires them: `data/campus_customs.db` (the untouched original) and `data/campus_customs_new.db` (the final working copy). Nothing else in `data/` is committed, such as backups or journal files.

## Data

- `data/campus_customs.db` is the original database from `data.zip`. Never modify it: it is read-only on disk, and code opens it read-only, only to copy it.
- `data/campus_customs_new.db` is the working copy. The backend (through `backend/db.py`) and the MCP server (`mcp_server/server.py` and `shop.py`) use only this file.
- The agents (`backend/agents.py`) reach the database only through the MCP server. Agent code never imports `sqlite3` or `db.py`, and `backend/check_agents.py` checks this.
- Agents only prepare payment requests. A payment is made only when a person approves a request through the MCP server's operator tools (`--operator`: `approve_and_pay`, used by the backend's approval route in `backend/main.py` and by `backend/approve.py`). Never give an agent an approval or payment tool.
- Never overwrite an existing working copy automatically. `python backend/reset_db.py` restores it from the original; run it before a full ticket-resolution run. The backend's `POST /api/reset` uses the same reset and is refused during an agent run or a payment.
- To try or test the dashboard, run `python backend/sandbox.py`. It serves the backend on a temporary copy of the original with a temporary audit file, with simulated agents by default or `--live` for Claude. Don't test against the working copy.
- If a data file is missing, tell the user. Never create replacement or sample data.
- Inspect the actual schema (`sqlite3 data/campus_customs_new.db .schema`) before writing database code.

## Shop rules

Enforce these in code, not only in agent prompts.

1. Use `desk.date_today` as the shop's current date (`db.shop_date()`), never the computer's clock.
2. Get vendor lead times from the `vendors` table (`lead_days`). A vendor cannot ship while it has an open, unpaid invoice (an `invoices` row for that vendor with `status = 'open'`).
3. Require human approval before any payment. Refuse a payment when there is not enough cash. When a payment succeeds, update the relevant database records (for example the `cash_accounts` balance, a new `payments` row, and the invoice or lease that was paid).
4. Never allow a negative cash balance, and never add incoming revenue to cash.
5. Keep customer messages and vendor communications as drafts on the board. Never send them.
