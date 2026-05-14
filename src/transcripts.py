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
    """Append one turn to the transcript store. No-op if call_sid or text
    is blank (whitespace included)."""
    if not (call_sid or "").strip() or not (text or "").strip():
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


def list_by_phone(client_id: str, phone: str, *,
                  limit: int = 200) -> list:
    """V9.1 — return every turn across every call_sid for one phone
    number, chronologically. Powers the per-conversation timeline view
    in the portal: calls + SMS exchanges unified per partner.

    Matching strategy: join transcripts → calls on (call_sid) where
    calls.from_number == phone, OR call_sid starts with 'SMS_{phone}'
    (the pseudo-SID used by /sms/incoming). Both sources are needed —
    voice calls live in `calls`; SMS exchanges live only in transcripts
    under the `SMS_{phone}` pseudo-SID.

    Returns list of dicts: {call_sid, ts, role, text, intent, channel}
    where channel is 'voice' (transcript turn from a real call) or
    'sms' (transcript turn from an SMS_* pseudo-call).
    """
    if not client_id or not phone:
        return []
    from memory import normalize_phone as _norm
    norm = _norm(phone)
    if not norm:
        return []
    # Match a wide net of stored representations. Twilio sends E.164 with
    # a leading country code (+1...); normalize_phone strips it for the
    # caller_id. Either form may end up in calls.from_number depending
    # on the path. We OR them all.
    variants = []
    seen = set()
    for v in (phone, norm, "+" + norm, "1" + norm, "+1" + norm):
        if v and v not in seen:
            seen.add(v)
            variants.append(v)
    placeholders = ",".join("?" for _ in variants)
    sms_sid = f"SMS_{norm}"

    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        _init_transcripts_schema(conn)
        rows = conn.execute(
            f"""
            SELECT t.call_sid, t.ts, t.role, t.text, t.intent,
                   CASE WHEN t.call_sid = ? THEN 'sms' ELSE 'voice' END AS channel
              FROM transcripts t
         LEFT JOIN calls c ON c.call_sid = t.call_sid
             WHERE t.client_id = ?
               AND (
                     t.call_sid = ?
                  OR (c.from_number IN ({placeholders}) AND c.client_id = ?)
                   )
          ORDER BY t.ts ASC, t.id ASC
             LIMIT ?
            """,
            (sms_sid, client_id, sms_sid,
             *variants, client_id,
             limit),
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
