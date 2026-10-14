"""Run the Campus Customs agents on one ticket, with live Claude (Homework 5).

Run from the project folder:
    .venv/bin/python backend/run_ticket.py 101
    .venv/bin/python backend/run_ticket.py 101 --note "Approval request 1 was approved and paid: finish the ticket."
    .venv/bin/python backend/run_ticket.py 101 --db /path/to/isolated_copy.db --max-steps 30

The Boss leads and asks the other agents as needed. Shop facts and changes go through the MCP server, on the
working copy (data/campus_customs_new.db) unless --db names another copy. Every step is appended to
output/audit_trail.json. The agents can only ask for a payment. A person's approval makes it, on the
dashboard (POST /api/approvals/{id}/approve) or with backend/approve.py; then run the ticket again so the
agents can confirm the payment and finish. The backend's POST /api/tickets/{id}/run starts the same run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from agents import run_ticket
from models import RunLimits


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Campus Customs agents on one ticket.")
    parser.add_argument("ticket_id", type=int, help="the ticket to handle, e.g. 101")
    parser.add_argument("--note", help="extra instructions for the Boss, e.g. that a payment was approved")
    parser.add_argument("--db", type=Path, help="an isolated copy of the database to use instead of the working copy")
    parser.add_argument("--max-steps", type=int, default=RunLimits.max_steps, help="Claude calls allowed for the whole run")
    args = parser.parse_args()

    result = asyncio.run(run_ticket(args.ticket_id, args.note, db_path=args.db, limits=RunLimits(max_steps=args.max_steps)))
    print(json.dumps(result.model_dump(), indent=2))
    return 0 if result.finished else 1


if __name__ == "__main__":
    raise SystemExit(main())
