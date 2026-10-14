"""Claude as the model behind the PydanticAI agents (Homework 5): the same adapter as Homework 4.

PydanticAI is the agent framework. It runs each agent's loop, calls the tools (the MCP server's tools, and
ask_agent for delegation), validates tool arguments and the final report, and enforces the usage limits.
What it lacks is a model for Claude through a Claude subscription sign-in: its Anthropic model calls the
Anthropic API with an API key, while CLAUDE_CODE_OAUTH_TOKEN signs in Claude Code, not the API. So
claude_step() is a PydanticAI FunctionModel: each agent step becomes one Claude call through the Claude
Agent SDK, which runs Claude Code on the sign-in in .env, and Claude answers by choosing the agent's next
tool call. Claude Code's own tools, settings and MCP servers are switched off for these calls, so Claude
only sees the prompt, the tool list and the transcript it is given. The model and effort come from
claude_config.py, and each call's real token usage is reported to PydanticAI for its token limit.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import tempfile
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ResultError, ResultMessage, query
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from claude_config import EFFORT, MODEL, use_claude_login

MODEL_NAME = f"claude-code:{MODEL}"
CALL_TIMEOUT_SECONDS = 240  # one agent step at high effort

STEP_FORMAT = """

## How to respond
You are the model inside a PydanticAI agent. Each reply is one step: call exactly one of these tools, with \
arguments that match its schema. You see the tool's result before your next step. When you are done, call \
final_report.
{tool_list}
"""

# Error text from Claude Code that means the sign-in or the plan is the problem, not this one step.
LOGIN_PROBLEMS = ("not logged in", "failed to authenticate", "invalid api key", "authentication", "oauth token", "unauthorized")
USAGE_PROBLEMS = ("usage limit", "rate limit")


class ClaudeUnavailable(RuntimeError):
    """Claude can't be reached: no sign-in, a rejected sign-in, or a usage limit."""


class ClaudeCallError(RuntimeError):
    """One Claude call finished without a usable answer."""


HAS_CLAUDE_LOGIN = use_claude_login()


def _failure(status: int | None, detail: str) -> Exception:
    lowered = detail.lower()
    if status in (401, 403) or any(marker in lowered for marker in LOGIN_PROBLEMS):
        return ClaudeUnavailable(f"Claude sign-in failed: {detail[:300]}")
    if any(marker in lowered for marker in USAGE_PROBLEMS):
        return ClaudeUnavailable(f"Claude usage limit reached: {detail[:300]}")
    return ClaudeCallError(detail[:500])


def _usage(raw: dict[str, Any] | None) -> RequestUsage:
    """Claude Code's token counts for one call, as PydanticAI usage. input_tokens includes cached input."""
    raw = raw or {}
    cache_read = int(raw.get("cache_read_input_tokens") or 0)
    cache_write = int(raw.get("cache_creation_input_tokens") or 0)
    return RequestUsage(
        input_tokens=int(raw.get("input_tokens") or 0) + cache_read + cache_write,
        output_tokens=int(raw.get("output_tokens") or 0),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


async def ask_claude(transcript: str, system_prompt: str, schema: dict[str, Any]) -> tuple[dict[str, Any], RequestUsage]:
    """One Claude call through Claude Code: a transcript in, JSON matching `schema` and the token usage out."""
    stderr_tail: collections.deque[str] = collections.deque(maxlen=15)
    options = ClaudeAgentOptions(
        model=MODEL,
        effort=EFFORT,
        system_prompt=system_prompt,
        output_format={"type": "json_schema", "schema": schema},
        tools=[],  # no file, shell or web tools: the agent's tools run in PydanticAI, not in Claude Code
        setting_sources=[],  # ignore user and project Claude Code settings and instruction files
        strict_mcp_config=True,  # and every MCP server configured outside this call
        max_turns=4,
        cwd=tempfile.gettempdir(),
        extra_args={"no-session-persistence": None},  # don't save these one-off sessions to disk
        stderr=stderr_tail.append,
    )
    result: ResultMessage | None = None
    try:
        async with contextlib.aclosing(query(prompt=transcript, options=options)) as messages:
            async for message in messages:
                if isinstance(message, ResultMessage):
                    result = message
    except ResultError as exc:  # Claude Code exited after reporting an error result
        raise _failure(exc.api_error_status, "; ".join(exc.errors) or exc.result or str(exc)) from None
    if result is None:
        raise _failure(None, "Claude Code exited without a result: " + " | ".join(stderr_tail))
    if result.is_error or result.structured_output is None:
        detail = "; ".join(result.errors or []) or result.result or ""
        raise _failure(result.api_error_status, f"{result.subtype}: {detail}")
    return result.structured_output, _usage(result.usage)


def render_messages(messages: list[ModelMessage]) -> tuple[str, str]:
    """PydanticAI's message history as a system prompt plus a plain-text transcript for Claude."""
    system, lines = [], []
    for message in messages:
        for part in message.parts:
            if isinstance(part, SystemPromptPart):
                system.append(part.content)
            elif isinstance(part, UserPromptPart):
                lines.append(f"Request: {part.content}")
            elif isinstance(part, TextPart):
                lines.append(f"You wrote: {part.content}")
            elif isinstance(part, ToolCallPart):
                lines.append(f"You called {part.tool_name} with {part.args_as_json_str()}")
            elif isinstance(part, ToolReturnPart):
                lines.append(f"{part.tool_name} returned: {part.model_response_str()}")
            elif isinstance(part, RetryPromptPart):
                lines.append(f"That did not work; correct it: {part.model_response()}")
    return "\n\n".join(system), "\n\n".join(lines)


async def claude_step(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """One agent step, answered by Claude through Claude Code: the next tool to call, with its arguments."""
    if not HAS_CLAUDE_LOGIN:
        raise ClaudeUnavailable("No Claude sign-in: set CLAUDE_CODE_OAUTH_TOKEN (or ANTHROPIC_API_KEY) in .env.")
    tools = [*info.function_tools, *info.output_tools]
    choices = [
        {
            "type": "object",
            "properties": {"tool_name": {"enum": [tool.name]}, "arguments": tool.parameters_json_schema},
            "required": ["tool_name", "arguments"],
            "additionalProperties": False,
        }
        for tool in tools
    ]
    schema = {
        "type": "object",
        "properties": {"next_step": {"anyOf": choices}},
        "required": ["next_step"],
        "additionalProperties": False,
    }
    system, transcript = render_messages(messages)
    system = "\n\n".join(filter(None, [system, info.instructions])) + STEP_FORMAT.format(
        tool_list="\n".join(f"- {tool.name}: {tool.description or ''}" for tool in tools)
    )
    answer, usage = await asyncio.wait_for(ask_claude(transcript, system, schema), CALL_TIMEOUT_SECONDS)
    step = answer["next_step"]
    return ModelResponse(parts=[ToolCallPart(step["tool_name"], step["arguments"])], usage=usage, model_name=MODEL_NAME)


def claude_model() -> FunctionModel:
    """The PydanticAI model every agent uses: Claude through the adapter above."""
    return FunctionModel(claude_step, model_name=MODEL_NAME)
