"""Data types shared by the Campus Customs agents (Homework 5)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, get_args

from pydantic import BaseModel, Field
from pydantic_ai import UsageLimits

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from audit import AuditTrail

AgentName = Literal["Boss", "Inventory", "Accounting", "Facilities", "Customer Service"]
AGENT_NAMES: tuple[AgentName, ...] = get_args(AgentName)


class AgentReport(BaseModel):
    """What an agent hands back when it finishes: to the agent that asked it, or as the run's result."""

    summary: str = Field(description="What you found or did, in a few sentences, citing tool results.")
    facts: list[str] = Field(
        default_factory=list,
        description="Shop facts the summary relies on, each with its source, e.g. 'get_inventory: CC-TEE-WHITE size S qty 0'.",
    )
    actions_taken: list[str] = Field(
        default_factory=list,
        description="Changes made through the tools, with their ids, e.g. 'approval request 1 (pending)', 'draft 2'.",
    )
    needs_human: list[str] = Field(
        default_factory=list,
        description="Decisions or approvals only a person can make, e.g. 'approve approval request 1 ($840.00)'.",
    )


@dataclass(frozen=True)
class RunLimits:
    """Limits shared by every agent in one run, so delegation can't loop forever. A step is one Claude call;
    the step, tool-call and token counts add up across all agents in the run."""

    max_steps: int = 40
    max_tool_calls: int = 60
    max_total_tokens: int = 500_000
    max_delegation_depth: int = 2  # Boss (0) -> one agent (1) -> one more agent (2)

    def usage_limits(self) -> UsageLimits:
        return UsageLimits(request_limit=self.max_steps, tool_calls_limit=self.max_tool_calls, total_tokens_limit=self.max_total_tokens)

    def as_dict(self) -> dict[str, int]:
        return {
            "steps": self.max_steps,
            "tool_calls": self.max_tool_calls,
            "total_tokens": self.max_total_tokens,
            "delegation_depth": self.max_delegation_depth,
        }


@dataclass
class RunDeps:
    """Everything an agent needs during a run: who it is, where it sits in the delegation chain, the shared
    limits, the audit trail, and the other agents it can ask."""

    run_id: str
    ticket_id: int | None
    agent: AgentName
    limits: RunLimits
    audit: AuditTrail
    agents: Mapping[AgentName, Agent[RunDeps, AgentReport]]
    database: str
    depth: int = 0
    chain: tuple[AgentName, ...] = field(default_factory=tuple)

    def child(self, agent: AgentName) -> RunDeps:
        """The same run, one level deeper, for the agent being asked."""
        return replace(self, agent=agent, depth=self.depth + 1, chain=(*self.chain, agent))


class TicketRunResult(BaseModel):
    """The outcome of one run: how it ended, the Boss's report, and the usage counted against the limits."""

    run_id: str
    ticket_id: int | None
    database: str
    finished: bool
    stop_reason: str
    report: AgentReport | None = None
    usage: dict[str, Any]
