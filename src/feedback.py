"""P11 — per-call feedback capture (the self-improvement loop).

Flow:
  1. When a call ends with outcome=normal + duration>30s + not emergency,
     `maybe_send_followup` sends ONE SMS: "How'd that go? Text YES if it
     worked, NO if not."
  2. The inbound SMS handler calls `classify` on each body; on YES/NO
     match, `record_response` stores the reply in the `feedback` table.
  3. When the response is NO, the caller's recent conversation (from
     memory.json) is dumped to `logs/negative_feedback.jsonl` for the
     operator to review and promote into `evals/cases.jsonl`.

Feature flag: `ENFORCE_FEEDBACK_SMS` (default off). Respects
`MARGIN_PROTECTION_ENABLED` kill.

DB: `feedback` table added lazily on first write (stdlib sqlite3; same
DB as src.usage).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src import sms_limiter, usage

log = logging.getLogger("feedback")

_ROOT = Path(__file__).parent.parent
_NEGATIVE_LOG = _ROOT / "logs" / "negative_feedback.jsonl"

FOLLOWUP_BODY_EN = "How'd that go? Text YES if it worked, NO if not."
MIN_DURATION_SECONDS = 30
RESPONSE_WINDOW_SECONDS = 48 * 3600  # 48h


# ── schema ─────────────────────────────────────────────────────────────

def _init_feedback_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            call_sid       TEXT,
            client_id      TEXT NOT NULL,
            caller_phone   TEXT NOT NULL,
            sent_ts        INTEGER NOT NULL,
            response       TEXT,       -- 'yes', 'no', NULL while pending
            response_ts    INTEGER,
            transcript     TEXT,       -- JSON of the conversation at send time
            month          TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_feedback_caller
            ON feedback(caller_phone, sent_ts)
    """)


# ── enforcement gate ───────────────────────────────────────────────────

def _enforcement_active() -> bool:
    global_on = os.environ.get("MARGIN_PROTECTION_ENABLED", "true").lower() != "false"
    feature_on = os.environ.get("ENFORCE_FEEDBACK_SMS", "false").lower() == "true"
    return global_on and feature_on


# ── send path ──────────────────────────────────────────────────────────

def maybe_send_followup(call_sid: str, client: dict, *,
                        caller_phone: str, outcome: str,
                        duration_s: int, emergency: bool,
                        twilio_client=None, twilio_from: Optional[str] = None,
                        conversation: Optional[list] = None) -> dict:
    """Decide whether to send the follow-up SMS, and send it if so.

    Returns `{sent, reason, body?}`. Never raises.
    """
    if outcome != "normal":
        return {"sent": False, "reason": "outcome_not_normal"}
    if emergency:
        return {"sent": False, "reason": "emergency_call"}
    if duration_s < MIN_DURATION_SECONDS:
        return {"sent": False, "reason": "too_short"}
    if not _enforcement_active():
        return {"sent": False, "reason": "flag_off"}
    if not caller_phone:
        return {"sent": False, "reason": "no_caller_phone"}

    twilio_from = twilio_from or os.environ.get("TWILIO_NUMBER") or ""
    if twilio_client is None or not twilio_from:
        return {"sent": False, "reason": "twilio_unavailable"}

    body = sms_limiter.cap_length(FOLLOWUP_BODY_EN)
    try:
        twilio_client.messages.create(to=caller_phone, from_=twilio_from, body=body)
    except Exception as e:
        log.error("feedback SMS send failed: %s", e)
        return {"sent": False, "reason": f"send_error:{type(e).__name__}"}

    # Log in usage DB as outbound (counts toward billable) and in the
    # feedback table as pending.
    usage.log_sms(call_sid, client.get("id", ""), caller_phone, body,
                  direction="outbound_feedback")
    _write_pending(call_sid, client.get("id", ""), caller_phone,
                   body, conversation)
    log.info("feedback_sms_sent call_sid=%s to=%s", call_sid, caller_phone)
    return {"sent": True, "reason": "ok", "body": body}


def _write_pending(call_sid: str, client_id: str, caller_phone: str,
                   body: str, conversation: Optional[list]):
    from src.usage import _connect, _db_lock, _init_schema, _now_month, _now_ts
    transcript = json.dumps(conversation or [], ensure_ascii=False)
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        _init_feedback_schema(conn)
        conn.execute("""
            INSERT INTO feedback
              (call_sid, client_id, caller_phone, sent_ts, transcript, month)
            VALUES (?,?,?,?,?,?)
        """, (call_sid, client_id, caller_phone, _now_ts(), transcript,
              _now_month()))
        conn.close()


# ── inbound matching ──────────────────────────────────────────────────

def classify(body: str) -> Optional[str]:
    """Return 'yes', 'no', or None. Lenient — accepts YES/Y/YUP/YEAH,
    NO/N/NOPE. Strips trailing punctuation from the first word."""
    if not body:
        return None
    b = body.strip().lower()
    yes_tokens = {"yes", "y", "yeah", "yup", "yep", "sure", "absolutely"}
    no_tokens = {"no", "n", "nope", "nah"}
    words = b.split()
    first = words[0].strip(".,!?;:") if words else ""
    if first in yes_tokens or b in yes_tokens:
        return "yes"
    if first in no_tokens or b in no_tokens:
        return "no"
    # Common multi-word "no" prefixes
    if b.startswith(("not really", "not at all")):
        return "no"
    return None


def record_response(caller_phone: str, body: str,
                    now_ts: Optional[int] = None) -> dict:
    """Match an inbound SMS to the most recent pending feedback row for
    this caller within the 48h window. Returns `{matched, response, call_sid, ...}`."""
    classification = classify(body)
    if classification is None:
        return {"matched": False, "reason": "unparseable"}

    now_ts = now_ts or int(time.time())
    cutoff = now_ts - RESPONSE_WINDOW_SECONDS

    from src.usage import _connect, _db_lock
    with _db_lock:
        conn = _connect()
        _init_feedback_schema(conn)
        row = conn.execute("""
            SELECT id, call_sid, client_id, transcript
              FROM feedback
             WHERE caller_phone = ?
               AND response IS NULL
               AND sent_ts >= ?
          ORDER BY sent_ts DESC
             LIMIT 1
        """, (caller_phone, cutoff)).fetchone()
        if row is None:
            conn.close()
            return {"matched": False, "reason": "no_pending_feedback"}
        conn.execute("""
            UPDATE feedback
               SET response = ?, response_ts = ?
             WHERE id = ?
        """, (classification, now_ts, row["id"]))
        conn.close()

    result = {
        "matched": True,
        "response": classification,
        "call_sid": row["call_sid"],
        "client_id": row["client_id"],
    }

    if classification == "no":
        _log_negative(row["call_sid"], row["client_id"], caller_phone,
                      body, row["transcript"])

    return result


def _log_negative(call_sid: str, client_id: str, caller_phone: str,
                  body: str, transcript_json: str):
    _NEGATIVE_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        transcript = json.loads(transcript_json or "[]")
    except json.JSONDecodeError:
        transcript = []
    entry = {
        "call_sid": call_sid,
        "client_id": client_id,
        "caller_phone": caller_phone,
        "feedback_body": body,
        "transcript": transcript,
        "ts": int(time.time()),
    }
    with _NEGATIVE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
