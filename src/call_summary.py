"""V3.4 — Post-call AI summary.

After each call ends, we feed the transcript to Claude Haiku and get
back one short sentence describing the call. Stored in the `calls`
table's `summary` column. Shown on:
  - admin call log + detail page
  - client portal call log + detail page
  - owner daily digest (longest calls get summaries attached)

Skipped for:
  - calls under 30 seconds (too short to be meaningful)
  - spam/silence outcomes (summary would waste tokens)
  - calls without transcript (nothing to summarize)

Best-effort: failure to generate never affects the call record. Errors
are logged and the call row keeps summary=NULL.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Optional

from src import transcripts
from src.usage import _connect, _db_lock, _init_schema

log = logging.getLogger("call_summary")

MIN_DURATION_SECONDS = 30
SKIP_OUTCOMES = {
    "spam_number", "spam_phrase", "silence_timeout",
    "busy", "failed", "canceled", "no_answer",
}
MAX_TURNS_IN_PROMPT = 16

_SYSTEM_PROMPT = (
    "You summarize one phone call between a business receptionist and a caller "
    "in ONE sentence under 100 characters. Lead with what the caller wants. "
    "No pronouns for the receptionist. No marketing fluff. Examples:\n"
    '  "Scheduled pump-out for 42 Oak St, Tuesday AM."\n'
    '  "Wrong number — redirected."\n'
    '  "Emergency: sewage backup, transferred to owner."\n'
    '  "Pricing question about AC install — quoted ballpark, offered estimate."\n'
    "Do NOT start with \"The caller\" or \"A customer\" — just the gist."
)


def _ensure_summary_column():
    """Idempotent migration: add `summary` column to `calls` if missing."""
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        try:
            conn.execute("ALTER TABLE calls ADD COLUMN summary TEXT")
        except sqlite3.OperationalError:
            pass  # already exists
        conn.close()


def _store_summary(call_sid: str, summary: str):
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        # Ensure column exists even if an older DB was created before V3.4
        try:
            conn.execute("ALTER TABLE calls ADD COLUMN summary TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "UPDATE calls SET summary = ? WHERE call_sid = ?",
            (summary, call_sid),
        )
        conn.close()


def get_summary(call_sid: str) -> Optional[str]:
    """Return the stored summary for a call, or None if not generated."""
    if not call_sid:
        return None
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        try:
            row = conn.execute(
                "SELECT summary FROM calls WHERE call_sid = ?",
                (call_sid,),
            ).fetchone()
        except sqlite3.OperationalError:
            # Column doesn't exist yet — no summaries stored
            conn.close()
            return None
        conn.close()
    return row["summary"] if row and "summary" in row.keys() else None


def _anthropic_client():
    """Lazy-import so tests that never hit this path don't need the SDK."""
    import anthropic
    return anthropic.Anthropic()


def generate_summary(call_sid: str,
                     llm_client=None,
                     min_duration: int = MIN_DURATION_SECONDS) -> Optional[str]:
    """Generate + store a one-line summary for the call. Returns the
    summary string, or None if skipped/failed.

    `llm_client` is injectable so tests can pass a mock that returns
    canned text without hitting the API.
    """
    meta = transcripts.get_call_meta(call_sid)
    if meta is None:
        return None

    duration = int(meta.get("duration_s") or 0)
    if duration < min_duration:
        log.debug("summary skipped call_sid=%s reason=too_short", call_sid)
        return None

    outcome = meta.get("outcome") or ""
    if outcome in SKIP_OUTCOMES:
        log.debug("summary skipped call_sid=%s reason=outcome=%s",
                  call_sid, outcome)
        return None

    turns = transcripts.get_transcript(call_sid)
    if not turns:
        log.debug("summary skipped call_sid=%s reason=no_transcript", call_sid)
        return None

    # Build the user prompt — cap at MAX_TURNS_IN_PROMPT
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
        resp = llm_client.messages.create(
            model=os.environ.get("SUMMARY_MODEL", "claude-haiku-4-5"),
            max_tokens=60,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user",
                       "content": f"Summarize this call:\n\n{conv}"}],
        )
    except Exception as e:
        log.warning("summary generation failed for %s: %s",
                    call_sid, e)
        return None

    # Extract text — handle different SDK response shapes
    try:
        content = resp.content[0]
        summary = getattr(content, "text", None) or content.get("text", "")
    except (AttributeError, IndexError, KeyError):
        summary = str(resp)

    summary = (summary or "").strip().strip('"').strip()
    if not summary:
        return None
    # Enforce length cap — if the model over-shot, truncate cleanly
    if len(summary) > 140:
        summary = summary[:137].rstrip() + "..."

    _store_summary(call_sid, summary)
    log.info("summary generated call_sid=%s len=%d", call_sid, len(summary))
    return summary
