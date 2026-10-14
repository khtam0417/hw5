"""How the backend reaches the Campus Customs MCP server (Homework 5).

The server (mcp_server/server.py) is started as a subprocess over stdio: the agent tools for the agents, or
the operator tools (--operator) for a person approving payments, in main.py and approve.py. It uses the working copy,
data/campus_customs_new.db, unless a check passes an isolated copy. Only the server's path and the database
path are passed to it, never Claude credentials. Nothing in the backend's agent code opens the database
itself: shop facts and changes all go through this server.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastmcp.client.transports import StdioTransport

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MCP_SERVER = PROJECT_ROOT / "mcp_server" / "server.py"
WORKING_DB_LABEL = "data/campus_customs_new.db"
SERVER_ENVIRONMENT = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")


def database_label(db_path: Path | None) -> str:
    """The database a run uses, for the audit trail, without the home folder."""
    if db_path is None:
        return WORKING_DB_LABEL
    try:
        return db_path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return f"isolated copy {db_path.name}"


def server_transport(db_path: Path | None = None, *, operator: bool = False) -> StdioTransport:
    """A stdio connection that starts the MCP server: the agent tools, or the operator tools for a person."""
    env = {name: os.environ[name] for name in SERVER_ENVIRONMENT if name in os.environ}
    if db_path is not None:
        env["CAMPUS_DB_PATH"] = str(db_path)
    args = [str(MCP_SERVER), *(["--operator"] if operator else [])]
    return StdioTransport(command=sys.executable, args=args, env=env, cwd=str(PROJECT_ROOT))
