"""Call transcript storage + retrieval (V4 feature).

Every turn of every call is captured here so the operator (admin) and
the client (portal) can replay the conversation. Invaluable for:
  - Client dispute resolution ("show me what the AI said")
  - Training data for the eval harness
  - Debugging wonky LLM behavior in the wild

Schema lives in the same SQLite DB as src.usage. Table init is lazy
on first write.

Public API:
    record_turn(call_sid, client_id, role, text, intent=None, ts=None)
    get_transcript(call_sid) -> list[dict]
    get_call_meta(call_sid)  -> dict | None
"""
from __future__ import annotations

import time
from typing import Optional

from src.usage import _connect, _db_lock, _init_schema, _now_month


def _init_transcripts_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transcripts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            call_sid   TEXT NOT NULL,
            client_id  TEXT NOT NULL,
            ts         INTEGER NOT NULL,
            role       TEXT NOT NULL,       -- 'user' | 'assistant' | 'system'
            text       TEXT NOT NULL,
            intent     TEXT,
            month      TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_transcripts_call
            ON transcripts(call_sid, ts)
    """)


def record_turn(call_sid: str, client_id: str, role: str, text: str,
                intent: Optional[str] = None,
                ts: Optional[int] = None) -> None:
    """Append one turn to the transcript store. No-op if call_sid is empty
    (web chat / SMS pseudo-sids use their own keys)."""
    if not call_sid or not text:
        return
    ts = ts or int(time.time())
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        _init_transcripts_schema(conn)
        conn.execute(
            """INSERT INTO transcripts
                 (call_sid, client_id, ts, role, text, intent, month)
               VALUES (?,?,?,?,?,?,?)""",
            (call_sid, client_id, ts, role, text, intent, _now_month()),
        )
        conn.close()


def get_transcript(call_sid: str) -> list:
    """Return turns for a call in chronological order."""
    if not call_sid:
        return []
    with _db_lock:
        conn = _connect()
        _init_transcripts_schema(conn)
        rows = conn.execute(
            """SELECT ts, role, text, intent FROM transcripts
                WHERE call_sid=? ORDER BY ts ASC, id ASC""",
            (call_sid,),
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def get_call_meta(call_sid: str) -> Optional[dict]:
    """Return the calls-table row for this SID, or None."""
    if not call_sid:
        return None
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        row = conn.execute(
            "SELECT * FROM calls WHERE call_sid = ?",
            (call_sid,),
        ).fetchone()
        conn.close()
    return dict(row) if row else None
