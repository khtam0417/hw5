"""Checks for the Campus Customs agents (Homework 5): delegation, MCP access, the audit trail and the payment safeguards.

Run from the project folder:
    .venv/bin/python backend/check_agents.py           SIMULATED checks only: scripted models, no Claude calls
    .venv/bin/python backend/check_agents.py --live    also the LIVE checks: Claude Opus 5.5 chooses every step

Every check runs on an isolated copy of the original database (data/campus_customs.db) in a temporary
folder, never on the working copy, which is confirmed unchanged at the end.

SIMULATED checks replace Claude with scripted PydanticAI FunctionModels whose choices are fixed in advance.
Everything else is the real code: the PydanticAI agent loops and delegation, the MCP server and its rules, and
the audit trail (written to a temporary file). Agents can only ask for payments. A person's approval, which
makes the payment, is simulated through the operator tool approve_and_pay, the same path as the dashboard's
Approve button and backend/approve.py. Steps that stand in for that person, or that change the test data
directly (lowering the cash, making a write fail), are marked as setup.

LIVE checks run the real agents with Claude through the adapter on a fresh isolated copy, and append their
records to output/audit_trail.json, marked with the copy's name. They use real Claude usage. The person's
approval between the two live runs is simulated through the operator tool.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from fastmcp import Client
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from agents import AGENT_TOOLS, run_ticket
from audit import AUDIT_FILE, AuditTrail
from mcp_client import PROJECT_ROOT, server_transport
from models import AgentName, RunLimits, TicketRunResult

ORIGINAL_DB = PROJECT_ROOT / "data" / "campus_customs.db"
WORKING_DB = PROJECT_ROOT / "data" / "campus_customs_new.db"
AGENT_MODULES = ("agents.py", "claude_bridge.py", "claude_config.py", "models.py", "audit.py", "mcp_client.py")
PERSON = "Pat Lee"  # the simulated person who approves payments in these checks
PAYING_TOOLS = {"approve_and_pay", "reject_request", "pay_approved_request", "decide_approval_request"}

RESULTS: list[dict[str, Any]] = []


def check(kind: str, name: str, passed: bool, detail: str = "") -> bool:
    RESULTS.append({"kind": kind, "check": name, "passed": bool(passed), "detail": detail})
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return bool(passed)


def section(title: str) -> None:
    print(f"\n{title}")


# --- Test data ---------------------------------------------------------------------------------------------


def isolated_copy(folder: Path, name: str) -> Path:
    """A fresh copy of the original database, made with SQLite's backup (the original is opened read-only)."""
    target = folder / name
    with (
        closing(sqlite3.connect(f"{ORIGINAL_DB.as_uri()}?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(target)) as copy,
    ):
        source.backup(copy)
    return target


def rows(db: Path, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Read the isolated copy directly, to verify what the agents did (the agents themselves only use MCP)."""
    with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql, params)]


def setup_sql(db: Path, sql: str, params: tuple = ()) -> None:
    """SETUP: change the isolated copy directly, to create a situation for a check."""
    with closing(sqlite3.connect(db)) as con:
        con.execute(sql, params)
        con.commit()


async def operator(db: Path, tool: str, args: dict[str, Any], audit_path: Path | None = None) -> dict[str, Any]:
    """SETUP: a simulated person using an operator tool, the same path as the dashboard's Approve button. The
    decision is recorded in the audit file as main.py records it. A tool error comes back as {"error": ...}."""
    async with Client(server_transport(db, operator=True)) as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    outcome = result.structured_content or {"error": " ".join(getattr(part, "text", "") for part in result.content) or "tool error"}
    if audit_path is not None:
        AuditTrail(audit_path).record(
            ticket=outcome.get("ticket_id"),
            agent="person",
            action="human_decision",
            tool=tool,
            input=args,
            result=outcome,
            outcome="error" if "error" in outcome else ("refused" if outcome.get("refused") else "ok"),
            approval={"approval_id": args.get("approval_id"), "status": outcome.get("status"), "payment_id": outcome.get("payment_id")},
            trigger="check (simulated person)",
        )
    return outcome


async def approve(db: Path, approval_id: int, approver: str, audit_path: Path | None = None) -> dict[str, Any]:
    """SETUP: a simulated person clicks Approve, which makes the payment."""
    return await operator(db, "approve_and_pay", {"approval_id": approval_id, "approver": approver}, audit_path)


async def agent_server_call(db: Path, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    async with Client(server_transport(db)) as client:
        result = await client.call_tool(tool, args)
    return result.structured_content or {}


async def agent_server_tools(db: Path) -> set[str]:
    async with Client(server_transport(db)) as client:
        return {tool.name for tool in await client.list_tools()}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


# --- Scripted models (SIMULATED) ---------------------------------------------------------------------------

Step = tuple[str, dict[str, Any]]
FAIL_STEP = "__fail__"  # a scripted step that makes the model raise, to simulate a failure


def report(summary: str) -> Step:
    return ("final_report", {"summary": summary, "facts": [], "actions_taken": [], "needs_human": []})


class Script:
    """Scripted models: for each agent, one list of steps per time that agent is run, used in order. Each step is
    the tool call the agent makes; Claude is never called. `delay` slows every step down, so a check can act while
    a run is still going."""

    def __init__(self, plans: dict[AgentName, list[list[Step]]], tokens_per_step: int = 1_000, delay: float = 0.0) -> None:
        self.plans = {name: list(runs) for name, runs in plans.items()}
        self.tokens_per_step = tokens_per_step
        self.delay = delay
        self.current: dict[tuple[str, int], list[Step]] = {}

    def model_for(self, name: AgentName) -> FunctionModel:
        async def step(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if self.delay:
                await asyncio.sleep(self.delay)
            taken = sum(isinstance(message, ModelResponse) for message in messages)
            key = (name, id(messages[0]))
            if taken == 0:  # a new run of this agent: start its next plan
                queue = self.plans.get(name, [])
                self.current[key] = queue.pop(0) if queue else []
            plan = self.current.get(key, [])
            tool, args = plan[taken] if taken < len(plan) else report(f"{name}: scripted plan finished.")
            if tool == FAIL_STEP:
                raise RuntimeError(args.get("message", "simulated failure"))
            usage = RequestUsage(input_tokens=self.tokens_per_step, output_tokens=self.tokens_per_step // 10)
            return ModelResponse(parts=[ToolCallPart(tool, args)], usage=usage)

        return FunctionModel(step, model_name=f"scripted:{name}")


async def simulated_run(
    db: Path, audit_path: Path, ticket_id: int | None, plans: dict[AgentName, list[list[Step]]], limits: RunLimits | None = None, tokens_per_step: int = 1_000
) -> TicketRunResult:
    script = Script(plans, tokens_per_step)
    return await run_ticket(
        ticket_id,
        db_path=db,
        audit_path=audit_path,
        limits=limits,
        model_for=script.model_for,
        model_label="scripted (SIMULATED, no Claude)",
        trigger="check",
    )


def run_records(audit_path: Path, run_id: str) -> list[dict[str, Any]]:
    return [record for record in AuditTrail(audit_path).read() if record.get("run") == run_id]


def payment_count(db: Path) -> int:
    return rows(db, "SELECT count(*) AS n FROM payments")[0]["n"]


def checking(db: Path) -> float:
    return rows(db, "SELECT balance FROM cash_accounts WHERE name = 'checking'")[0]["balance"]


# --- SIMULATED checks ------------------------------------------------------------------------------------------


async def simulated_checks(folder: Path) -> None:
    kind = "SIMULATED"
    db = isolated_copy(folder, "simulated_copy.db")
    audit_path = folder / "simulated_audit_trail.json"
    print(f"\n=== SIMULATED checks: scripted models, no Claude calls (isolated copy {db.name}) ===")

    # S1: ticket 101, first pass. Delegation, MCP access, an approval request, and no way for an agent to pay.
    section("S1  Ticket 101, first pass: the Boss delegates to Inventory, Accounting and Customer Service")
    s1 = await simulated_run(
        db,
        audit_path,
        101,
        {
            "Boss": [[
                ("get_ticket", {"ticket_id": 101}),
                ("ask_agent", {"agent": "Inventory", "request": "Ticket 101: check CC-TEE-WHITE size S, and whether vendor 1 can ship the reprint."}),
                ("ask_agent", {"agent": "Accounting", "request": "Ticket 101: invoice 501 blocks Bulldog Print Co. Check cash and request approval to pay it."}),
                ("ask_agent", {"agent": "Customer Service", "request": "Ticket 101: draft an update to Tauhid Zaman: size S is out of stock."}),
                ("update_ticket", {"ticket_id": 101, "status": "waiting_on_approval", "note": "Invoice 501 must be paid first.", "agent": "Boss"}),
                report("Ticket 101 waits for a person to approve paying invoice 501."),
            ]],
            "Inventory": [[
                ("get_inventory", {"sku": "CC-TEE-WHITE", "size": "S"}),
                ("get_vendor", {"vendor_id": 1}),
                ("request_vendor_shipment", {"vendor_id": 1, "sku": "CC-TEE-WHITE", "size": "S", "qty": 1, "requested_by": "Inventory", "ticket_id": 101}),
                report("Size S: 0 in stock. Bulldog Print Co is blocked by open invoice 501."),
            ]],
            "Accounting": [[
                ("get_cash_balance", {}),
                # The model writes the wrong name here; the run fills in the real one.
                ("request_payment_approval", {"kind": "invoice", "ref_id": 501, "reason": "Unblocks the reprint for ticket 101.", "requested_by": "Customer Service", "ticket_id": 101}),
                ("pay_approved_request", {"approval_id": 1, "paid_by": "Accounting"}),  # the old payment tool: gone
                report("Approval request 1 ($840.00) waits for a person."),
            ]],
            "Customer Service": [[
                ("approve_and_pay", {"approval_id": 1, "approver": "Customer Service"}),  # an operator tool, never an agent's
                ("save_draft", {"recipient_type": "customer", "recipient": "Tauhid Zaman", "subject": "Your Bulldog tee", "body": "Size S is out of stock; we'll update you.", "author": "Customer Service", "ticket_id": 101}),
                report("Draft 1 saved for Tauhid Zaman."),
            ]],
        },
    )
    records = run_records(audit_path, s1.run_id)
    check(kind, "run finishes with the Boss's report", s1.finished, s1.stop_reason)
    started = [r for r in records if r["action"] == "delegation_started"]
    check(kind, "Boss delegates to three agents", sorted(r["input"]["agent"] for r in started) == ["Accounting", "Customer Service", "Inventory"],
          ", ".join(r["chain"] for r in started))
    boss_delegations = [r for r in records if r["agent"] == "Boss" and r["action"] == "delegation"]
    check(kind, "each delegation returns the agent's report to the Boss",
          len(boss_delegations) == 3 and all(r.get("result", {}).get("summary") for r in boss_delegations),
          " | ".join(r["result"]["summary"] for r in boss_delegations))
    inventory_call = next((r for r in records if r.get("tool") == "get_inventory"), {})
    check(kind, "agents read shop facts through MCP", inventory_call.get("result", {}).get("qty") == 0 and inventory_call.get("depth") == 1,
          f"Inventory's get_inventory returned qty {inventory_call.get('result', {}).get('qty')} from the isolated copy")
    shipment = next((r for r in records if r.get("tool") == "request_vendor_shipment"), {})
    check(kind, "vendor shipment blocked while invoice 501 is unpaid", shipment.get("outcome") == "refused", shipment.get("result", {}).get("refused", ""))
    old_pay = next((r for r in records if r.get("tool") == "pay_approved_request"), {})
    check(kind, "Accounting has no tool that pays", old_pay.get("outcome") == "error", (old_pay.get("error") or "")[:110])
    approval = rows(db, "SELECT id, status, requested_by, amount FROM approval_requests")
    check(kind, "approval request made through MCP, with the real agent's name",
          approval == [{"id": 1, "status": "pending", "requested_by": "Accounting", "amount": 840.0}], json.dumps(approval))
    asked = next((r for r in records if r.get("tool") == "request_payment_approval"), {})
    check(kind, "the audit records the name actually sent, not the model's", asked.get("input", {}).get("requested_by") == "Accounting",
          "the scripted model wrote 'Customer Service'; the record and the database say 'Accounting'")
    operator_try = next((r for r in records if r["agent"] == "Customer Service" and r.get("tool") == "approve_and_pay"), {})
    check(kind, "no agent can reach the person's approve-and-pay tool", operator_try.get("outcome") == "error", (operator_try.get("error") or "")[:110])
    check(kind, "no payment, cash unchanged", payment_count(db) == 0 and checking(db) == 3400.0, "payments 0, checking $3,400.00")
    draft = rows(db, "SELECT id, kind, status FROM board_drafts WHERE ticket_id = 101")
    check(kind, "customer message saved as a draft only", draft == [{"id": 1, "kind": "customer_message", "status": "draft"}], json.dumps(draft))
    ticket = rows(db, "SELECT status FROM tickets WHERE id = 101")[0]["status"]
    check(kind, "Boss records the decision on the ticket", ticket == "waiting_on_approval"
          and rows(db, "SELECT agent FROM ticket_updates WHERE ticket_id = 101") == [{"agent": "Boss"}], f"ticket 101 is {ticket}")
    steps_taken = sum(1 for r in records if r["action"] in ("tool", "delegation", "final_report"))
    check(kind, "delegated agents share the run's usage", s1.usage["steps"] == steps_taken == 17,
          f"{s1.usage['steps']} steps counted for the whole run = {steps_taken} step records across 4 agents")

    # H: only a person's approval, through the operator tool, makes a payment.
    section("H   A person's approval pays, through the operator tool approve_and_pay (simulated person: SETUP)")
    tools = await agent_server_tools(db)
    check(kind, "the agents' MCP server has no tool that approves or pays", not (PAYING_TOOLS & tools), f"{len(tools)} agent tools, none of them approves or pays")
    for name in ("Accounting", "Claude"):
        result = await approve(db, 1, name, audit_path)
        check(kind, f"'{name}' can't approve a payment", result.get("ok") is False, result.get("refused", ""))
    own = await agent_server_call(db, "request_payment_approval", {"kind": "rent", "ref_id": 1, "reason": "Self-approval test.", "requested_by": "Dana Smith"})
    result = await approve(db, own["approval_id"], "Dana Smith", audit_path)
    check(kind, "whoever asked for a payment can't approve it", result.get("ok") is False, result.get("refused", ""))
    await operator(db, "reject_request", {"approval_id": own["approval_id"], "approver": PERSON}, audit_path)  # clear it for later
    check(kind, "refused approvals pay nothing", payment_count(db) == 0 and checking(db) == 3400.0, "payments 0, checking $3,400.00")
    paid = await approve(db, 1, PERSON, audit_path)
    payments = rows(db, "SELECT kind, ref_id, amount, paid_at, approved_by FROM payments")
    check(kind, "a person's approval makes the payment, dated with the shop date", paid.get("status") == "paid"
          and payments == [{"kind": "invoice", "ref_id": 501, "amount": 840.0, "paid_at": "2026-08-31", "approved_by": PERSON}], json.dumps(payments))
    cash = rows(db, "SELECT balance, date FROM cash_accounts")[0]
    invoice = rows(db, "SELECT status FROM invoices WHERE id = 501")[0]["status"]
    request = rows(db, "SELECT status, payment_id, decided_by FROM approval_requests WHERE id = 1")[0]
    check(kind, "cash, invoice and request updated together", cash == {"balance": 2560.0, "date": "2026-08-31"} and invoice == "paid"
          and request == {"status": "paid", "payment_id": 1, "decided_by": PERSON},
          f"checking ${cash['balance']:,.2f}, invoice 501 {invoice}, request 1 {request['status']}")
    again = await approve(db, 1, PERSON, audit_path)
    check(kind, "approving the same request again is refused", again.get("ok") is False and payment_count(db) == 1, again.get("refused", ""))

    # S2: ticket 101, second pass. The vendor is unblocked; nothing is paid twice; no supplier is invented.
    section("S2  Ticket 101, second pass: the reprint request after the approved payment")
    s2 = await simulated_run(
        db,
        audit_path,
        101,
        {
            "Boss": [[
                ("list_approval_requests", {"ticket_id": 101}),
                ("ask_agent", {"agent": "Inventory", "request": "A person approved and paid invoice 501: request the S reprint, and check CC-HOOD-NAVY M for ticket 103."}),
                ("ask_agent", {"agent": "Accounting", "request": "Confirm invoice 501's payment; don't request it again."}),
                ("update_ticket", {"ticket_id": 101, "status": "waiting_on_vendor", "note": "Invoice 501 paid; reprint shipment request saved as a draft.", "agent": "Boss"}),
                report("Invoice 501 paid; the reprint shipment request is a draft for a person to send."),
            ]],
            "Inventory": [[
                ("request_vendor_shipment", {"vendor_id": 1, "sku": "CC-TEE-WHITE", "size": "S", "qty": 1, "requested_by": "Inventory", "ticket_id": 101}),
                ("request_vendor_shipment", {"vendor_id": 1, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 12, "requested_by": "Inventory", "ticket_id": 103}),
                report("Reprint shipment request saved as a draft; no recorded supplier for the hoodies."),
            ]],
            "Accounting": [[
                ("list_payments", {}),
                ("request_payment_approval", {"kind": "invoice", "ref_id": 501, "reason": "Pay it again?", "requested_by": "Accounting", "ticket_id": 101}),
                report("Invoice 501 was paid once, by the approval."),
            ]],
        },
    )
    records = run_records(audit_path, s2.run_id)
    check(kind, "run finishes", s2.finished, s2.stop_reason)
    order = rows(db, "SELECT kind, status, expected_arrival FROM board_drafts WHERE kind = 'vendor_order'")
    check(kind, "vendor unblocked once paid: draft shipment request, estimated arrival from shop date + lead days",
          order == [{"kind": "vendor_order", "status": "draft", "expected_arrival": "2026-09-05"}], json.dumps(order))
    hoodies = next((r for r in records if r.get("tool") == "request_vendor_shipment" and r["input"].get("sku") == "CC-HOOD-NAVY"), {})
    check(kind, "no supplier invented for the hoodies", hoodies.get("outcome") == "refused", hoodies.get("result", {}).get("refused", ""))
    again = next((r for r in records if r.get("tool") == "request_payment_approval"), {})
    check(kind, "requesting the paid invoice again is refused", again.get("outcome") == "refused" and payment_count(db) == 1,
          again.get("result", {}).get("refused", ""))

    # S3: ticket 102. Rent through nested delegation, then the approval's safeguards.
    section("S3  Ticket 102: rent request via nested delegation, then the approval's safeguards")
    s3 = await simulated_run(
        db,
        audit_path,
        102,
        {
            "Boss": [[("ask_agent", {"agent": "Facilities", "request": "Ticket 102: confirm lease 1's rent and get it requested for approval."}), report("Rent requested.")]],
            "Facilities": [[
                ("get_lease_details", {"lease_id": 1}),
                ("ask_agent", {"agent": "Accounting", "request": "Lease 1 rent, $2,400.00 due 2026-09-02: request approval."}),
                report("Rent for lease 1 is waiting for approval."),
            ]],
            "Accounting": [[
                ("request_payment_approval", {"kind": "rent", "ref_id": 1, "reason": "Rent for ticket 102.", "requested_by": "Accounting", "ticket_id": 102}),
                report("Rent approval requested."),
            ]],
        },
    )
    records = run_records(audit_path, s3.run_id)
    nested = next((r for r in records if r["agent"] == "Accounting"), {})
    check(kind, "nested delegation: Boss -> Facilities -> Accounting", s3.finished and nested.get("chain") == "Boss -> Facilities -> Accounting" and nested.get("depth") == 2,
          f"Accounting ran at depth {nested.get('depth')}")
    rent = rows(db, "SELECT id, status, amount, due_date FROM approval_requests WHERE kind = 'rent' AND status = 'pending'")
    check(kind, "rent approval requested", len(rent) == 1 and rent[0]["amount"] == 2400.0, json.dumps(rent))
    rent_id = rent[0]["id"] if rent else 0

    def unchanged(cash: float) -> bool:
        return (payment_count(db) == 1 and checking(db) == cash and rows(db, "SELECT next_due FROM leases")[0]["next_due"] == "2026-09-02"
                and rows(db, "SELECT status FROM approval_requests WHERE id = ?", (rent_id,))[0]["status"] == "pending")

    setup_sql(db, "UPDATE cash_accounts SET balance = 1000.0 WHERE name = 'checking'")  # SETUP: cash drops to $1,000
    short = await approve(db, rent_id, PERSON, audit_path)
    check(kind, "approval refused when cash is short; nothing changes, the request stays pending", short.get("ok") is False and unchanged(1000.0),
          short.get("refused", ""))
    setup_sql(db, "UPDATE cash_accounts SET balance = 2560.0 WHERE name = 'checking'")  # SETUP: restore the cash
    setup_sql(db, "CREATE TRIGGER simulated_failure BEFORE UPDATE ON leases BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END")
    failed = await approve(db, rent_id, PERSON, audit_path)
    check(kind, "a write failing mid-payment rolls the whole payment back", "error" in failed and unchanged(2560.0),
          "payments 1, checking $2,560.00 and the request still pending after a failed lease update")
    setup_sql(db, "DROP TRIGGER simulated_failure")  # SETUP: writes work again
    paid = await approve(db, rent_id, PERSON, audit_path)
    check(kind, "approved rent is paid; the lease's next due date moves a month on", paid.get("status") == "paid"
          and rows(db, "SELECT next_due FROM leases")[0]["next_due"] == "2026-10-02", f"checking ${checking(db):,.2f}, lease 1 next due 2026-10-02")
    following = await simulated_run(
        db,
        audit_path,
        102,
        {
            "Boss": [[("ask_agent", {"agent": "Accounting", "request": "Request next month's rent for lease 1."}), report("Done.")]],
            "Accounting": [[
                ("request_payment_approval", {"kind": "rent", "ref_id": 1, "reason": "Next month's rent.", "requested_by": "Accounting", "ticket_id": 102}),
                report("Next month's rent can't be covered yet."),
            ]],
        },
    )
    next_rent = next((r for r in run_records(audit_path, following.run_id) if r.get("tool") == "request_payment_approval"), {})
    check(kind, "next rent refused for insufficient funds", next_rent.get("outcome") == "refused", next_rent.get("result", {}).get("refused", ""))
    total_paid = sum(r["amount"] for r in rows(db, "SELECT amount FROM payments"))
    cash_now = checking(db)
    check(kind, "cash never negative, and no revenue added", cash_now >= 0 and round(3400.0 - total_paid, 2) == cash_now,
          f"$3,400.00 - payments ${total_paid:,.2f} = ${cash_now:,.2f}")

    # S4: shared limits stop runaway delegation and loops.
    section("S4  Shared limits: delegation depth, self-delegation, steps, tokens")
    deep = await simulated_run(
        db,
        audit_path,
        103,
        {
            "Boss": [[("ask_agent", {"agent": "Inventory", "request": "Depth test."}), report("Depth test done.")]],
            "Inventory": [[("ask_agent", {"agent": "Accounting", "request": "Depth test."}), report("Depth test done.")]],
            "Accounting": [[("ask_agent", {"agent": "Facilities", "request": "Depth test."}), report("Depth test done.")]],
        },
    )
    refusal = next((r for r in run_records(audit_path, deep.run_id) if r["agent"] == "Accounting" and r["action"] == "delegation"), {})
    check(kind, "delegation stops at the depth limit", deep.finished and refusal.get("outcome") == "refused"
          and not any(r["agent"] == "Facilities" for r in run_records(audit_path, deep.run_id)), refusal.get("result", {}).get("refused", ""))
    own = await simulated_run(db, audit_path, 103, {"Boss": [[("ask_agent", {"agent": "Boss", "request": "Ask myself."}), report("Done.")]]})
    refusal = next((r for r in run_records(audit_path, own.run_id) if r["action"] == "delegation"), {})
    check(kind, "an agent can't delegate to itself", refusal.get("outcome") == "refused", refusal.get("result", {}).get("refused", ""))
    looping = await simulated_run(db, audit_path, 103, {"Boss": [[("get_shop_date", {})] * 20]}, limits=RunLimits(max_steps=4))
    check(kind, "a looping agent is stopped by the step limit", not looping.finished and "limit" in looping.stop_reason and looping.usage["steps"] == 4,
          f"{looping.stop_reason[:110]}")
    chain = await simulated_run(
        db,
        audit_path,
        103,
        {"Boss": [[("ask_agent", {"agent": "Inventory", "request": "Loop."}), report("Done.")]], "Inventory": [[("get_shop_date", {})] * 20]},
        limits=RunLimits(max_steps=5),
    )
    interrupted = next((r for r in run_records(audit_path, chain.run_id) if r["agent"] == "Boss" and r["action"] == "delegation"), {})
    check(kind, "the limit counts delegated steps too, and stops the whole run", not chain.finished and chain.usage["steps"] == 5
          and interrupted.get("outcome") == "error", f"stopped inside Inventory after {chain.usage['steps']} steps; the Boss's delegation is recorded as cut short")
    heavy = await simulated_run(db, audit_path, 103, {"Boss": [[("get_shop_date", {})] * 20]}, limits=RunLimits(max_total_tokens=50_000), tokens_per_step=30_000)
    check(kind, "token use is capped by the token limit", not heavy.finished and "total_tokens" in heavy.stop_reason, f"{heavy.stop_reason[:110]}")

    # S5: the audit trail.
    section("S5  Audit trail: every step appended, earlier runs preserved, valid JSON")
    before = audit_path.read_bytes()
    records_before = len(json.loads(before))
    extra = await simulated_run(db, audit_path, 101, {"Boss": [[("get_ticket", {"ticket_id": 101}), report("Audit append test.")]]})
    after = audit_path.read_bytes()
    trail = json.loads(after)
    check(kind, "the file stays valid JSON and only grows", len(trail) == records_before + 4 and after.startswith(before.rstrip()[:-1].rstrip()),
          f"{records_before} records, then {len(trail)}; earlier bytes unchanged")
    runs = {r["run"] for r in trail if r.get("action") == "run_started"}
    check(kind, "earlier runs are preserved", {s1.run_id, s2.run_id, s3.run_id, extra.run_id} <= runs, f"{len(runs)} runs in the file")
    steps = [r for r in trail if r["action"] in ("tool", "delegation")]
    required = ("time", "run", "ticket", "agent", "depth", "chain", "step", "action", "tool", "input", "outcome", "usage")
    check(kind, "every step record has the run, ticket, agent, action, input, result or error, and usage",
          all(all(key in r for key in required) and ("result" in r or "error" in r) for r in steps), f"{len(steps)} step records checked")
    check(kind, "approvals are recorded", any(r.get("approval", {}).get("status") == "pending" for r in trail)
          and any(r.get("action") == "human_decision" and r.get("approval", {}).get("status") == "paid" for r in trail),
          "an agent's request, then a person's approval that paid it")

    # S6: MCP is the only way in.
    section("S6  MCP is the agents' only way to the database")
    imports = {name: (PROJECT_ROOT / "backend" / name).read_text() for name in AGENT_MODULES}
    shortcuts = [name for name, source in imports.items() if "import sqlite3" in source or "import db" in source or "from db import" in source]
    check(kind, "the agent code never opens the database", not shortcuts, f"checked {', '.join(AGENT_MODULES)}")
    every_tool = set().union(*AGENT_TOOLS.values())
    check(kind, "every agent tool is an MCP agent-server tool, and none pays", every_tool <= await agent_server_tools(db) and not (PAYING_TOOLS & every_tool),
          f"{len(every_tool)} tools across 5 agents")


# --- LIVE checks --------------------------------------------------------------------------------------------------


async def live_checks(folder: Path) -> None:
    kind = "LIVE"
    db = isolated_copy(folder, "live_copy.db")
    print(f"\n=== LIVE Claude checks: Claude Opus 5.5 chooses every step (isolated copy {db.name}) ===")
    print("    Records are appended to output/audit_trail.json.")

    section("L1  Ticket 101 with live Claude")
    first = await run_ticket(101, db_path=db, trigger="check (live)")
    records = run_records(AUDIT_FILE, first.run_id)
    agents = sorted({r["agent"] for r in records if r.get("depth", 0) >= 1})
    mcp_calls = [r for r in records if r["action"] == "tool"]
    check(kind, "run finishes", first.finished, first.stop_reason)
    check(kind, "Claude delegates to other agents", bool(agents), f"agents asked: {', '.join(agents)}")
    check(kind, "Claude's agents use MCP tools", bool(mcp_calls), ", ".join(sorted({f"{r['agent']}:{r['tool']}" for r in mcp_calls})))
    pending = rows(db, "SELECT id, amount, status, requested_by FROM approval_requests WHERE kind = 'invoice' AND ref_id = 501")
    check(kind, "an approval request for invoice 501 waits for a person", any(r["status"] == "pending" for r in pending), json.dumps(pending))
    check(kind, "nothing is paid without a person's approval", payment_count(db) == 0 and checking(db) == 3400.0, "payments 0, checking $3,400.00")
    print(f"    usage: {first.usage['steps']} steps, {first.usage['tool_calls']} tool calls, {first.usage['total_tokens']:,} tokens")
    if not pending:
        return

    section("L2  A simulated person approves, which pays; the live agents finish ticket 101")
    approval_id = pending[0]["id"]
    decision = await approve(db, approval_id, PERSON, AUDIT_FILE)
    print(f"    SETUP (simulated person): request {approval_id} {decision.get('status')} by {decision.get('approved_by')}")
    check(kind, "the approval pays the invoice once, with cash and invoice updated together", decision.get("status") == "paid"
          and payment_count(db) == 1 and checking(db) == 2560.0 and rows(db, "SELECT status FROM invoices WHERE id = 501")[0]["status"] == "paid",
          "checking $2,560.00, invoice 501 paid")
    second = await run_ticket(
        101, f"A person approved request {approval_id}, which paid invoice 501. Finish ticket 101.", db_path=db, trigger="check (live)"
    )
    check(kind, "run finishes", second.finished, second.stop_reason)
    check(kind, "the agents don't pay again", payment_count(db) == 1, f"{payment_count(db)} payment")
    ticket = rows(db, "SELECT status FROM tickets WHERE id = 101")[0]["status"]
    drafts = rows(db, "SELECT kind, status FROM board_drafts")
    check(kind, "ticket updated, and messages kept as drafts", ticket != "open" and all(d["status"] == "draft" for d in drafts),
          f"ticket 101 is {ticket}; drafts: {json.dumps(drafts)}")
    print(f"    usage: {second.usage['steps']} steps, {second.usage['tool_calls']} tool calls, {second.usage['total_tokens']:,} tokens")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Check the Campus Customs agents on isolated copies of the original database.")
    parser.add_argument("--live", action="store_true", help="also run the live Claude checks (uses real Claude usage)")
    args = parser.parse_args()

    working_before, original_before = sha256(WORKING_DB), sha256(ORIGINAL_DB)
    with tempfile.TemporaryDirectory(prefix="campus_checks_") as tmp:
        await simulated_checks(Path(tmp))
        if args.live:
            await live_checks(Path(tmp))
    print("\n=== Data safety ===")
    check("SIMULATED", "working copy and original unchanged", sha256(WORKING_DB) == working_before and sha256(ORIGINAL_DB) == original_before,
          "data/campus_customs_new.db and data/campus_customs.db have the same SHA-256 as before")

    print("\n=== Summary ===")
    for kind in ("SIMULATED", "LIVE"):
        mine = [r for r in RESULTS if r["kind"] == kind]
        if mine:
            print(f"{kind}: {sum(r['passed'] for r in mine)} of {len(mine)} checks passed")
    return 0 if all(r["passed"] for r in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
