"""The five Campus Customs agents (Homework 5), built with PydanticAI.

- Boss coordinates a ticket, asks the other agents for help and makes the final decision.
- Inventory handles stock, vendors, lead times and restocking (draft shipment requests).
- Accounting checks cash, invoices, prices and margins, and asks a person to approve payments. No agent can pay:
  a person's approval on the dashboard makes the payment (the MCP server's operator tools).
- Facilities handles leases and rent.
- Customer Service drafts messages to customers (and vendors or the landlord) on the board.

PydanticAI runs everything: each agent's loop, its tool calls, validation of tool arguments and of the
final report (models.AgentReport), delegation, and the usage limits. Claude chooses each step through the
Homework 4 adapter in claude_bridge.py. Each agent's prompt is in prompts/.

Shop access goes only through the MCP server (mcp_server/server.py), started once per run as a subprocess:
nothing in this module opens the database. Each agent sees only the MCP tools of its role (AGENT_TOOLS),
and the tools that record who acted get the agent's name from the run, not from what the model wrote.

Delegation: every agent has ask_agent, which runs another agent on a request and returns its report. The
whole run shares one budget (models.RunLimits): steps (Claude calls), tool calls and tokens add up across
all agents, and delegation goes at most max_delegation_depth levels deep, so a run can't loop forever.
Every agent-loop step is appended to the audit trail (audit.py).
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastmcp import Client
from pydantic_ai import Agent, FunctionToolset, ModelRetry, RunContext, RunUsage, ToolOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.mcp import CallToolFunc, MCPToolset
from pydantic_ai.messages import RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model

from audit import AUDIT_FILE, AuditTrail, usage_counts
from claude_bridge import MODEL_NAME, ClaudeCallError, ClaudeUnavailable, claude_model
from mcp_client import database_label, server_transport
from models import AGENT_NAMES, AgentName, AgentReport, RunDeps, RunLimits, TicketRunResult

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_FILES: dict[AgentName, str] = {
    "Boss": "boss.md",
    "Inventory": "inventory.md",
    "Accounting": "accounting.md",
    "Facilities": "facilities.md",
    "Customer Service": "customer_service.md",
}
FINAL_TOOL = "final_report"
DELEGATION_TOOL = "ask_agent"

# The MCP tools each agent may call. Anything else it needs, it asks the responsible agent for.
SHARED_TOOLS = frozenset({"get_shop_date", "get_ticket"})
AGENT_TOOLS: dict[AgentName, frozenset[str]] = {
    "Boss": SHARED_TOOLS | {"list_tickets", "list_approval_requests", "list_board", "update_ticket"},
    "Inventory": SHARED_TOOLS | {"get_inventory", "list_vendors", "get_vendor", "get_invoice_details", "request_vendor_shipment"},
    "Accounting": SHARED_TOOLS
    | {
        "get_cash_balance",
        "get_invoice_details",
        "get_lease_details",
        "get_pricing",
        "evaluate_discount",
        "list_approval_requests",
        "list_payments",
        "request_payment_approval",
    },
    "Facilities": SHARED_TOOLS | {"get_lease_details", "list_approval_requests", "list_payments"},
    "Customer Service": SHARED_TOOLS | {"list_board", "save_draft"},
}
# Tools that record who acted: the run fills these in with the calling agent's name.
ACTOR_ARGUMENT = {
    "request_payment_approval": "requested_by",
    "request_vendor_shipment": "requested_by",
    "save_draft": "author",
    "update_ticket": "agent",
}


async def act_as_agent(ctx: RunContext[RunDeps], call_tool: CallToolFunc, name: str, args: dict[str, Any]) -> Any:
    """Run an MCP tool for the agent whose step this is. A tool outside the agent's role is refused, and the
    tools that record who acted get the agent's name from the run, whatever the model wrote."""
    agent = ctx.deps.agent
    if name not in AGENT_TOOLS[agent]:
        raise ModelRetry(f"{name} isn't one of {agent}'s tools. Ask the agent responsible with {DELEGATION_TOOL}.")
    if name in ACTOR_ARGUMENT:
        args = {**args, ACTOR_ARGUMENT[name]: agent}
    return await call_tool(name, args)


def mcp_toolset(db_path: Path | None = None) -> MCPToolset[RunDeps]:
    """The MCP server as a PydanticAI toolset: started as a subprocess when a run begins, on the working copy
    or on the isolated copy at db_path."""
    return MCPToolset(Client(server_transport(db_path)), process_tool_call=act_as_agent, max_retries=2)


# --- Delegation ---------------------------------------------------------------------------------------------

delegation = FunctionToolset[RunDeps]()


@delegation.tool
async def ask_agent(ctx: RunContext[RunDeps], agent: AgentName, request: str) -> dict[str, Any]:
    """Ask another Campus Customs agent to do part of the work, and wait for its report.

    Args:
        agent: Who to ask: "Boss", "Inventory", "Accounting", "Facilities" or "Customer Service" (not yourself).
        request: One clear job, with the ids and facts it needs (ticket, SKU and size, invoice, lease, approval request).
    """
    deps = ctx.deps
    if agent == deps.agent:
        return {"refused": "You can't ask yourself. Use your own tools, or ask a different agent."}
    if deps.depth >= deps.limits.max_delegation_depth:
        return {
            "refused": f"Delegation limit reached: {' -> '.join(deps.chain)} is already {deps.depth} levels deep "
            f"(at most {deps.limits.max_delegation_depth}). Finish with what you have and list what's missing in needs_human."
        }
    child = deps.child(agent)
    deps.audit.record(
        run=deps.run_id,
        ticket=deps.ticket_id,
        agent=deps.agent,
        depth=deps.depth,
        chain=" -> ".join(child.chain),
        action="delegation_started",
        tool=DELEGATION_TOOL,
        input={"agent": agent, "request": request},
        usage=usage_counts(ctx.usage, deps.limits),
    )
    report = await run_agent(child, f"{deps.agent} asks: {request}", ctx.usage)
    return report.model_dump()


# --- The agents ----------------------------------------------------------------------------------------------


def _role_prompt(name: AgentName) -> Callable[[], str]:
    path = PROMPTS_DIR / PROMPT_FILES[name]

    def role_prompt() -> str:
        """The agent's prompt, read on every run so edits apply without a restart."""
        return path.read_text(encoding="utf-8")

    return role_prompt


def run_context(ctx: RunContext[RunDeps]) -> str:
    """Where this agent sits in the run, and how much of the shared budget is left."""
    deps, usage, limits = ctx.deps, ctx.usage, ctx.deps.limits
    asked = f"{deps.chain[-2]} asked you for help" if len(deps.chain) > 1 else "you lead this run"
    ticket = f"ticket {deps.ticket_id}" if deps.ticket_id is not None else "no single ticket"
    return (
        "## This run\n"
        f"- You are the {deps.agent} agent, and {asked}. Run {deps.run_id}, {ticket}.\n"
        f"- Delegation chain: {' -> '.join(deps.chain)} (depth {deps.depth}; at most {limits.max_delegation_depth}).\n"
        f"- Shared budget for the whole run, all agents together: {usage.requests} of {limits.max_steps} steps, "
        f"{usage.tool_calls} of {limits.max_tool_calls} tool calls and {usage.total_tokens:,} of "
        f"{limits.max_total_tokens:,} tokens used so far. The run stops when any of them runs out."
    )


def build_agents(
    toolset: MCPToolset[RunDeps], model_for: Callable[[AgentName], Model] | None = None
) -> dict[AgentName, Agent[RunDeps, AgentReport]]:
    """The five agents, sharing one MCP toolset (each filtered to its role) and the delegation tool. Every agent
    uses Claude through the adapter unless model_for supplies another model (the simulated checks do)."""
    agents: dict[AgentName, Agent[RunDeps, AgentReport]] = {}
    for name in AGENT_NAMES:
        allowed = AGENT_TOOLS[name]
        agents[name] = Agent(
            model_for(name) if model_for else claude_model(),
            name=name,
            deps_type=RunDeps,
            output_type=ToolOutput(
                AgentReport, name=FINAL_TOOL, description="Finish: hand back your report. This ends your work on the request."
            ),
            toolsets=[toolset.filtered(lambda ctx, tool, allowed=allowed: tool.name in allowed), delegation],
            instructions=[_role_prompt(name), run_context],
            retries=2,
        )
    return agents


# --- Running agents, with an audit record for every step ----------------------------------------------------


def _approval(tool: str, args: dict[str, Any], result: Any) -> dict[str, Any] | None:
    """The payment approval a step touched, for the audit record."""
    if tool != "request_payment_approval" or not isinstance(result, dict):
        return None
    if result.get("ok"):
        return {key: result.get(key) for key in ("approval_id", "status", "payment_id", "amount") if key in result} or None
    return {"approval_id": args.get("approval_id"), "refused": result.get("refused")}


def _step_record(step: int, call: ToolCallPart, outcome: ToolReturnPart | RetryPromptPart | None, agent: AgentName) -> dict[str, Any]:
    args = call.args_as_dict()
    if call.tool_name in ACTOR_ARGUMENT:  # record the name actually sent (act_as_agent), not the model's guess
        args[ACTOR_ARGUMENT[call.tool_name]] = agent
    fields: dict[str, Any] = {
        "step": step,
        "action": "delegation" if call.tool_name == DELEGATION_TOOL else "tool",
        "tool": call.tool_name,
        "input": args,
    }
    if outcome is None:
        fields.update(outcome="error", error="The run stopped before this step finished.")
    elif isinstance(outcome, RetryPromptPart):
        fields.update(outcome="error", error=outcome.model_response())
    else:
        result = outcome.content
        refusal = result.get("refused") if isinstance(result, dict) else None
        fields.update(result=result, outcome="refused" if refusal else "ok")
        fields["approval"] = _approval(call.tool_name, args, result)
    return fields


async def run_agent(deps: RunDeps, prompt: str, usage: RunUsage) -> AgentReport:
    """Run one agent on a request, recording every step. `usage` is the run's shared counter: steps, tool calls
    and tokens of every agent in the run add up in it, and the run's limits are checked against the total."""
    agent = deps.agents[deps.agent]
    pending: dict[str, tuple[int, ToolCallPart]] = {}
    step = 0

    def record(**fields: Any) -> None:
        deps.audit.record(
            run=deps.run_id,
            ticket=deps.ticket_id,
            agent=deps.agent,
            depth=deps.depth,
            chain=" -> ".join(deps.chain),
            usage=usage_counts(usage, deps.limits),
            **fields,
        )

    try:
        async with agent.iter(prompt, deps=deps, usage=usage, usage_limits=deps.limits.usage_limits()) as run:
            async for node in run:
                if Agent.is_call_tools_node(node):
                    step += 1
                    for part in node.model_response.parts:
                        if isinstance(part, ToolCallPart):
                            pending[part.tool_call_id] = (step, part)
                elif Agent.is_model_request_node(node):
                    for part in node.request.parts:
                        if isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id in pending:
                            number, call = pending.pop(part.tool_call_id)
                            record(**_step_record(number, call, part, deps.agent))
                elif Agent.is_end_node(node):
                    for number, call in pending.values():
                        if call.tool_name == FINAL_TOOL:
                            record(step=number, action="final_report", tool=FINAL_TOOL, input=call.args_as_dict(), outcome="ok")
                    pending.clear()
            return run.result.output
    finally:
        for number, call in pending.values():  # steps cut short by an error or a limit
            record(**_step_record(number, call, None, deps.agent))


def new_run_id() -> str:
    """A run id that sorts by start time, e.g. 20261014-044902-4c4efa."""
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)


def _boss_request(ticket_id: int | None, note: str | None) -> str:
    request = (
        f"Handle ticket {ticket_id}: read it, involve the agents it needs, decide, record your decision on the "
        "ticket with update_ticket, and report."
        if ticket_id is not None
        else "Handle the operator's request below."
    )
    return request + (f"\n\nNote from the operator: {note}" if note else "")


async def run_ticket(
    ticket_id: int | None,
    note: str | None = None,
    *,
    db_path: Path | None = None,
    audit_path: Path = AUDIT_FILE,
    limits: RunLimits | None = None,
    model_for: Callable[[AgentName], Model] | None = None,
    model_label: str = MODEL_NAME,
    run_id: str | None = None,
    trigger: str = "command line",
    label: str | None = None,
) -> TicketRunResult:
    """One run: the Boss handles a ticket (or the operator's note), asking the other agents as needed.
    Uses the working copy unless db_path names an isolated copy, and appends every step to the audit file. A label
    (for example "Problem 9 full run") is recorded with the run's start and finish, to tell sessions apart."""
    limits = limits or RunLimits()
    run_id = run_id or new_run_id()
    audit = AuditTrail(audit_path)
    database = database_label(db_path)
    toolset = mcp_toolset(db_path)
    deps = RunDeps(
        run_id=run_id,
        ticket_id=ticket_id,
        agent="Boss",
        limits=limits,
        audit=audit,
        agents=build_agents(toolset, model_for),
        database=database,
        chain=("Boss",),
    )
    prompt = _boss_request(ticket_id, note)
    usage = RunUsage()
    audit.record(
        run=run_id,
        ticket=ticket_id,
        agent="Boss",
        depth=0,
        action="run_started",
        input={"request": prompt},
        database=database,
        model=model_label,
        trigger=trigger,
        label=label,
        limits=limits.as_dict(),
    )
    report: AgentReport | None = None
    try:
        async with toolset:
            report = await run_agent(deps, prompt, usage)
        finished, reason = True, "finished: the Boss handed in its final report"
    except UsageLimitExceeded as error:
        finished, reason = False, f"stopped: a shared limit was reached ({error})"
    except (ClaudeUnavailable, ClaudeCallError) as error:
        finished, reason = False, f"stopped: Claude could not answer ({error})"
    except UnexpectedModelBehavior as error:
        finished, reason = False, f"stopped: {error}"
    except Exception as error:  # an MCP server that won't start, a timeout, a bug: record it, don't hide it
        finished, reason = False, f"stopped: {type(error).__name__}: {error}"
    audit.record(
        run=run_id,
        ticket=ticket_id,
        agent="Boss",
        depth=0,
        action="run_finished",
        outcome="ok" if finished else "error",
        result=report,
        error=None if finished else reason,
        usage=usage_counts(usage, limits),
        label=label,
    )
    return TicketRunResult(
        run_id=run_id,
        ticket_id=ticket_id,
        database=database,
        finished=finished,
        stop_reason=reason,
        report=report,
        usage=usage_counts(usage, limits),
    )
