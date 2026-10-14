"""Checks for the backend's routes (Homework 5): runs, events, approvals, cash and reset, on an isolated database.

Run from the project folder:
    .venv/bin/python backend/check_api.py           ROUTE checks and SIMULATED agent runs (scripted models, no Claude)
    .venv/bin/python backend/check_api.py --live    also one LIVE agent run of ticket 102 with Claude, through the API

The backend (main.py) runs inside this process at http://127.0.0.1:8010. CAMPUS_DB_PATH points it at an isolated
copy of data/campus_customs.db and CAMPUS_AUDIT_PATH at a temporary audit file, so the working copy, its tickets
and output/audit_trail.json are never touched. Every request goes over real HTTP.

- ROUTE checks call the routes and look only at their answers and at the isolated copy.
- SIMULATED agent runs swap Claude for scripted models (main.MODEL_FOR). Everything else is the real backend,
  MCP server and rules.
- LIVE runs use Claude through the adapter.
A person clicking Approve is simulated with POST /api/approvals/{id}/approve. Direct changes to the test data,
such as lowering the cash, are marked as setup. To catch a reset while a payment is in progress, the payment is
slowed down for one check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from check_agents import (
    ORIGINAL_DB,
    PERSON,
    RESULTS,
    WORKING_DB,
    Script,
    agent_server_call,
    check,
    checking,
    isolated_copy,
    payment_count,
    report,
    rows,
    section,
    setup_sql,
    sha256,
)

PORT = 8010
BASE = f"http://127.0.0.1:{PORT}"
FRONTEND = "http://localhost:5175"


def _parse(text: str) -> Any:
    """A response body: JSON when it is JSON (every route), else the text (a CORS preflight answers "OK")."""
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return text


def call(method: str, path: str, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[int, Any, dict[str, str]]:
    """One HTTP request to the backend: status, JSON body, headers."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, _parse(response.read().decode()), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, _parse(error.read().decode()), dict(error.headers)


def wait_for_run(run_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, run, _ = call("GET", f"/api/runs/{run_id}")
        if status == 200 and run["status"] != "running":
            return run
        time.sleep(0.3)
    raise TimeoutError(f"run {run_id} still running after {timeout} s")


def detail(body: Any) -> str:
    value = body.get("detail") if isinstance(body, dict) else body
    return value if isinstance(value, str) else json.dumps(value)


def start_backend(main: Any) -> Any:
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:
            raise TimeoutError("the backend didn't start")
        time.sleep(0.1)
    return server


def ticket_101_plans() -> dict[str, list[list[tuple[str, dict[str, Any]]]]]:
    return {
        "Boss": [[
            ("get_ticket", {"ticket_id": 101}),
            ("ask_agent", {"agent": "Inventory", "request": "Ticket 101: check CC-TEE-WHITE size S and vendor 1."}),
            ("ask_agent", {"agent": "Accounting", "request": "Ticket 101: request approval to pay invoice 501."}),
            ("update_ticket", {"ticket_id": 101, "status": "waiting_on_approval", "note": "Invoice 501 waits for a person.", "agent": "Boss"}),
            report("Ticket 101 waits for a person to approve invoice 501."),
        ]],
        "Inventory": [[("get_inventory", {"sku": "CC-TEE-WHITE", "size": "S"}), ("get_vendor", {"vendor_id": 1}), report("0 in stock; vendor blocked.")]],
        "Accounting": [[
            ("get_cash_balance", {}),
            ("request_payment_approval", {"kind": "invoice", "ref_id": 501, "reason": "Unblocks the reprint for ticket 101.", "requested_by": "Accounting", "ticket_id": 101}),
            report("Request 1 waits for a person."),
        ]],
    }


def run_checks(main: Any, db: Path, audit_path: Path, live: bool) -> None:
    route, simulated = "ROUTE", "SIMULATED"

    section("R1  Reads, through the operator MCP tools")
    status, body, _ = call("GET", "/")
    check(route, "GET / answers with the route list", status == 200 and len(body["routes"]) == 11, f"{len(body['routes'])} routes")
    status, body, _ = call("GET", "/api/health")
    check(route, "GET /api/health uses the isolated copy and the shop date", status == 200 and body["date_today"] == "2026-08-31"
          and body["database"].startswith("isolated copy"), json.dumps(body))
    status, body, _ = call("GET", "/api/tickets")
    tickets = body["tickets"]
    check(route, "GET /api/tickets lists the three tickets, open and unresolved", status == 200 and [t["id"] for t in tickets] == [101, 102, 103]
          and all(t["status"] == "open" and t["resolved"] is False for t in tickets), ", ".join(f"{t['id']} {t['status']}" for t in tickets))
    status, body, _ = call("GET", "/api/cash")
    check(route, "GET /api/cash returns the checking balance", status == 200 and body["balance"] == 3400.0, f"${body['balance']:,.2f} as of {body['as_of']}")
    status, body, _ = call("GET", "/api/approvals")
    check(route, "GET /api/approvals starts empty", status == 200 and body["approval_requests"] == [], "no pending requests")
    status, _, headers = call("OPTIONS", "/api/tickets", headers={"Origin": FRONTEND, "Access-Control-Request-Method": "GET"})
    allowed = headers.get("access-control-allow-origin") or headers.get("Access-Control-Allow-Origin")
    status_other, _, headers_other = call("OPTIONS", "/api/tickets", headers={"Origin": "http://example.com", "Access-Control-Request-Method": "GET"})
    other = headers_other.get("access-control-allow-origin") or headers_other.get("Access-Control-Allow-Origin")
    check(route, "CORS allows the local React frontend only", status == 200 and allowed == FRONTEND and other is None,
          f"{FRONTEND} allowed; http://example.com refused ({status_other})")

    section("R2  Invalid requests")
    status, body, _ = call("POST", "/api/tickets/999/run")
    check(route, "an unknown ticket id is refused", status == 404, detail(body))
    status, _, _ = call("POST", "/api/tickets/abc/run")
    check(route, "a ticket id that isn't a number is refused", status == 422, "422 from FastAPI's validation")
    status, body, _ = call("GET", "/api/runs/no-such-run")
    check(route, "an unknown run id is refused", status == 404, detail(body))
    status, body, _ = call("POST", "/api/approvals/999/approve", {"approver": PERSON})
    check(route, "approving an unknown request is refused", status == 404, detail(body))

    section("S1  A SIMULATED agent run in the background, polled through /api/events")
    main.MODEL_FOR = Script(ticket_101_plans(), delay=0.4).model_for
    main.MODEL_LABEL = "scripted (SIMULATED, no Claude)"
    status, started, _ = call("POST", "/api/tickets/101/run")
    run_id = started["run_id"]
    check(simulated, "POST /api/tickets/101/run starts a background run and returns its id", status == 202 and started["status"] == "running", run_id)
    status, body, _ = call("POST", "/api/tickets/101/run")
    check(simulated, "a second run on the same ticket is refused while one is running", status == 409 and body["detail"]["run_id"] == run_id,
          body["detail"]["message"])
    status, body, _ = call("POST", "/api/reset")
    check(simulated, "reset is refused during a run", status == 409 and run_id in body["detail"]["run_ids"], body["detail"]["message"])
    status, body, _ = call("GET", "/api/tickets")
    check(simulated, "GET /api/tickets shows the run in progress", next(t for t in body["tickets"] if t["id"] == 101)["active_run_id"] == run_id, "ticket 101")
    after, polls_with_news, seen = -1, 0, []
    while True:
        _, page, _ = call("GET", f"/api/events?run_id={run_id}&after={after}")
        if page["events"]:
            polls_with_news += 1
            seen += page["events"]
            after = page["next_after"]
        _, run, _ = call("GET", f"/api/runs/{run_id}")
        if run["status"] != "running":
            _, page, _ = call("GET", f"/api/events?run_id={run_id}&after={after}")
            seen += page["events"]
            break
        time.sleep(0.3)
    actions = [event["action"] for event in seen]
    check(simulated, "polling with after= returns each new event once, while the run goes on", polls_with_news >= 3 and len(seen) == len({e["index"] for e in seen})
          and actions[0] == "run_started" and actions[-1] == "run_finished", f"{len(seen)} events over {polls_with_news} polls")
    check(simulated, "events include agent messages and tool events", {"delegation_started", "tool", "delegation", "final_report"} <= set(actions),
          ", ".join(sorted(set(actions))))
    check(simulated, "the run finishes with the Boss's report", run["status"] == "finished" and run["report"]["summary"].startswith("Ticket 101"), run["report"]["summary"])
    status, body, _ = call("GET", "/api/tickets")
    ticket = next(t for t in body["tickets"] if t["id"] == 101)
    check(simulated, "the ticket shows its new status, not resolved", ticket["status"] == "waiting_on_approval" and ticket["resolved"] is False
          and ticket["active_run_id"] is None, ticket["status"])
    status, body, _ = call("GET", "/api/approvals")
    pending = body["approval_requests"]
    check(simulated, "the agents' payment request waits for a person", len(pending) == 1 and pending[0]["amount"] == 840.0 and pending[0]["status"] == "pending"
          and payment_count(db) == 0, f"request {pending[0]['id']}: ${pending[0]['amount']:,.2f}, nothing paid")

    section("A   Approve: the operator tool checks the request and makes the payment")
    approval_id = pending[0]["id"]
    status, body, _ = call("POST", f"/api/approvals/{approval_id}/approve", {"approver": "Accounting"})
    check(route, "an agent's name can't approve", status == 409, detail(body))
    status, _, _ = call("POST", f"/api/approvals/{approval_id}/approve", {"approver": ""})
    check(route, "an empty approver name is refused", status == 422, "422 from FastAPI's validation")
    setup_sql(db, "UPDATE cash_accounts SET balance = 500.0 WHERE name = 'checking'")  # SETUP: only $500 in checking
    status, body, _ = call("POST", f"/api/approvals/{approval_id}/approve", {"approver": PERSON})
    still = rows(db, "SELECT status FROM approval_requests WHERE id = ?", (approval_id,))[0]["status"]
    check(route, "insufficient funds: refused, nothing changes, the request stays pending", status == 409 and payment_count(db) == 0
          and checking(db) == 500.0 and still == "pending", detail(body))
    setup_sql(db, "UPDATE cash_accounts SET balance = 3400.0 WHERE name = 'checking'")  # SETUP: restore the cash
    status, paid, _ = call("POST", f"/api/approvals/{approval_id}/approve", {"approver": PERSON})
    invoice = rows(db, "SELECT status FROM invoices WHERE id = 501")[0]["status"]
    check(route, "a person's approval pays: cash, invoice and request change together", status == 200 and paid["status"] == "paid"
          and paid["new_balance"] == 2560.0 and invoice == "paid", f"payment {paid.get('payment_id')}, checking ${paid.get('new_balance', 0):,.2f}, invoice 501 {invoice}")
    status, body, _ = call("GET", "/api/cash")
    check(route, "GET /api/cash shows the new balance", body["balance"] == 2560.0, f"${body['balance']:,.2f}")
    status, body, _ = call("POST", f"/api/approvals/{approval_id}/approve", {"approver": PERSON})
    check(route, "approving the same request again is refused; still one payment", status == 409 and payment_count(db) == 1, detail(body))

    section("A2  Reset is refused while a payment is being made")
    rent = asyncio.run(agent_server_call(db, "request_payment_approval", {"kind": "rent", "ref_id": 1, "reason": "Rent for ticket 102.", "requested_by": "Accounting", "ticket_id": 102}))
    original_call = main.operator.call

    async def slow_payment(tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        if tool == "approve_and_pay":
            await asyncio.sleep(1.5)  # SETUP: slow this one payment down
        return await original_call(tool, args)

    main.operator.call = slow_payment
    outcome: dict[str, Any] = {}
    payer = threading.Thread(target=lambda: outcome.update(zip(("status", "body", "headers"), call("POST", f"/api/approvals/{rent['approval_id']}/approve", {"approver": PERSON}))))
    payer.start()
    time.sleep(0.5)
    status, body, _ = call("POST", "/api/reset")
    payer.join()
    main.operator.call = original_call
    check(route, "reset is refused during a payment", status == 409 and "payment" in detail(body), detail(body))
    check(route, "the payment itself completes", outcome.get("status") == 200 and outcome["body"]["status"] == "paid" and checking(db) == 160.0,
          f"rent paid; checking ${checking(db):,.2f}")

    section("S2  A failing SIMULATED run, and separate runs kept apart")
    main.MODEL_FOR = Script({"Boss": [[("__fail__", {"message": "simulated failure in the Boss's model"})]]}).model_for
    status, started, _ = call("POST", "/api/tickets/103/run")
    failed = wait_for_run(started["run_id"], 60)
    check(simulated, "a failing run is reported clearly, and the backend keeps working", failed["status"] == "stopped"
          and "simulated failure" in failed["stop_reason"] and call("GET", "/api/health")[0] == 200, failed["stop_reason"][:110])
    _, first_events, _ = call("GET", f"/api/events?run_id={run_id}&limit=500")
    _, second_events, _ = call("GET", f"/api/events?run_id={started['run_id']}&limit=500")
    check(simulated, "events of different runs stay apart", {e["run"] for e in first_events["events"]} == {run_id}
          and {e["run"] for e in second_events["events"]} == {started["run_id"]}, f"{len(first_events['events'])} and {len(second_events['events'])} events")

    section("R3  Reset: restore from the original and clear stale requests")
    setup_sql(db, "UPDATE cash_accounts SET balance = 5000.0 WHERE name = 'checking'")  # SETUP: room for one more request
    stale = asyncio.run(agent_server_call(db, "request_payment_approval", {"kind": "rent", "ref_id": 1, "reason": "A stale request.", "requested_by": "Accounting", "ticket_id": 102}))
    audit_before = audit_path.read_bytes()
    status, body, _ = call("POST", "/api/reset")
    check(route, "POST /api/reset restores the copy and clears stale pending requests", status == 200 and body["cleared_pending_requests"] == [stale["approval_id"]]
          and body["pending_after_reset"] == 0, json.dumps({k: body[k] for k in ("cleared_pending_requests", "pending_after_reset")}))
    _, tickets_now, _ = call("GET", "/api/tickets")
    _, cash_now, _ = call("GET", "/api/cash")
    _, approvals_now, _ = call("GET", "/api/approvals?status=all")
    check(route, "after the reset: tickets open, cash $3,400.00, no requests, no payments", all(t["status"] == "open" for t in tickets_now["tickets"])
          and cash_now["balance"] == 3400.0 and approvals_now["approval_requests"] == [] and payment_count(db) == 0
          and rows(db, "SELECT status FROM invoices WHERE id = 501")[0]["status"] == "open", "the copy matches the original again")
    audit_after = audit_path.read_bytes()
    trail = json.loads(audit_after)
    check(route, "the audit history is kept, and the reset is recorded", audit_after.startswith(audit_before.rstrip()[:-1].rstrip())
          and trail[-1]["action"] == "database_reset" and sum(r["action"] == "human_decision" for r in trail) >= 5,
          f"{len(trail)} records; earlier bytes unchanged")

    if live:
        section("L1  A LIVE run of ticket 102 with Claude, through the API")
        main.MODEL_FOR = None
        main.MODEL_LABEL = "claude-code:claude-opus-5-5"
        status, started, _ = call("POST", "/api/tickets/102/run")
        live_run = wait_for_run(started["run_id"], 900)
        _, events_live, _ = call("GET", f"/api/events?run_id={started['run_id']}&limit=500")
        agents = sorted({e["agent"] for e in events_live["events"] if e.get("depth", 0) >= 1})
        check("LIVE", "the run finishes in the background", live_run["status"] == "finished", live_run["stop_reason"])
        check("LIVE", "Claude's Boss delegates, and the agents use MCP tools", bool(agents)
              and any(e["action"] == "tool" for e in events_live["events"]), f"agents asked: {', '.join(agents)}")
        _, approvals_live, _ = call("GET", "/api/approvals")
        rent_requests = [r for r in approvals_live["approval_requests"] if r["kind"] == "rent"]
        check("LIVE", "the agents only asked for the rent payment; nothing is paid yet", len(rent_requests) == 1 and payment_count(db) == 0,
              json.dumps(rent_requests[:1]))
        if rent_requests:
            status, paid, _ = call("POST", f"/api/approvals/{rent_requests[0]['id']}/approve", {"approver": PERSON})
            check("LIVE", "a person's approval pays the rent", status == 200 and paid["new_balance"] == 1000.0
                  and rows(db, "SELECT next_due FROM leases")[0]["next_due"] == "2026-10-02", f"checking ${paid.get('new_balance', 0):,.2f}, lease 1 next due 2026-10-02")
            status, body, _ = call("POST", f"/api/approvals/{rent_requests[0]['id']}/approve", {"approver": PERSON})
            check("LIVE", "approving it again is refused", status == 409 and payment_count(db) == 1, detail(body))
        usage = live_run.get("usage", {})
        print(f"    usage: {usage.get('steps')} steps, {usage.get('tool_calls')} tool calls, {usage.get('total_tokens', 0):,} tokens")


def main_entry() -> int:
    parser = argparse.ArgumentParser(description="Check the backend's routes on an isolated copy of the original database.")
    parser.add_argument("--live", action="store_true", help="also run one live Claude run through the API (uses real Claude usage)")
    args = parser.parse_args()

    working_before, original_before = sha256(WORKING_DB), sha256(ORIGINAL_DB)
    real_audit = Path(__file__).resolve().parent.parent / "output" / "audit_trail.json"
    real_audit_before = sha256(real_audit)
    with tempfile.TemporaryDirectory(prefix="campus_api_checks_") as tmp:
        folder = Path(tmp)
        db = isolated_copy(folder, "api_copy.db")
        audit_path = folder / "api_audit_trail.json"
        os.environ["CAMPUS_DB_PATH"] = str(db)
        os.environ["CAMPUS_AUDIT_PATH"] = str(audit_path)
        import main  # after the environment points it at the isolated copy

        print(f"=== Backend checks at {BASE} on isolated copy {db.name} ===")
        server = start_backend(main)
        try:
            run_checks(main, db, audit_path, args.live)
        finally:
            server.should_exit = True
            time.sleep(1)
    print("\n=== Data safety ===")
    check("ROUTE", "working copy, original and output/audit_trail.json unchanged", sha256(WORKING_DB) == working_before
          and sha256(ORIGINAL_DB) == original_before and sha256(real_audit) == real_audit_before, "same SHA-256 as before")

    print("\n=== Summary ===")
    for kind in ("ROUTE", "SIMULATED", "LIVE"):
        mine = [r for r in RESULTS if r["kind"] == kind]
        if mine:
            print(f"{kind}: {sum(r['passed'] for r in mine)} of {len(mine)} checks passed")
    return 0 if all(r["passed"] for r in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main_entry())
