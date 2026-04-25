"""V4.5 — Twilio call recording bookkeeping + admin playback.

Twilio records the call when we enable it via the REST API after the
call connects. When recording finishes, Twilio POSTs to /voice/recording
with RecordingSid + RecordingUrl + RecordingDuration. We store those on
the existing `calls` row via two new columns (lazily added).

The admin /admin/call/{sid} page renders an HTML5 <audio> player
proxied through /admin/recording/{recording_sid}.mp3 so the operator
doesn't have to go to the Twilio console.

Per-tenant flag `record_calls: true|false` (default false — opt-in).
When true:
  - Caller hears "this call may be recorded for quality" at greeting
    (handled in main.py)
  - REST API call to twilio_client.calls(sid).update(record=True, ...)
    fires after start_call so Twilio captures the full conversation.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Optional

from src.usage import _connect, _db_lock, _init_schema

log = logging.getLogger("recordings")


def is_enabled(client: Optional[dict]) -> bool:
    """Per-tenant toggle. Default OFF — recordings touch privacy + storage,
    so operators opt in explicitly."""
    if client is None:
        return False
    val = client.get("record_calls")
    if val is None:
        return False
    return str(val).strip().lower() in ("true", "1", "yes")


def _ensure_columns():
    """Lazy migration: add recording_url + recording_sid + recording_duration_s
    columns to `calls` if they don't exist yet."""
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        for col, ddl in [
            ("recording_sid", "ALTER TABLE calls ADD COLUMN recording_sid TEXT"),
            ("recording_url", "ALTER TABLE calls ADD COLUMN recording_url TEXT"),
            ("recording_duration_s",
             "ALTER TABLE calls ADD COLUMN recording_duration_s INTEGER"),
        ]:
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # already exists
        conn.close()


def store_recording(call_sid: str, recording_sid: str,
                    recording_url: str, duration_s: Optional[int] = None):
    """Persist a recording metadata row keyed by call_sid."""
    if not call_sid or not recording_sid:
        return
    _ensure_columns()
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        conn.execute(
            "UPDATE calls SET recording_sid=?, recording_url=?, "
            "recording_duration_s=? WHERE call_sid=?",
            (recording_sid, recording_url, duration_s, call_sid),
        )
        conn.close()
    log.info("recording stored call_sid=%s sid=%s duration=%s",
             call_sid, recording_sid, duration_s)


def get_recording(call_sid: str) -> Optional[dict]:
    """Return the recording metadata for this call, or None."""
    if not call_sid:
        return None
    _ensure_columns()
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        row = conn.execute(
            "SELECT recording_sid, recording_url, recording_duration_s "
            "FROM calls WHERE call_sid=?",
            (call_sid,),
        ).fetchone()
        conn.close()
    if not row or not row["recording_sid"]:
        return None
    return {
        "recording_sid": row["recording_sid"],
        "recording_url": row["recording_url"],
        "duration_s": row["recording_duration_s"],
    }


def start_recording_via_rest(call_sid: str, twilio_client,
                             callback_url: str) -> bool:
    """Enable Twilio call recording via the REST API. Returns True on
    success, False on any failure. Best-effort — recording isn't worth
    blocking the call over."""
    if not call_sid or not twilio_client:
        return False
    try:
        twilio_client.calls(call_sid).update(
            record=True,
            recording_status_callback=callback_url,
            recording_status_callback_event=["completed"],
            recording_channels="dual",
        )
        return True
    except Exception as e:
        log.error("start_recording_via_rest failed for %s: %s", call_sid, e)
        return False


def disclosure_text() -> str:
    """The recording-disclosure phrase added to the greeting when
    record_calls is on. Legal in 2-party-consent states + good-practice
    elsewhere."""
    return "This call may be recorded for quality. "


# ── /admin proxy for the recording audio ────────────────────────────

import urllib.request
import base64

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, Response

from src.admin_auth import check_admin_auth as _check_admin_auth

router = APIRouter(prefix="/admin", tags=["recordings"])


def _twilio_creds() -> tuple:
    return (os.environ.get("TWILIO_ACCOUNT_SID", ""),
            os.environ.get("TWILIO_AUTH_TOKEN", ""))


@router.get("/recording/{call_sid}.mp3")
def proxy_recording(call_sid: str, user=Depends(_check_admin_auth)):
    """Stream the Twilio recording for the given call_sid through our
    server. Authenticates server-side with the Twilio Basic creds so
    the operator's browser never sees the auth token.

    V5.2 — admin Basic-auth dependency added. Previously this route
    was open even when ADMIN_USER/ADMIN_PASS were set; every other
    /admin/* route 401'd correctly but this proxy slipped through.

    V5.2 — also reject path-traversal attempts in the call_sid.
    """
    # Defense in depth: call_sid should always look like Twilio's
    # CA<32hex> shape. Reject anything with path separators or non-
    # safe characters before we hit the DB.
    if "/" in call_sid or ".." in call_sid or "\\" in call_sid:
        raise HTTPException(400, "invalid call_sid")
    rec = get_recording(call_sid)
    if not rec or not rec.get("recording_url"):
        raise HTTPException(404, "no recording for that call")
    sid, token = _twilio_creds()
    if not (sid and token):
        raise HTTPException(503, "twilio credentials not configured")

    # Twilio Recording URL doesn't include extension; append .mp3
    src_url = rec["recording_url"]
    if not src_url.endswith(".mp3"):
        src_url = src_url + ".mp3"

    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req = urllib.request.Request(src_url, headers={"Authorization": f"Basic {auth}"})
    try:
        upstream = urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        log.error("recording proxy fetch failed: %s", e)
        raise HTTPException(502, "recording fetch failed")

    return StreamingResponse(
        upstream, media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )
