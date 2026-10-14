"""Restore the working database from the original, before a full ticket-resolution run.

Run from the project folder:
    python backend/reset_db.py

Replaces everything in data/campus_customs_new.db (the working copy the backend and MCP server use) with a
fresh copy of data/campus_customs.db (the original, which is never changed), or creates the working copy if
it is missing. Run it while no ticket-resolution run is in progress.
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import closing

import db


def main() -> int:
    try:
        db.reset_working_copy()
    except db.DatabaseMissing as error:
        print(f"Not reset: {error}", file=sys.stderr)
        return 1
    except sqlite3.OperationalError as error:  # e.g. "database is locked" while an agent is writing
        print(f"Not reset: the working database is busy ({error}). Stop the run and try again.", file=sys.stderr)
        return 1

    with closing(db.connect()) as con:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
        today = db.shop_date(con)
    print(
        f"Restored {db.display_path(db.DB_PATH)} from {db.display_path(db.ORIGINAL_DB_PATH)}: "
        f"{tables} tables, integrity check {integrity}, shop date {today}."
    )
    return 0 if integrity == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
