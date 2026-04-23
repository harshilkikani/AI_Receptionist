"""V3.6 — Booking capture.

When a caller schedules service (intent=Scheduling), the transcript
usually contains the name, address, callback number, and desired time.
After the call ends, we ask Claude Haiku to extract those into a
structured row in a `bookings` SQLite table. The admin + client portal
both surface this.

Pipeline:
  /voice/status  (on outcome='normal')
      → maybe_extract_from_call(call_sid)
          → filter by intent/outcome
          → LLM structured extraction
          → bookings table INSERT
          → returns the row

Skipped when:
  - no transcript
  - call was spam/silence/failed
  - LLM returns should_book=False (caller didn't actually commit)

All writes best-effort; a failed extraction never breaks the call
pipeline.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

from src import transcripts
from src.usage import _connect, _db_lock, _init_schema, _now_month

log = logging.getLogger("bookings")


MAX_TURNS_IN_PROMPT = 16


class BookingExtraction(BaseModel):
    """Structured output schema for the extraction call."""
    should_book: bool
    caller_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    when: Optional[str] = None
    service: Optional[str] = None
    notes: Optional[str] = None


_EXTRACT_SYSTEM = (
    "You extract booking details from a phone transcript between a "
    "receptionist and a caller for a home-services business. "
    "Return should_book=True only if the caller actually committed to "
    "having someone come out — not just priced out or asked about hours. "
    "Return null for any field the caller did not explicitly state. "
    "Keep 'when' as what the caller said ('Tuesday morning', 'ASAP', "
    "'next week'). Keep 'notes' under 120 characters."
)


def _init_schema_bookings(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id            TEXT PRIMARY KEY,
            client_id     TEXT NOT NULL,
            call_sid      TEXT,
            caller_phone  TEXT,
            caller_name   TEXT,
            address       TEXT,
            requested_when TEXT,
            service       TEXT,
            notes         TEXT,
            status        TEXT DEFAULT 'pending',
            created_ts    INTEGER NOT NULL,
            month         TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_bookings_client
            ON bookings(client_id, created_ts DESC)
    """)


def record_booking(*, client_id: str, caller_phone: str = "",
                   caller_name: Optional[str] = None,
                   address: Optional[str] = None,
                   requested_when: Optional[str] = None,
                   service: Optional[str] = None,
                   notes: Optional[str] = None,
                   call_sid: str = "") -> dict:
    """Insert a booking row. Returns the row as a dict."""
    booking_id = f"bk_{uuid.uuid4().hex[:12]}"
    now_ts = int(time.time())
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        _init_schema_bookings(conn)
        conn.execute("""
            INSERT INTO bookings
              (id, client_id, call_sid, caller_phone, caller_name,
               address, requested_when, service, notes,
               status, created_ts, month)
            VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?)
        """, (booking_id, client_id, call_sid, caller_phone, caller_name,
              address, requested_when, service, notes,
              now_ts, _now_month()))
        conn.close()
    log.info("booking recorded id=%s client=%s phone=%s",
             booking_id, client_id, caller_phone)
    return {
        "id": booking_id,
        "client_id": client_id,
        "call_sid": call_sid,
        "caller_phone": caller_phone,
        "caller_name": caller_name,
        "address": address,
        "requested_when": requested_when,
        "service": service,
        "notes": notes,
        "status": "pending",
        "created_ts": now_ts,
    }


