"""The append-only audit trail of agent runs: output/audit_trail.json (Homework 5).

Every agent-loop step adds one record: the run, the ticket, the agent and its place in the delegation chain,
the action (a tool call, a delegation, the final report), the tool's input and result, any approval it
touched, any error, and the usage counted so far against the run's shared limits. Runs also record when
they start and finish. Each person's decision on a payment is recorded too, by backend/approve.py or by the
dashboard's approval routes in backend/main.py, which also records each database reset.

The file is one JSON array that only grows. A new record replaces the closing "]" with ",<record>]", so
earlier records are never rewritten and the file is valid JSON after every append. A lock (in this
process, and on the file where the system supports it) keeps writers apart. Strings that look like keys,
tokens or email addresses are blanked, and long values are shortened.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_ai import RunUsage

from models import RunLimits

try:
    import fcntl  # file locking on macOS and Linux
except ImportError:  # Windows: the in-process lock below still keeps this process's writes apart
    fcntl = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUDIT_FILE = PROJECT_ROOT / "output" / "audit_trail.json"
MAX_STRING = 800  # characters kept from one string value
MAX_ITEMS = 30  # items kept from one list

_write_lock = threading.Lock()
_SECRET = re.compile(r"\b(?:sk-ant-|sk-|ghp_|xox[bp]-)[A-Za-z0-9_\-]{8,}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def compact(value: Any, limit: int = MAX_STRING) -> Any:
    """value as plain JSON: models become dicts, long strings and lists are shortened, secrets and emails blanked."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): compact(item, limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        items = [compact(item, limit) for item in value[:MAX_ITEMS]]
        return items + ([f"... {len(value) - MAX_ITEMS} more"] if len(value) > MAX_ITEMS else [])
    if isinstance(value, str):
        text = _EMAIL.sub("[email]", _SECRET.sub("[secret]", value))
        return text if len(text) <= limit else text[: limit - 1] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return compact(str(value), limit)


def usage_counts(usage: RunUsage | None, limits: RunLimits) -> dict[str, int]:
    """What the run has used so far, and what is left under its shared limits."""
    usage = usage or RunUsage()
    return {
        "steps": usage.requests,
        "steps_left": max(limits.max_steps - usage.requests, 0),
        "tool_calls": usage.tool_calls,
        "tool_calls_left": max(limits.max_tool_calls - usage.tool_calls, 0),
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "tokens_left": max(limits.max_total_tokens - usage.total_tokens, 0),
    }


def _end_of_content(file: Any, end: int) -> int:
    """The position just after the last non-whitespace byte before `end` (0 if there is none)."""
    while end > 0:
        file.seek(end - 1)
        if not file.read(1).isspace():
            return end
        end -= 1
    return 0


def append(path: Path, record: dict[str, Any]) -> None:
    """Add one record to the JSON array in path without rewriting earlier records."""
    text = json.dumps(record, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock, open(os.open(path, os.O_RDWR | os.O_CREAT, 0o644), "r+b") as file:
        if fcntl:
            fcntl.flock(file, fcntl.LOCK_EX)
        try:
            end = _end_of_content(file, file.seek(0, os.SEEK_END))
            if end == 0:  # a new, empty file
                file.write(f"[\n{text}\n]\n".encode())
                return
            file.seek(0)
            starts_array = file.read(1) == b"["
            file.seek(end - 1)
            if not starts_array or file.read(1) != b"]":
                raise RuntimeError(f"{path} is not a JSON array, so the record was not appended.")
            last = _end_of_content(file, end - 1)  # just after the last record, or after "[" when there is none
            file.seek(last - 1)
            empty = file.read(1) == b"["
            file.seek(last)
            file.truncate()
            file.write(f"{'' if empty else ','}\n{text}\n]\n".encode())
        finally:
            if fcntl:
                fcntl.flock(file, fcntl.LOCK_UN)


class AuditTrail:
    """Writes records to one audit file (output/audit_trail.json unless a check passes another)."""

    def __init__(self, path: Path = AUDIT_FILE) -> None:
        self.path = path

    def record(self, **fields: Any) -> None:
        entry = {"time": datetime.now(UTC).isoformat(timespec="seconds")}
        entry.update({key: compact(value) for key, value in fields.items() if value is not None})
        append(self.path, entry)

    def read(self) -> list[dict[str, Any]]:
        """Every record, read under the same locks as appends, so a record being written is never half-read."""
        if not self.path.exists():
            return []
        with _write_lock, open(self.path, "rb") as file:
            if fcntl:
                fcntl.flock(file, fcntl.LOCK_SH)
            try:
                data = file.read()
            finally:
                if fcntl:
                    fcntl.flock(file, fcntl.LOCK_UN)
        return json.loads(data) if data.strip() else []
