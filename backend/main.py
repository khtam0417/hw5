"""Campus Customs operations backend (Homework 5).

Run from the backend folder:
    uvicorn main:app --reload --port 8000

Routes, for the React dashboard (http://localhost:5175, allowed by CORS; CAMPUS_FRONTEND_ORIGINS adds more
origins, comma-separated):
- GET  /                                     the service and its routes
- GET  /api/health                           the database in use, the shop's current date, and which agents run
- GET  /api/tickets                          every ticket, its status, whether it is resolved, its details, and any run in progress
- POST /api/tickets/{ticket_id}/run          start the agent team on a ticket in the background; returns the run id
- GET  /api/runs/{run_id}                    a run's status and, once it ends, the Boss's report
- GET  /api/events                           recent agent messages and tool events, for polling (?run_id=, ?after=)
- GET  /api/approvals                        payment requests waiting for a person (?status= for others)
- POST /api/approvals/{approval_id}/approve  a person approves: the payment is checked and made in one step
- POST /api/approvals/{approval_id}/reject   a person rejects a payment request
- GET  /api/cash                             the checking balance
- POST /api/reset                            restore the working copy from the original (refused during a run or payment)

Shop reads and changes all go through the MCP server. The dashboard's go through the operator tools
(mcp_server/server.py --operator), a person's channel. The agents' go through the agent tools, which can ask
for a payment but never approve or make one. Only the reset uses the backend's own database code,
db.reset_working_copy(), the same mechanism as reset_db.py. Agent runs happen in the background, and every
step is appended to the audit trail (output/audit_trail.json), which /api/events reads.

CAMPUS_DB_PATH and CAMPUS_AUDIT_PATH point the backend at an isolated database copy and audit file. The checks
(check_api.py) and the sandbox (sandbox.py) use them, so the working tickets are never touched. CAMPUS_RUN_LABEL
(for example "Problem 9 full run") is recorded with every run, approval and reset this backend writes to the audit
trail, so one session's records can be told apart from earlier tests.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from fastmcp import Client
from pydantic import BaseModel, Field
from pydantic_ai.models import Model

import db
from agents import new_run_id, run_ticket
from audit import AUDIT_FILE, AuditTrail
from claude_bridge import MODEL_NAME
from mcp_client import database_label, server_transport
from models import AgentName

FRONTEND_ORIGINS = ["http://localhost:5175", "http://127.0.0.1:5175"]  # the Vite dev server (frontend/vite.config.ts)
FRONTEND_ORIGINS += [origin.strip().rstrip("/") for origin in os.environ.get("CAMPUS_FRONTEND_ORIGINS", "").split(",") if origin.strip()]
TICKET_DETAILS = ("sku", "size", "qty", "invoice_id", "lease_id", "notes", "created_at")
AUDIT_PATH = Path(os.environ["CAMPUS_AUDIT_PATH"]) if os.environ.get("CAMPUS_AUDIT_PATH") else AUDIT_FILE
DATABASE = database_label(db.DB_PATH)
CASH_ACCOUNT = "checking"
EVENTS_LIMIT = 100

# The agents' model: Claude through the adapter (claude_bridge.py). The simulated checks swap in scripted models.
MODEL_FOR: Callable[[AgentName], Model] | None = None
MODEL_LABEL = MODEL_NAME
RUN_LABEL = os.environ.get("CAMPUS_RUN_LABEL") or None

audit = AuditTrail(AUDIT_PATH)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class OperatorClient:
    """One connection to the MCP server's operator tools, shared by every request. Calls take turns, and the
    server is started again if it has stopped."""

    def __init__(self) -> None:
        self._client: Client | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        client = Client(server_transport(db.DB_PATH, operator=True))
        await client.__aenter__()
        self._client = client

    async def stop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.__aexit__(None, None, None)

    async def restart(self, while_stopped: Callable[[], Awaitable[Any]]) -> None:
        """Stop the server, run while_stopped (the reset), and start it again; starting recreates the tables the
        server adds. Other calls wait meanwhile."""
        async with self._lock:
            await self.stop()
            try:
                await while_stopped()
            finally:
                await self.start()

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._lock:
            if self._client is None or not self._client.is_connected():
                await self.stop()
                await self.start()
            result = await self._client.call_tool(tool, args or {}, raise_on_error=False)
        if result.is_error:
            detail = getattr(result.content[0], "text", "") if result.content else ""
            raise HTTPException(502, f"The MCP server couldn't run {tool}: {detail or 'no detail'}")
        return result.structured_content or {}


class BackendState:
    """What the backend tracks between requests: the runs it started, and whether a payment or reset is underway."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.lock = asyncio.Lock()  # starting a run, a payment and a reset check each other under this lock
        self.payment = asyncio.Lock()  # held while a payment is being made
        self.resetting = False

    def active_runs(self) -> list[str]:
        return [run_id for run_id, run in self.runs.items() if run["status"] == "running"]

    def active_run_for(self, ticket_id: int) -> str | None:
        return next((run_id for run_id in self.active_runs() if self.runs[run_id]["ticket_id"] == ticket_id), None)


