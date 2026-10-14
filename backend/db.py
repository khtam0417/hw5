"""SQLite access for the Campus Customs operations backend (Homework 5).

data/campus_customs.db is the original database from the course data pack. It is never changed: the file
is read-only on disk, and the code opens it read-only, only to copy it. Everything else uses the working
copy, data/campus_customs_new.db (DB_PATH):
- ensure_working_copy() creates the working copy from the original when it is missing, and never replaces
  one that already exists. The backend calls it when it starts. (The MCP server, mcp_server/server.py, only
  reads the working copy and stops if it is missing.)
- reset_working_copy() restores the working copy from the original. Only reset_db.py and the dashboard's reset
  route (main.py, refused during a run or payment) call it, so a reset always happens on purpose.
- connect() opens the working copy. If the file is missing it raises DatabaseMissing; it never creates an
  empty or replacement database.

The data pack's tables are desk, inventory, pricing, vendors, leases, cash_accounts, payments, invoices and
tickets. The shop's current date is desk.date_today (shop_date()), never the computer's clock.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ORIGINAL_DB_PATH = DATA_DIR / "campus_customs.db"  # from data.zip; never changed
BUSY_TIMEOUT_SECONDS = 10  # the backend and MCP server share the file, so wait this long for a lock


def _working_copy() -> Path:
    """data/campus_customs_new.db, or the isolated copy named by CAMPUS_DB_PATH (the checks use one). Never the original."""
    raw = os.environ.get("CAMPUS_DB_PATH")
    path = Path(raw).expanduser().resolve() if raw else DATA_DIR / "campus_customs_new.db"
    if path == ORIGINAL_DB_PATH.resolve():
        raise RuntimeError("CAMPUS_DB_PATH points at the original database, which is never changed. Use a copy.")
    return path


DB_PATH = _working_copy()  # the working copy the backend and MCP server use


class DatabaseMissing(RuntimeError):
    """A database file the project needs is not there."""


def display_path(path: Path) -> str:
    """The path relative to the project folder, for messages (no home folder or user name)."""
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return f"isolated copy {path.name}"


def _require_original() -> None:
    if not ORIGINAL_DB_PATH.is_file():
        raise DatabaseMissing(
            f"{display_path(ORIGINAL_DB_PATH)} is missing. Unzip the course's data.zip into the project folder "
            "so it exists; this project never creates replacement data."
        )


def _copy_original(target: Path) -> None:
    """Copy the original database into target with SQLite's backup API. The backup takes SQLite's locks,
    so the copy is complete and consistent even if another process has target open. Raises
    sqlite3.OperationalError if another connection keeps target locked for BUSY_TIMEOUT_SECONDS."""
    deadline = time.monotonic() + BUSY_TIMEOUT_SECONDS

    def stop_if_locked_too_long(status: int, _remaining: int, _total: int) -> None:
        # Python's backup() retries a locked target forever, so give up after the busy timeout instead.
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) and time.monotonic() > deadline:
            raise sqlite3.OperationalError(f"{display_path(target)} is locked by another connection")

    with (
        closing(sqlite3.connect(f"{ORIGINAL_DB_PATH.as_uri()}?mode=ro", uri=True)) as original,
        closing(sqlite3.connect(target, timeout=BUSY_TIMEOUT_SECONDS)) as copy,
    ):
        original.backup(copy, progress=stop_if_locked_too_long)


def ensure_working_copy() -> bool:
    """Create the working copy from the original if it does not exist yet. An existing working copy is
    never replaced. Returns whether a new copy was made."""
    if DB_PATH.exists():
        return False
    _require_original()
    # Build the copy under a temporary name, then link it into place. os.link() refuses to replace a file,
    # so a working copy that another process made in the meantime is kept as it is.
    temp_path = DB_PATH.with_name(f".{DB_PATH.name}.{os.getpid()}.tmp")
    try:
        _copy_original(temp_path)
        os.link(temp_path, DB_PATH)
    except FileExistsError:
        return False
    finally:
        temp_path.unlink(missing_ok=True)
    return True


def reset_working_copy() -> None:
    """Replace everything in the working copy with a fresh copy of the original, or create the working copy
    if it is missing. Only reset_db.py calls this."""
    _require_original()
    if not ensure_working_copy():
        _copy_original(DB_PATH)


def connect() -> sqlite3.Connection:
    """Open the working copy for reading and writing, with rows as sqlite3.Row and foreign keys enforced."""
    if not DB_PATH.is_file():
        raise DatabaseMissing(
            f"{display_path(DB_PATH)} is missing. Run `python backend/reset_db.py` to create it from the original."
        )
    con = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=rw", uri=True, timeout=BUSY_TIMEOUT_SECONDS)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def shop_date(con: sqlite3.Connection) -> str:
    """The shop's current date, desk.date_today (YYYY-MM-DD). Use it instead of the computer's clock."""
    row = con.execute("SELECT date_today FROM desk").fetchone()
    if row is None:
        raise RuntimeError("The desk table has no row, so the shop's current date is unknown.")
    return row["date_today"]
