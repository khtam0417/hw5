"""Approve (which pays) or reject payment requests, as a person (Homework 5).

Run from the project folder, in a terminal:
    .venv/bin/python backend/approve.py                                     list requests waiting for a decision
    .venv/bin/python backend/approve.py approve 1 --by "Your Name"
    .venv/bin/python backend/approve.py reject 1 --by "Your Name" --note "Why"
Add --db PATH to decide on an isolated copy instead of the working copy.

Besides the dashboard's Approve button (POST /api/approvals/{id}/approve in main.py), this is the only way a
payment gets approved, and approving makes the payment at once. Both use the MCP server's operator tools
(mcp_server/server.py --operator), which the agents are never connected to. This command shows the request and
asks you to type a confirmation, so it can't run unattended. Agent and system names are refused as approvers,
and so is whoever asked for the payment. The payment is refused, with nothing changed, if the cash doesn't
cover it or the request was already paid. Each decision is added to output/audit_trail.json.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

from audit import AuditTrail
from mcp_client import database_label, server_transport


async def operator_call(db_path: Path | None, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call one operator tool on a freshly started operator server."""
    async with Client(server_transport(db_path, operator=True)) as client:
        result = await client.call_tool(tool, args)
    return result.structured_content or {}


def describe(request: dict[str, Any]) -> str:
    what = f"invoice {request['ref_id']}" if request["kind"] == "invoice" else f"rent for lease {request['ref_id']}"
    ticket = f", ticket {request['ticket_id']}" if request.get("ticket_id") is not None else ""
    return (
        f"#{request['id']}  ${request['amount']:,.2f}  {what} due {request['due_date']}{ticket}  [{request['status']}]\n"
        f"     asked by {request['requested_by']} on {request['requested_at']}: {request['reason']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve or reject Campus Customs payment requests, as a person.")
    parser.add_argument("action", nargs="?", choices=["approve", "reject"])
    parser.add_argument("approval_id", nargs="?", type=int)
    parser.add_argument("--by", help="your name, as the person deciding")
    parser.add_argument("--note", default="", help="a short note on the decision")
    parser.add_argument("--db", type=Path, help="an isolated copy of the database to use instead of the working copy")
    args = parser.parse_args()

    requests = asyncio.run(operator_call(args.db, "list_requests", {})).get("approval_requests", [])
    if args.action is None:
        pending = [r for r in requests if r["status"] == "pending"]
        print("Payment requests waiting for a person:" if pending else "No payment requests are waiting for a decision.")
        for request in pending:
            print(describe(request))
        return 0
    if args.approval_id is None or not args.by:
        parser.error("give the request id and --by with your name, e.g. approve 1 --by \"Your Name\"")
    if not sys.stdin.isatty():
        print("A payment decision must be confirmed by a person at a terminal, so nothing was changed.", file=sys.stderr)
        return 2
    request = next((r for r in requests if r["id"] == args.approval_id), None)
    if request is None:
        print(f"No payment request #{args.approval_id}.", file=sys.stderr)
        return 1

    print(describe(request))
    confirmation = f"{args.action} {args.approval_id}"
    if input(f"Type '{confirmation}' to confirm, as {args.by}: ").strip().lower() != confirmation:
        print("Not confirmed, so nothing was changed.")
        return 1
    tool = "approve_and_pay" if args.action == "approve" else "reject_request"
    result = asyncio.run(
        operator_call(args.db, tool, {"approval_id": args.approval_id, "approver": args.by, "note": args.note})
    )
    AuditTrail().record(
        ticket=request.get("ticket_id"),
        agent="person",
        action="human_decision",
        tool=tool,
        input={"approval_id": args.approval_id, "approver": args.by, "note": args.note or None},
        result=result,
        outcome="ok" if result.get("ok") else "refused",
        approval={"approval_id": args.approval_id, "status": result.get("status"), "payment_id": result.get("payment_id"), "refused": result.get("refused")},
        database=database_label(args.db),
        trigger="approve.py",
    )
    if result.get("refused"):
        print(result["refused"])
    elif result["status"] == "paid":
        print(f"Request #{args.approval_id} approved by {args.by} and paid: payment {result['payment_id']}, {result['account']} now ${result['new_balance']:,.2f}.")
    else:
        print(f"Request #{args.approval_id} rejected by {args.by}. Nothing was paid.")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
