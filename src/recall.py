"""V4.7 — Cross-call recall ("calling back about yesterday?").

When a caller rings, look up their prior calls in the last N days for
THIS tenant. If anything's there, build a short context line and inject
it into the LLM's system prompt as a "Recent calls" volatile block.

The prompt already knows how to use that — it'll naturally lead with
"hey, calling back about yesterday?" or "you called this morning — got
the pump-out booked yet?" without us scripting the greeting.

Cap: last 3 calls from this number for this tenant within max_days.

We exclude:
  - Spam/silence outcomes (those weren't real conversations)
  - The current in-flight call (matched by call_sid argument when
    available — phone-matching alone could include the just-started call)
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

from src.usage import _connect, _db_lock, _init_schema

log = logging.getLogger("recall")

EXCLUDED_OUTCOMES = {
    "spam_number", "spam_phrase", "silence_timeout",
    "busy", "failed", "canceled",
}


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _phone_matches(stored: str, target_digits: str) -> bool:
    return _normalize_phone(stored or "") == target_digits


def _humanize_when(ts: int, now_ts: Optional[int] = None) -> str:
    """'2 minutes ago', 'yesterday at 4 PM', '3 days ago'."""
    now_ts = now_ts or int(time.time())
    delta = now_ts - ts
    when_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    # Cross-platform hour formatting — Windows doesn't support %-I
    try:
        hour_str = when_dt.strftime("%-I %p")
    except (ValueError, NotImplementedError):
        hour_str = when_dt.strftime("%I %p").lstrip("0")
    if delta < 60:
        return "moments ago"
    if delta < 3600:
        m = delta // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if delta < 86400:
        h = delta // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    if delta < 86400 * 2:
        return f"yesterday around {hour_str}"
    days = delta // 86400
    return f"{days} days ago"


def prior_calls(client_id: str, from_phone: str, *,
                exclude_call_sid: str = "",
                max_days: int = 7,
                limit: int = 3,
                now_ts: Optional[int] = None) -> list:
    """Return up to `limit` prior calls from this number for this tenant
    within the last `max_days`. Excludes spam/silence and the in-flight
    call (by SID). Each result: {call_sid, start_ts, duration_s,
    outcome, emergency, summary, when_human}."""
    if not (client_id and from_phone):
        return []
    target_digits = _normalize_phone(from_phone)
    if not target_digits:
        return []
    now_ts = now_ts or int(time.time())
    cutoff = now_ts - max_days * 86400

    # Ensure the V3.4 summary column exists (idempotent migration).
    # Without this, fresh test DBs hit OperationalError and return empty.
    try:
        from src import call_summary
        call_summary._ensure_summary_column()
    except Exception:
        pass

    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        # First try with summary; if that column genuinely isn't there,
        # fall back to a simpler query.
        try:
            rows = conn.execute("""
                SELECT call_sid, from_number, start_ts, duration_s, outcome,
                       emergency, summary
                  FROM calls
                 WHERE client_id = ?
                   AND start_ts >= ?
              ORDER BY start_ts DESC
                 LIMIT 50
            """, (client_id, cutoff)).fetchall()
        except Exception:
            try:
                rows = conn.execute("""
                    SELECT call_sid, from_number, start_ts, duration_s, outcome,
                           emergency
                      FROM calls
                     WHERE client_id = ?
                       AND start_ts >= ?
                  ORDER BY start_ts DESC
                     LIMIT 50
                """, (client_id, cutoff)).fetchall()
            except Exception:
                conn.close()
                return []
        conn.close()

    out = []
    for r in rows:
        if exclude_call_sid and r["call_sid"] == exclude_call_sid:
            continue
        if r["outcome"] and r["outcome"] in EXCLUDED_OUTCOMES:
            continue
        if not _phone_matches(r["from_number"], target_digits):
            continue
        # Safely extract summary (column may be missing in old DBs)
        try:
            summary = r["summary"]
        except (IndexError, KeyError):
            summary = None
        out.append({
            "call_sid": r["call_sid"],
            "start_ts": int(r["start_ts"] or 0),
            "duration_s": int(r["duration_s"] or 0),
            "outcome": r["outcome"] or "",
            "emergency": bool(r["emergency"]),
            "summary": summary,
            "when_human": _humanize_when(int(r["start_ts"] or 0), now_ts=now_ts),
        })
        if len(out) >= limit:
            break
    return out


def build_recall_block(client_id: str, from_phone: str, *,
                       exclude_call_sid: str = "",
                       max_days: int = 7,
                       now_ts: Optional[int] = None) -> str:
    """Render a system-prompt-friendly recall block. Empty string when
    there's nothing recent to mention (so the prompt stays clean)."""
    rows = prior_calls(client_id, from_phone,
                       exclude_call_sid=exclude_call_sid,
                       max_days=max_days, now_ts=now_ts)
    if not rows:
        return ""
    lines = ["## Recent calls from this same number"]
    for r in rows:
        bullet = f"- {r['when_human']}"
        if r["emergency"]:
            bullet += " (emergency)"
        if r["outcome"]:
            bullet += f" · outcome: {r['outcome']}"
        if r["duration_s"]:
            bullet += f" · {r['duration_s']}s"
        if r["summary"]:
            bullet += f" — {r['summary']}"
        lines.append(bullet)
    lines.append(
        "If the caller is following up on one of these, lead with that "
        "instead of starting fresh ('hey, calling back about yesterday?'). "
        "Don't repeat the summary verbatim; reference it lightly."
    )
    return "\n".join(lines)