operator = OperatorClient()
state = BackendState()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db.ensure_working_copy()
    await operator.start()
    yield
    for task in list(state.tasks.values()):
        task.cancel()
    await operator.stop()


app = FastAPI(title="Campus Customs Operations API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=FRONTEND_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


class RunRequest(BaseModel):
    note: str | None = Field(None, max_length=1000, description="Extra instructions for the Boss, e.g. that a payment was approved.")


class Decision(BaseModel):
    approver: str = Field(min_length=1, max_length=80, description="The name of the person who clicked Approve or Reject.")
    note: str = Field("", max_length=500)


@app.get("/")
def index() -> dict[str, Any]:
    """The service and its routes."""
    routes = [
        f"{next(iter(route.methods - {'HEAD'}))} {route.path}: {(route.endpoint.__doc__ or '').splitlines()[0]}"
        for route in app.routes
        if isinstance(route, APIRoute)
    ]
    return {"service": "Campus Customs Operations API", "database": DATABASE, "routes": routes}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """The database in use, the shop's current date, and which agents run: Claude, or simulated stand-ins."""
    shop_date = await operator.call("get_shop_date")
    return {
        "status": "ok",
        "database": DATABASE,
        "date_today": shop_date["date_today"],
        "agents": {"model": MODEL_LABEL, "simulated": MODEL_FOR is not None},
        "run_label": RUN_LABEL,
    }


@app.get("/api/tickets")
async def tickets() -> dict[str, Any]:
    """Every ticket with its current status, whether it is resolved, its details, and any run in progress."""
    listed = await operator.call("list_tickets")
    return {
        "date_today": listed["date_today"],
        "tickets": [
            {
                "id": ticket["id"],
                "type": ticket["type"],
                "requester": ticket["requester"],
                "subject": ticket["subject"],
                "status": ticket["status"],
                "resolved": ticket["status"] == "resolved",
                "active_run_id": state.active_run_for(ticket["id"]),
                **{field: ticket.get(field) for field in TICKET_DETAILS},
            }
            for ticket in listed["tickets"]
        ],
    }


async def _run_in_background(run_id: str, ticket_id: int, note: str | None) -> None:
    run = state.runs[run_id]
    try:
        result = await run_ticket(
            ticket_id,
            note,
            db_path=db.DB_PATH,
            audit_path=AUDIT_PATH,
            model_for=MODEL_FOR,
            model_label=MODEL_LABEL,
            run_id=run_id,
            trigger="dashboard",
            label=RUN_LABEL,
        )
        run.update(
            status="finished" if result.finished else "stopped",
            stop_reason=result.stop_reason,
            report=result.report.model_dump() if result.report else None,
            usage=result.usage,
        )
    except Exception as error:  # run_ticket records its own failures; this catches anything it couldn't
        run.update(status="failed", stop_reason=f"{type(error).__name__}: {error}")
        audit.record(
            run=run_id, ticket=ticket_id, agent="Boss", action="run_failed", outcome="error", error=run["stop_reason"], database=DATABASE, label=RUN_LABEL
        )
    finally:
        run["ended"] = now()
        state.tasks.pop(run_id, None)


@app.post("/api/tickets/{ticket_id}/run", status_code=202)
async def start_run(ticket_id: int, body: RunRequest | None = None) -> dict[str, Any]:
    """Start the agent team on a ticket in the background, and return the run id to poll."""
    async with state.lock:
        if state.resetting:
            raise HTTPException(409, "The database is being reset. Try again in a moment.")
        if (active := state.active_run_for(ticket_id)) is not None:
            raise HTTPException(409, {"message": f"Ticket {ticket_id} already has a run in progress.", "run_id": active})
        ticket = await operator.call("get_ticket", {"ticket_id": ticket_id})
        if not ticket.get("found"):
            raise HTTPException(404, f"No ticket with id {ticket_id}.")
        run_id = new_run_id()
        state.runs[run_id] = {"run_id": run_id, "ticket_id": ticket_id, "status": "running", "started": now(), "database": DATABASE}
        state.tasks[run_id] = asyncio.create_task(_run_in_background(run_id, ticket_id, body.note if body else None))
    return {
        "run_id": run_id,
        "ticket_id": ticket_id,
        "status": "running",
        "status_url": f"/api/runs/{run_id}",
        "events_url": f"/api/events?run_id={run_id}",
    }


@app.get("/api/runs/{run_id}")
def run_status(run_id: str) -> dict[str, Any]:
    """A run's status and, once it has ended, the Boss's report and usage."""
    if (run := state.runs.get(run_id)) is None:
        raise HTTPException(404, f"No run {run_id} was started by this backend.")
    return run


@app.get("/api/events")
def events(
    run_id: str | None = None,
    after: int | None = Query(None, ge=-1, description="Only events after this index (the last next_after)."),
    limit: int = Query(EVENTS_LIMIT, ge=1, le=500),
) -> dict[str, Any]:
    """Recent agent messages and tool events from the audit trail, for polling with after=next_after."""
    records = audit.read()
    matching = [(index, record) for index, record in enumerate(records) if run_id is None or record.get("run") == run_id]
    page = [item for item in matching if item[0] > after][:limit] if after is not None else matching[-limit:]
    next_after = page[-1][0] if page else (after if after is not None else len(records) - 1)
    return {"events": [{"index": index, **record} for index, record in page], "next_after": next_after, "total_records": len(records)}


@app.get("/api/approvals")
async def approvals(status: str = "pending") -> dict[str, Any]:
    """Payment requests waiting for a person (status=paid, rejected or all for the others)."""
    listed = await operator.call("list_requests", {"status": None if status == "all" else status})
    return {"status": status, "approval_requests": listed["approval_requests"]}


async def _decide(approval_id: int, decision: Decision, tool: str) -> dict[str, Any]:
    async with state.lock:
        if state.resetting:
            raise HTTPException(409, "The database is being reset. Try again in a moment.")
        await state.payment.acquire()
    try:
        result = await operator.call(tool, {"approval_id": approval_id, "approver": decision.approver, "note": decision.note})
    finally:
        state.payment.release()
    refusal = result.get("refused")
    audit.record(
        ticket=result.get("ticket_id"),
        agent="person",
        action="human_decision",
        tool=tool,
        input={"approval_id": approval_id, "approver": decision.approver, "note": decision.note or None},
        result=result,
        outcome="refused" if refusal else "ok",
        approval={"approval_id": approval_id, "status": result.get("status"), "payment_id": result.get("payment_id"), "refused": refusal},
        database=DATABASE,
        trigger="dashboard",
        label=RUN_LABEL,
    )
    if refusal:
        raise HTTPException(404 if refusal.startswith("No approval request") else 409, refusal)
    return result


@app.post("/api/approvals/{approval_id}/approve")
async def approve(approval_id: int, decision: Decision) -> dict[str, Any]:
    """A person approves a payment request: the operator tool checks it and makes the payment in one step."""
    return await _decide(approval_id, decision, "approve_and_pay")


@app.post("/api/approvals/{approval_id}/reject")
async def reject(approval_id: int, decision: Decision) -> dict[str, Any]:
    """A person rejects a payment request. Nothing is paid."""
    return await _decide(approval_id, decision, "reject_request")


@app.get("/api/cash")
async def cash() -> dict[str, Any]:
    """The checking balance, with the total of payment requests waiting for approval."""
    balance = await operator.call("get_cash_balance")
    checking = next((account for account in balance["accounts"] if account["name"] == CASH_ACCOUNT), None)
    if checking is None:
        raise HTTPException(404, "The database has no checking account.")
    return {
        "account": CASH_ACCOUNT,
        "balance": checking["balance"],
        "as_of": checking["date"],
        "date_today": balance["date_today"],
        "waiting_for_approval_total": balance["waiting_for_approval_total"],
    }


@app.post("/api/reset")
async def reset() -> dict[str, Any]:
    """Restore the working copy from the original, clearing stale payment requests; refused during a run or payment."""
    async with state.lock:
        if running := state.active_runs():
            raise HTTPException(409, {"message": "An agent run is in progress, so the database can't be reset.", "run_ids": running})
        if state.payment.locked():
            raise HTTPException(409, "A payment is being made, so the database can't be reset.")
        state.resetting = True
    try:
        stale = (await operator.call("list_requests", {"status": "pending"}))["approval_requests"]
        await operator.restart(while_stopped=lambda: asyncio.to_thread(db.reset_working_copy))
        shop_date = await operator.call("get_shop_date")
        still_pending = (await operator.call("list_requests", {"status": "pending"}))["approval_requests"]
    except db.DatabaseMissing as error:
        raise HTTPException(500, str(error)) from None
    except sqlite3.OperationalError as error:  # another program kept the database locked
        raise HTTPException(409, f"The database is busy, so it wasn't reset ({error}).") from None
    finally:
        state.resetting = False
    cleared = [request["id"] for request in stale]
    audit.record(
        agent="person",
        action="database_reset",
        tool="POST /api/reset",
        result={"cleared_pending_requests": cleared, "date_today": shop_date["date_today"]},
        outcome="ok",
        database=DATABASE,
        trigger="dashboard",
        label=RUN_LABEL,
    )
    return {
        "reset": True,
        "database": DATABASE,
        "restored_from": "data/campus_customs.db",
        "cleared_pending_requests": cleared,
        "pending_after_reset": len(still_pending),
        "date_today": shop_date["date_today"],
    }
