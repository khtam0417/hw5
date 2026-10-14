"""Check that the agents can reach Claude: one short call with the project's model, effort and .env sign-in.

Run from the project folder:
    .venv/bin/python backend/check_claude.py

It makes one real Claude call of a few tokens. It prints which model answered, never the credential.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import tempfile

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultError, ResultMessage, query

from claude_config import EFFORT, MODEL, use_claude_login

PROMPT = "Reply with exactly this sentence and nothing else: Campus Customs agents can reach Claude."


async def main() -> int:
    if not use_claude_login():
        print("No Claude sign-in: set CLAUDE_CODE_OAUTH_TOKEN (or ANTHROPIC_API_KEY) in .env.", file=sys.stderr)
        return 1

    options = ClaudeAgentOptions(
        model=MODEL,
        effort=EFFORT,
        tools=[],  # no file, shell or web tools
        setting_sources=[],  # ignore user and project Claude Code settings and instruction files
        max_turns=1,
        cwd=tempfile.gettempdir(),
        extra_args={"no-session-persistence": None},  # don't save this one-off session to disk
    )
    answered_by: str | None = None
    result: ResultMessage | None = None
    try:
        async with contextlib.aclosing(query(prompt=PROMPT, options=options)) as messages:
            async for message in messages:
                if isinstance(message, AssistantMessage):
                    answered_by = message.model
                elif isinstance(message, ResultMessage):
                    result = message
    except ResultError as error:  # Claude Code exited after reporting an error result
        print(f"Claude call failed (status {error.api_error_status}): {'; '.join(error.errors) or error.result}", file=sys.stderr)
        return 1

    if result is None or result.is_error:
        detail = "no result" if result is None else "; ".join(result.errors or []) or result.result or result.subtype
        print(f"Claude call failed: {detail}", file=sys.stderr)
        return 1
    print(f"Claude replied: {result.result}")
    print(f"Model: {answered_by} (requested {MODEL}, effort {EFFORT}); took {result.duration_ms / 1000:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