def list_bookings(client_id: Optional[str] = None, limit: int = 50) -> list:
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        _init_schema_bookings(conn)
        if client_id:
            rows = conn.execute("""
                SELECT * FROM bookings WHERE client_id=?
                 ORDER BY created_ts DESC LIMIT ?
            """, (client_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM bookings ORDER BY created_ts DESC LIMIT ?
            """, (limit,)).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def get_booking(booking_id: str) -> Optional[dict]:
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        _init_schema_bookings(conn)
        row = conn.execute(
            "SELECT * FROM bookings WHERE id = ?", (booking_id,),
        ).fetchone()
        conn.close()
    return dict(row) if row else None


def _anthropic_client():
    import anthropic
    return anthropic.Anthropic()


def _any_scheduling_intent(call_sid: str) -> bool:
    """Quick check: did any turn in this call carry intent=Scheduling?"""
    from src.usage import _connect as _c, _init_schema as _i, _db_lock as _l
    with _l:
        conn = _c()
        _i(conn)
        row = conn.execute("""
            SELECT 1 FROM turns
             WHERE call_sid=? AND intent='Scheduling'
             LIMIT 1
        """, (call_sid,)).fetchone()
        conn.close()
    return row is not None


def maybe_extract_from_call(call_sid: str, *,
                            llm_client=None) -> Optional[dict]:
    """Run the extraction pipeline for one call. Returns the booking dict
    if recorded, else None.

    Guards:
      - transcript must exist
      - outcome must be 'normal'
      - at least one turn must have intent='Scheduling'
      - LLM extraction must return should_book=True
    """
    meta = transcripts.get_call_meta(call_sid)
    if meta is None:
        return None
    if (meta.get("outcome") or "") != "normal":
        return None
    if (meta.get("duration_s") or 0) < 20:
        return None
    if not _any_scheduling_intent(call_sid):
        return None

    turns = transcripts.get_transcript(call_sid)
    if not turns:
        return None

    lines = []
    for t in turns[:MAX_TURNS_IN_PROMPT]:
        role = "caller" if t["role"] != "assistant" else "receptionist"
        text = (t["text"] or "").replace("\n", " ").strip()
        if text:
            lines.append(f"{role}: {text}")
    if not lines:
        return None
    conv = "\n".join(lines)

    llm_client = llm_client or _anthropic_client()
    try:
        resp = llm_client.beta.messages.parse(
            model=os.environ.get("BOOKING_MODEL", "claude-haiku-4-5"),
            max_tokens=250,
            system=_EXTRACT_SYSTEM,
            messages=[{"role": "user",
                       "content": f"Transcript:\n\n{conv}"}],
            output_format=BookingExtraction,
        )
    except Exception as e:
        log.warning("booking extraction failed for %s: %s", call_sid, e)
        return None

    extracted = resp.parsed_output
    if extracted is None or not extracted.should_book:
        log.debug("booking skipped for %s: should_book=False", call_sid)
        return None

    from_phone = meta.get("from_number") or (extracted.phone or "")
    return record_booking(
        client_id=meta["client_id"],
        call_sid=call_sid,
        caller_phone=from_phone,
        caller_name=extracted.caller_name,
        address=extracted.address,
        requested_when=extracted.when,
        service=extracted.service,
        notes=extracted.notes,
    )


# ── ICS generation ────────────────────────────────────────────────────

def generate_ics(booking: dict, *, duration_hours: int = 1) -> str:
    """Produce a minimal RFC 5545 .ics calendar invite string. Uses
    'today' + the booking's created_ts as a placeholder DTSTART when no
    `requested_when` has been parsed into a real datetime — humans
    reading the invite can fix the time in their own calendar."""
    bk_id = booking.get("id") or f"bk_{int(time.time())}"
    now = datetime.now(timezone.utc)
    dtstamp = now.strftime("%Y%m%dT%H%M%SZ")
    # Default start = tomorrow 10:00 UTC; real scheduling is on the operator.
    tomorrow = now.replace(hour=10, minute=0, second=0, microsecond=0)
    dtstart = tomorrow.strftime("%Y%m%dT%H%M%SZ")
    dtend = tomorrow.replace(hour=tomorrow.hour + duration_hours).strftime(
        "%Y%m%dT%H%M%SZ")
    summary = (booking.get("service") or "Service appointment").replace(",", " ")
    descr_parts = []
    for key, label in [("caller_name", "Caller"),
                       ("caller_phone", "Phone"),
                       ("address", "Address"),
                       ("requested_when", "Requested"),
                       ("notes", "Notes")]:
        v = booking.get(key)
        if v:
            descr_parts.append(f"{label}: {v}")
    description = "\\n".join(descr_parts).replace(",", " ")
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//AI Receptionist//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{bk_id}@ai-receptionist\r\n"
        f"DTSTAMP:{dtstamp}\r\n"
        f"DTSTART:{dtstart}\r\n"
        f"DTEND:{dtend}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"DESCRIPTION:{description}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
