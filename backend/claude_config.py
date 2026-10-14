"""How the agents reach Claude (Homework 5): the model, its effort level, and the sign-in from .env.

The agents call Claude through the Claude Agent SDK, which runs Claude Code, the same setup as Homework 4.
Claude Code signs in with CLAUDE_CODE_OAUTH_TOKEN (a Claude subscription token from `claude setup-token`)
or, if that isn't set, ANTHROPIC_API_KEY, both read from the project's .env file. PORTKEY_API_KEY is not used.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MODEL = "claude-opus-5-5"  # Claude Opus 5.5
EFFORT = "high"  # Opus 5.5 defaults to medium; stock, cash and approval decisions deserve more thought


def use_claude_login() -> bool:
    """Prepare this process so Claude Code signs in with the credentials in .env: CLAUDE_CODE_OAUTH_TOKEN
    or, if that isn't set, ANTHROPIC_API_KEY.

    When the backend is started from inside Claude Code (for example the desktop app), it inherits
    variables that tie it to that parent session and its login, and Claude Code would inherit them
    too. Drop those and any inherited API-key credentials, then use the ones from .env (an OAuth token
    already in the environment also counts). Returns whether a credential is available. Their values
    are never printed.
    """
    env_file = dotenv_values(PROJECT_ROOT / ".env")
    token = env_file.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    api_key = env_file.get("ANTHROPIC_API_KEY")  # only from .env, never inherited from a parent session
    for name in list(os.environ):
        parent_session_var = name.startswith("CLAUDE") and name != "CLAUDE_CODE_OAUTH_TOKEN"
        other_credential = name in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "USE_LOCAL_OAUTH", "USE_STAGING_OAUTH"}
        if parent_session_var or other_credential:
            del os.environ[name]
    if token:
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
    elif api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    return bool(token or api_key)
