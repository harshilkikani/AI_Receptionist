"""V5.4 — single-source-of-truth SQLite migrations.

Each version added columns lazily inside the module that needed them
(V3.4 src/call_summary.py for `summary`; V4.5 src/recordings.py for
recording_*). That works but means a fresh deploy might miss a column
until the right code path runs the ALTER TABLE.

This module consolidates every additive migration into a single
idempotent `run_all()` invoked from main.py's lifespan. Lazy migrations
in the original modules are KEPT (defense in depth) but become no-ops
after this runs.

Why additive-only:
  - SQLite ALTER TABLE supports ADD COLUMN trivially. RENAME/DROP need
    a full table rewrite.
  - We never destructively change a column once shipped. New columns =
    new versions.

To add a future migration:
  - Append a tuple to MIGRATIONS below.
  - The next startup picks it up; existing data is preserved.
"""
from __future__ import annotations

import logging
import sqlite3

log = logging.getLogger("migrations")


# (table, column, ddl) — ddl runs only when `column` is missing.
MIGRATIONS = [
    # V3.4 — per-call AI summary
    ("calls", "summary",
     "ALTER TABLE calls ADD COLUMN summary TEXT"),
    # V4.5 — Twilio recording metadata
    ("calls", "recording_sid",
     "ALTER TABLE calls ADD COLUMN recording_sid TEXT"),
    ("calls", "recording_url",
     "ALTER TABLE calls ADD COLUMN recording_url TEXT"),
    ("calls", "recording_duration_s",
     "ALTER TABLE calls ADD COLUMN recording_duration_s INTEGER"),
]


def _columns_for(conn, table: str) -> set:
    """Inspect SQLite schema for the given table's columns. Returns
    empty set if the table doesn't exist yet."""
    try:
        return {row["name"] for row in
                conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        return set()


def run_all() -> dict:
    """Run every additive migration idempotently. Returns a summary
    {applied: list[str], skipped: list[str], errors: list[str]} —
    callers can log it but failures don't raise; we'd rather start
    degraded than fail to boot."""
    from src.usage import _connect, _db_lock, _init_schema

    applied: list = []
    skipped: list = []
    errors: list = []

    with _db_lock:
        conn = _connect()
        # Ensure base tables exist before any ALTER
        try:
            _init_schema(conn)
        except sqlite3.OperationalError as e:
            errors.append(f"_init_schema: {e}")
            conn.close()
            return {"applied": applied, "skipped": skipped, "errors": errors}

        # Cache columns per table to avoid repeated PRAGMA calls
        col_cache: dict = {}
        for table, column, ddl in MIGRATIONS:
            cols = col_cache.get(table)
            if cols is None:
                cols = _columns_for(conn, table)
                col_cache[table] = cols
            if column in cols:
                skipped.append(f"{table}.{column}")
                continue
            try:
                conn.execute(ddl)
                applied.append(f"{table}.{column}")
                cols.add(column)
            except sqlite3.OperationalError as e:
                # Re-check — another writer might have added it between
                # our PRAGMA and ALTER (race condition under threading).
                fresh_cols = _columns_for(conn, table)
                if column in fresh_cols:
                    skipped.append(f"{table}.{column}")
                    col_cache[table] = fresh_cols
                else:
                    errors.append(f"{table}.{column}: {e}")
        conn.close()

    if applied:
        log.info("migrations applied: %s", ", ".join(applied))
    if errors:
        log.error("migrations errors: %s", ", ".join(errors))
    return {"applied": applied, "skipped": skipped, "errors": errors}
