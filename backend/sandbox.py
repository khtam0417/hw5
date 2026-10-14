"""Run the backend on a sandbox: a fresh, isolated copy of the database for trying the dashboard safely (Homework 5).

Run from the project folder (stop the real backend first: both use port 8000 unless you pass --port):
    .venv/bin/python backend/sandbox.py                     SIMULATED agents: scripted stand-ins, no Claude calls
    .venv/bin/python backend/sandbox.py --live              LIVE agents: Claude Opus 5.5 (uses real Claude usage)
    .venv/bin/python backend/sandbox.py --fail-ticket 103   simulated, and the first run of ticket 103 fails

The backend (main.py) serves its usual routes at http://localhost:8000, so the dashboard works unchanged. But
CAMPUS_DB_PATH points it at a fresh copy of the original database (data/campus_customs.db) in a temporary folder,
and CAMPUS_AUDIT_PATH at a temporary audit file. The working copy (data/campus_customs_new.db) and
output/audit_trail.json are never touched, and the temporary folder is deleted when the sandbox stops (Ctrl+C).

SIMULATED agents are scripted PydanticAI models. Each ticket has a fixed script, chosen from the sandbox copy's
state when the Boss starts a run: ticket 101 before and after invoice 501 is paid, ticket 102 before and after the
rent is paid, and ticket 103's discount offer. Everything else is real: the agent loops and delegation, the MCP
server and its rules, the database changes, the approvals and payments, and the audit records. /api/health reports
the agents as simulated, and the dashboard labels them.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import signal
import sqlite3
import sys
import tempfile
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from check_agents import FAIL_STEP, Step, isolated_copy
from models import AgentName

SIMULATED_LABEL = "scripted (SIMULATED, no Claude)"
RUN_LINE = re.compile(r"Run (\S+), ticket (\d+)\.")  # from the "This run" part of every agent's instructions

Plans = dict[AgentName, list[list[Step]]]  # for each agent, one list of steps per time it is asked, in order


def final(summary: str, facts: Iterable[str] = (), actions: Iterable[str] = (), needs_human: Iterable[str] = ()) -> Step:
    return ("final_report", {"summary": summary, "facts": list(facts), "actions_taken": list(actions), "needs_human": list(needs_human)})


def ask(agent: AgentName, request: str) -> Step:
    return ("ask_agent", {"agent": agent, "request": request})


# --- The scripts, one per ticket and situation (SIMULATED) ------------------------------------------------------


def ticket_101_blocked() -> Plans:
    """Before invoice 501 is paid: the tee is out of stock and the vendor is blocked, so Accounting asks a person."""
    return {
        "Boss": [[
            ("get_ticket", {"ticket_id": 101}),
            ask("Inventory", "Ticket 101: Tauhid Zaman wants 1 Classic Bulldog Tee (CC-TEE-WHITE) in size S. Check the stock, "
                "and whether the vendor linked to invoice 501 can ship a reprint."),
            ask("Customer Service", "Ticket 101: draft an honest update for Tauhid Zaman. Size S is out of stock, and a reprint "
                "from Bulldog Print Co waits on unpaid invoice 501. Don't promise a date."),
            ("update_ticket", {"ticket_id": 101, "status": "waiting_on_approval", "agent": "Boss",
                               "note": "Size S is out of stock. Bulldog Print Co can't ship the reprint until invoice 501 ($840.00) "
                                       "is paid, and Accounting asked a person to approve paying it."}),
            final(
                "Ticket 101 waits for a person to approve paying invoice 501 ($840.00). Size S is out of stock, and Bulldog "
                "Print Co can only ship a reprint once the invoice is paid. A draft update for the customer is on the board.",
                facts=["get_inventory: CC-TEE-WHITE size S qty 0", "get_invoice_details: invoice 501, $840.00, open, 3 days overdue"],
                actions=["payment approval request for invoice 501 (pending)", "customer draft for Tauhid Zaman",
                         "ticket 101 set to waiting_on_approval"],
                needs_human=["Approve the $840.00 payment for invoice 501 on the dashboard, which pays it.",
                             "Review the draft update for Tauhid Zaman. Nothing has been sent."],
            ),
        ]],
        "Inventory": [[
            ("get_inventory", {"sku": "CC-TEE-WHITE", "size": "S"}),
            ("get_invoice_details", {"invoice_id": 501}),
            ("get_vendor", {"vendor_id": 1}),
            ("request_vendor_shipment", {"vendor_id": 1, "sku": "CC-TEE-WHITE", "size": "S", "qty": 1, "requested_by": "Inventory",
                                         "ticket_id": 101, "note": "Reprint for ticket 101."}),
            ask("Accounting", "Ticket 101: invoice 501 ($840.00, 3 days overdue) blocks Bulldog Print Co from shipping the "
                "reprint. Check the cash and ask a person to approve paying it."),
            final(
                "CC-TEE-WHITE size S has 0 on hand. Bulldog Print Co (5-day lead time) is the vendor the database links to "
                "this tee, but it can't ship while invoice 501 is open, so the shipment request was refused. Accounting asked "
                "a person to approve paying the invoice.",
                facts=["get_inventory: qty 0 in Aisle B", "get_vendor: Bulldog Print Co, can_ship false"],
                actions=["shipment request refused while invoice 501 is open"],
            ),
        ]],
        "Accounting": [[
            ("list_approval_requests", {"ticket_id": 101}),
            ("get_cash_balance", {}),
            ("request_payment_approval", {"kind": "invoice", "ref_id": 501, "requested_by": "Accounting", "ticket_id": 101,
                                          "reason": "Unblocks Bulldog Print Co so it can ship the reprint for ticket 101."}),
            final(
                "Checking holds $3,400.00, which covers invoice 501 ($840.00) and leaves $2,560.00. I asked a person to "
                "approve the payment. Nothing is paid until they do.",
                facts=["get_cash_balance: checking $3,400.00"],
                actions=["payment approval request for invoice 501 (pending)"],
                needs_human=["Approve the $840.00 payment for invoice 501 on the dashboard."],
            ),
        ]],
        "Customer Service": [[
            ("list_board", {"ticket_id": 101}),
            ("save_draft", {"recipient_type": "customer", "recipient": "Tauhid Zaman", "subject": "Your Classic Bulldog Tee order",
                            "author": "Customer Service", "ticket_id": 101,
                            "body": "Hi Tauhid, size S of the Classic Bulldog Tee is out of stock right now. We're arranging a "
                                    "reprint and will write again with an estimated date once it's confirmed. Thanks for your "
                                    "patience! Campus Customs"}),
            final("I saved a draft update for Tauhid Zaman on the board. It says size S is out of stock and promises no "
                  "date. Nothing was sent.", actions=["customer draft for Tauhid Zaman"]),
        ]],
    }


def ticket_101_restock() -> Plans:
    """After invoice 501 is paid: Inventory saves the reprint request as a draft, with an estimated arrival."""
    return {
        "Boss": [[
            ("get_ticket", {"ticket_id": 101}),
            ("list_approval_requests", {"ticket_id": 101}),
            ask("Inventory", "Ticket 101: invoice 501 is paid. Request the reprint of 1 CC-TEE-WHITE in size S from Bulldog "
                "Print Co as a draft, and give the estimated arrival."),
            ask("Customer Service", "Ticket 101: draft an update for Tauhid Zaman. The reprint is requested, and 2026-09-05 is "
                "only an estimated arrival that depends on the vendor fulfilling it."),
            ("update_ticket", {"ticket_id": 101, "status": "waiting_on_vendor", "agent": "Boss",
                               "note": "Invoice 501 is paid. The reprint request is a draft for a person to send; arrival around "
                                       "2026-09-05 is only an estimate."}),
            final(
                "Ticket 101 now waits on the vendor. Invoice 501 is paid, Bulldog Print Co can ship, and a draft reprint "
                "request is on the board with an estimated arrival of 2026-09-05, if the vendor fulfills it. Nothing was sent.",
                facts=["list_approval_requests: the request for invoice 501 is paid"],
                actions=["draft shipment request to Bulldog Print Co", "customer draft for Tauhid Zaman",
                         "ticket 101 set to waiting_on_vendor"],
                needs_human=["Review and send the draft shipment request to Bulldog Print Co.",
                             "Review the draft update for Tauhid Zaman."],
            ),
        ]],
        "Inventory": [[
            ("get_vendor", {"vendor_id": 1}),
            ("request_vendor_shipment", {"vendor_id": 1, "sku": "CC-TEE-WHITE", "size": "S", "qty": 1, "requested_by": "Inventory",
                                         "ticket_id": 101, "note": "Reprint for ticket 101; invoice 501 is paid."}),
            final(
                "Bulldog Print Co can ship now that invoice 501 is paid. I saved a draft shipment request for 1 CC-TEE-WHITE "
                "in size S. Its estimated arrival is 2026-09-05 (the shop date plus the 5-day lead time) if the vendor "
                "fulfills it. Nothing was sent.",
                actions=["draft shipment request to Bulldog Print Co"],
            ),
        ]],
        "Customer Service": [[
            ("save_draft", {"recipient_type": "customer", "recipient": "Tauhid Zaman", "subject": "Update on your Classic Bulldog Tee",
                            "author": "Customer Service", "ticket_id": 101,
                            "body": "Hi Tauhid, we've requested a reprint of the Classic Bulldog Tee in size S. Our estimate is "
                                    "that it arrives around September 5, depending on the printer. We'll confirm once it ships. "
                                    "Campus Customs"}),
            final("I saved a draft update for Tauhid Zaman with 2026-09-05 as an estimate only. Nothing was sent.",
                  actions=["customer draft for Tauhid Zaman"]),
        ]],
    }


def ticket_101_already_requested() -> Plans:
    """The reprint request is already a draft: the Boss confirms the ticket still waits on the vendor."""
    return {
        "Boss": [[
            ("get_ticket", {"ticket_id": 101}),
            ("list_board", {"ticket_id": 101}),
            ("update_ticket", {"ticket_id": 101, "status": "waiting_on_vendor", "agent": "Boss",
                               "note": "The reprint request is already a draft on the board, waiting for a person to send it."}),
            final("Nothing new to do: the reprint request for ticket 101 is already a draft on the board. The ticket "
                  "waits on the vendor.", needs_human=["Send the draft shipment request to Bulldog Print Co."]),
        ]],
    }


def ticket_102_request() -> Plans:
    """Before the rent is paid: Facilities checks the lease, and Accounting asks a person to approve paying it."""
    return {
        "Boss": [[
            ("get_ticket", {"ticket_id": 102}),
            ask("Facilities", "Ticket 102: Elm City Properties says rent is due in 2 days. Verify lease 1's rent and due date, "
                "check that it isn't already requested or paid, and get the payment arranged for a person to approve."),
            ("update_ticket", {"ticket_id": 102, "status": "waiting_on_approval", "agent": "Boss",
                               "note": "Rent of $2,400.00 for lease 1 is due 2026-09-02. Accounting asked a person to approve paying it."}),
            final(
                "Ticket 102 waits for a person to approve the $2,400.00 rent for the Chapel Street shop, due 2026-09-02. "
                "Facilities confirmed the lease matches the notice, and checking covers the payment.",
                facts=["get_lease_details: lease 1, $2,400.00, next due 2026-09-02"],
                actions=["rent payment approval request for lease 1 (pending)", "ticket 102 set to waiting_on_approval"],
                needs_human=["Approve the $2,400.00 rent payment on the dashboard, ideally before 2026-09-02."],
            ),
        ]],
        "Facilities": [[
            ("get_lease_details", {"lease_id": 1}),
            ("list_approval_requests", {"ticket_id": 102}),
            ("list_payments", {}),
            ask("Accounting", "Ticket 102: lease 1's rent of $2,400.00 to Elm City Properties is due 2026-09-02, and nothing is "
                "requested or paid for it yet. Check the cash and ask a person to approve paying it."),
            final(
                "Lease 1 (Chapel Street shop) owes Elm City Properties $2,400.00, due 2026-09-02, in 2 days, matching the "
                "notice. No request or payment existed for it, so Accounting asked a person to approve paying it.",
                facts=["get_lease_details: $2,400.00 due 2026-09-02", "list_payments: no rent payment yet"],
            ),
        ]],
        "Accounting": [[
            ("get_cash_balance", {}),
            ("request_payment_approval", {"kind": "rent", "ref_id": 1, "requested_by": "Accounting", "ticket_id": 102,
                                          "reason": "Rent for the Chapel Street shop, due 2026-09-02 (ticket 102). Paying on time "
                                                    "keeps the lease in good standing."}),
            final("Checking covers the $2,400.00 rent. I asked a person to approve paying it; nothing is paid until they do.",
                  actions=["rent payment approval request for lease 1 (pending)"],
                  needs_human=["Approve the $2,400.00 rent payment on the dashboard."]),
        ]],
    }


def ticket_102_paid() -> Plans:
    """After the rent is paid: Facilities confirms it, and the Boss resolves the ticket."""
    return {
        "Boss": [[
            ("get_ticket", {"ticket_id": 102}),
            ask("Facilities", "Ticket 102: a person approved the rent request. Confirm lease 1's rent was paid once and that "
                "the next due date moved on."),
            ("update_ticket", {"ticket_id": 102, "status": "resolved", "agent": "Boss",
                               "note": "The $2,400.00 rent due 2026-09-02 was paid once, and lease 1 is next due 2026-10-02."}),
            final("Ticket 102 is resolved. The $2,400.00 rent due 2026-09-02 was paid once, and lease 1 is next due 2026-10-02.",
                  facts=["get_lease_details: next due 2026-10-02", "list_payments: one rent payment of $2,400.00"],
                  actions=["ticket 102 set to resolved"]),
        ]],
        "Facilities": [[
            ("get_lease_details", {"lease_id": 1}),
            ("list_payments", {}),
            final("The rent due 2026-09-02 was paid once ($2,400.00), and lease 1 is next due 2026-10-02.",
                  facts=["list_payments: one rent payment", "get_lease_details: next due 2026-10-02"]),
        ]],
    }


def ticket_103_offer() -> Plans:
    """The hoodie order: 8 of 20 in stock, no recorded supplier, and a recommended discount for a person to confirm."""
    return {
        "Boss": [[
            ("get_ticket", {"ticket_id": 103}),
            ask("Inventory", "Ticket 103: the Yale AI Club wants 20 Basic Hoodie Big Yale (CC-HOOD-NAVY) in size M. Check the "
                "stock, and whether the database links a vendor that could restock them."),
            ask("Accounting", "Ticket 103: work out the price and margin for the 8 CC-HOOD-NAVY hoodies at 10% and at 15% off, "
                "for a recommendation. Nothing is paid."),
            ask("Customer Service", "Ticket 103: draft a reply to the Yale AI Club offering the 8 hoodies in stock at a "
                "recommended 10% off ($52.20 each), and explain that the other 12 can't be promised. Say a person will confirm."),
            ("update_ticket", {"ticket_id": 103, "status": "needs_human", "agent": "Boss",
                               "note": "Only 8 of 20 are in stock, and no supplier is recorded for the hoodies. Recommended: the 8 "
                                       "at 10% off ($417.60). A person must confirm the offer."}),
            final(
                "Ticket 103 needs a person. Only 8 of the 20 hoodies are in stock, and the database records no supplier for "
                "them. I recommend offering the 8 at 10% off ($52.20 each, $417.60 in all), which stays above cost.",
                facts=["get_inventory: CC-HOOD-NAVY size M qty 8", "evaluate_discount: 10% off is $52.20 each, margin $241.60"],
                actions=["draft reply to the Yale AI Club", "ticket 103 set to needs_human"],
                needs_human=["Confirm or change the recommended 10% discount.", "Review and send the draft reply to the Yale AI Club."],
            ),
        ]],
        "Inventory": [[
            ("get_inventory", {"sku": "CC-HOOD-NAVY", "size": "M"}),
            ("list_vendors", {}),
            ("request_vendor_shipment", {"vendor_id": 1, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 12, "requested_by": "Inventory",
                                         "ticket_id": 103, "note": "Restock for ticket 103."}),
            final("CC-HOOD-NAVY size M has 8 on hand, 12 short of 20. No vendor's invoices name this hoodie, so the database "
                  "records no supplier, and the restock request was refused.",
                  facts=["get_inventory: qty 8 in Aisle A"]),
        ]],
        "Accounting": [[
            ("get_pricing", {"sku": "CC-HOOD-NAVY"}),
            ("evaluate_discount", {"sku": "CC-HOOD-NAVY", "qty": 8, "discount_percent": 10}),
            ("evaluate_discount", {"sku": "CC-HOOD-NAVY", "qty": 8, "discount_percent": 15}),
            final("At 10% off the 8 hoodies are $52.20 each ($417.60, margin $241.60); at 15% off, $49.30 each ($394.40, "
                  "margin $218.40). Both stay above the $22.00 unit cost. A sale adds nothing to cash.",
                  facts=["get_pricing: unit cost $22.00, list $58.00"]),
        ]],
        "Customer Service": [[
            ("save_draft", {"recipient_type": "customer", "recipient": "Yale AI Club", "subject": "Your hoodie request",
                            "author": "Customer Service", "ticket_id": 103,
                            "body": "Hi Yale AI Club, thanks for your order! We have 8 Basic Hoodie Big Yale in size M ready now "
                                    "and can offer them at 10% off ($52.20 each). We can't promise the other 12 yet. A member "
                                    "of our team will confirm the offer. Campus Customs"}),
            final("I saved a draft offer for the Yale AI Club on the board, pending a person's confirmation. Nothing was sent.",
                  actions=["draft reply to the Yale AI Club"]),
        ]],
    }


def failing_run(ticket_id: int) -> Plans:
    """A run whose Boss model breaks down after one delegation, to show how a failed run looks."""
    return {
        "Boss": [[
            ("get_ticket", {"ticket_id": ticket_id}),
            ask("Inventory", f"Ticket {ticket_id}: check the stock the ticket asks for."),
            (FAIL_STEP, {"message": "simulated failure: the Boss's model stopped answering"}),
        ]],
        "Inventory": [[
            ("get_ticket", {"ticket_id": ticket_id}),
            final("I read the ticket; the run stopped before I was asked anything else."),
        ]],
    }


class SandboxScript:
    """SIMULATED agents for the sandbox: for each run, a script for its ticket, picked from the sandbox copy's state
    when the Boss starts. Claude is never called."""

    def __init__(self, db: Path, delay: float, fail_ticket: int | None) -> None:
        self.db = db
        self.delay = delay
        self.fail_ticket = fail_ticket
        self.runs: dict[str, Plans] = {}
        self.current: dict[tuple[str, AgentName, int], list[Step]] = {}

    def choose(self, ticket_id: int) -> Plans:
        if ticket_id == self.fail_ticket:
            self.fail_ticket = None  # only the first run of that ticket fails
            return failing_run(ticket_id)
        with closing(sqlite3.connect(f"{self.db.as_uri()}?mode=ro", uri=True)) as con:
            invoice = con.execute("SELECT status FROM invoices WHERE id = 501").fetchone()
            lease = con.execute("SELECT next_due FROM leases WHERE id = 1").fetchone()
            reprint = con.execute("SELECT count(*) FROM board_drafts WHERE ticket_id = 101 AND kind = 'vendor_order'").fetchone()
        if ticket_id == 101:
            if invoice and invoice[0] == "open":
                return ticket_101_blocked()
            return ticket_101_already_requested() if reprint and reprint[0] else ticket_101_restock()
        if ticket_id == 102:
            return ticket_102_request() if lease and lease[0] == "2026-09-02" else ticket_102_paid()
        if ticket_id == 103:
            return ticket_103_offer()
        return {"Boss": [[("get_ticket", {"ticket_id": ticket_id}), final(f"The sandbox has no script for ticket {ticket_id}.")]]}

    @staticmethod
    def run_of(messages: list[ModelMessage], info: AgentInfo) -> tuple[str, int]:
        """The run id and ticket, from the "This run" line every agent gets in its instructions."""
        texts = [info.instructions or ""] + [message.instructions or "" for message in messages if isinstance(message, ModelRequest)]
        for text in texts:
            if match := RUN_LINE.search(text):
                return match.group(1), int(match.group(2))
        return "unknown", 0

    def model_for(self, name: AgentName) -> FunctionModel:
        async def step(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            await asyncio.sleep(self.delay)  # so the dashboard can show the run step by step
            run_id, ticket_id = self.run_of(messages, info)
            if run_id not in self.runs:
                self.runs[run_id] = self.choose(ticket_id)
            taken = sum(isinstance(message, ModelResponse) for message in messages)
            key = (run_id, name, id(messages[0]))
            if taken == 0:  # this agent was just asked: start its next script
                queue = self.runs[run_id].get(name, [])
                self.current[key] = queue.pop(0) if queue else []
            plan = self.current.get(key, [])
            tool, args = plan[taken] if taken < len(plan) else final(f"{name}: the sandbox script for this request is finished.")
            if tool == FAIL_STEP:
                raise RuntimeError(args.get("message", "simulated failure"))
            return ModelResponse(parts=[ToolCallPart(tool, args)], usage=RequestUsage(input_tokens=0, output_tokens=0))

        return FunctionModel(step, model_name=f"scripted:{name}")


def main_entry() -> int:
    parser = argparse.ArgumentParser(description="Run the backend on an isolated sandbox copy of the database.")
    parser.add_argument("--live", action="store_true", help="run the real agents with Claude (uses real Claude usage)")
    parser.add_argument("--port", type=int, default=8000, help="port for the backend (default 8000, where the dashboard looks)")
    parser.add_argument("--delay", type=float, default=0.7, help="seconds per simulated agent step (default 0.7)")
    parser.add_argument("--fail-ticket", type=int, help="make the first simulated run of this ticket fail")
    args = parser.parse_args()
    if args.live and args.fail_ticket is not None:
        parser.error("--fail-ticket only applies to simulated agents")

    # Stopping with SIGTERM or SIGHUP (a closed terminal, a process manager) exits normally, so the folder below is
    # deleted. Without this, uvicorn re-raises SIGTERM after its shutdown and the process ends before cleanup.
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda signum, frame: sys.exit(0))

    folder = Path(tempfile.mkdtemp(prefix="campus_sandbox_"))
    try:
        db = isolated_copy(folder, "sandbox.db")
        audit_path = folder / "sandbox_audit_trail.json"
        os.environ["CAMPUS_DB_PATH"] = str(db)
        os.environ["CAMPUS_AUDIT_PATH"] = str(audit_path)
        import uvicorn

        import main  # after the environment points it at the sandbox copy

        if not args.live:
            main.MODEL_FOR = SandboxScript(db, args.delay, args.fail_ticket).model_for
            main.MODEL_LABEL = SIMULATED_LABEL
        agents = "LIVE: Claude Opus 5.5 (real Claude usage)" if args.live else "SIMULATED: scripted stand-ins, no Claude calls"
        print(
            f"Campus Customs sandbox at http://localhost:{args.port}\n"
            f"  agents:   {agents}\n"
            f"  database: {db} (a fresh copy of the original, deleted when the sandbox stops)\n"
            f"  audit:    {audit_path}\n"
            "The working copy and output/audit_trail.json are not used. Press Ctrl+C to stop.",
            flush=True,
        )
        uvicorn.run(main.app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main_entry())
