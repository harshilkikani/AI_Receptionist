"""Usage tracking — per-client cost + margin.

Uses SQLite (stdlib, no deps). Schema is intentionally narrow:

  calls       — one row per call, with aggregate minutes + outcome
  turns       — one row per LLM turn (input/output tokens + TTS chars)
  sms         — one row per SMS sent

All queries are scoped by (client_id, month). Aggregate via monthly_summary().

This module is the SOURCE OF TRUTH for cost and margin calculations.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
DB_PATH = _ROOT / "data" / "usage.db"
RATE_CARD_PATH = _ROOT / "config" / "rate_card.json"

_db_lock = threading.Lock()


def _ensure_parent_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_parent_dir()
    conn = sqlite3.connect(DB_PATH, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS calls (
            call_sid       TEXT PRIMARY KEY,
            client_id      TEXT NOT NULL,
            from_number    TEXT,
            to_number      TEXT,
            start_ts       INTEGER NOT NULL,   -- unix epoch seconds
            end_ts         INTEGER,
            duration_s     INTEGER,
            outcome        TEXT,              -- normal, duration_capped, spam_number, spam_phrase, silence_timeout, emergency_transfer
            emergency      INTEGER DEFAULT 0,
            month          TEXT NOT NULL       -- YYYY-MM for fast per-month queries
        );
        CREATE INDEX IF NOT EXISTS idx_calls_client_month ON calls(client_id, month);

        CREATE TABLE IF NOT EXISTS turns (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            call_sid       TEXT,
            client_id      TEXT NOT NULL,
            ts             INTEGER NOT NULL,
            input_tokens   INTEGER DEFAULT 0,
            output_tokens  INTEGER DEFAULT 0,
            tts_chars      INTEGER DEFAULT 0,
            role           TEXT,               -- user, assistant
            intent         TEXT,
            month          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_turns_client_month ON turns(client_id, month);
        CREATE INDEX IF NOT EXISTS idx_turns_sid ON turns(call_sid);

        CREATE TABLE IF NOT EXISTS sms (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            call_sid       TEXT,
            client_id      TEXT NOT NULL,
            ts             INTEGER NOT NULL,
            to_number      TEXT,
            segments       INTEGER DEFAULT 1,
            body_len       INTEGER DEFAULT 0,
            direction      TEXT DEFAULT 'outbound',
            month          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sms_client_month ON sms(client_id, month);
    """)


def _now_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _now_ts() -> int:
    return int(time.time())


# ── Rate card ──────────────────────────────────────────────────────────

_cached_rate_card: Optional[dict] = None


def _rate_card() -> dict:
    global _cached_rate_card
    if _cached_rate_card is None:
        try:
            _cached_rate_card = json.loads(RATE_CARD_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _cached_rate_card = {}
    return _cached_rate_card


def reload_rate_card():
    global _cached_rate_card
    _cached_rate_card = None


def _rate(key: str, default: float = 0.0) -> float:
    return float(_rate_card().get(key, default))


# ── Write path — called from voice/sms handlers ────────────────────────

def start_call(call_sid: str, client_id: str, from_number: str, to_number: str):
    if not call_sid:
        return
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        conn.execute("""
            INSERT OR REPLACE INTO calls
                (call_sid, client_id, from_number, to_number, start_ts, month)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (call_sid, client_id, from_number, to_number, _now_ts(), _now_month()))
        conn.close()


def end_call(call_sid: str, outcome: str = "normal", emergency: bool = False):
    if not call_sid:
        return
    now = _now_ts()
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        row = conn.execute("SELECT start_ts FROM calls WHERE call_sid = ?",
                           (call_sid,)).fetchone()
        duration = max(0, now - int(row["start_ts"])) if row else 0
        conn.execute("""
            UPDATE calls
               SET end_ts = ?, duration_s = ?, outcome = ?, emergency = ?
             WHERE call_sid = ?
        """, (now, duration, outcome, 1 if emergency else 0, call_sid))
        conn.close()


def log_turn(call_sid: str, client_id: str, role: str,
             input_tokens: int = 0, output_tokens: int = 0,
             tts_chars: int = 0, intent: Optional[str] = None):
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        conn.execute("""
            INSERT INTO turns
                (call_sid, client_id, ts, input_tokens, output_tokens,
                 tts_chars, role, intent, month)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (call_sid or "", client_id, _now_ts(), input_tokens, output_tokens,
              tts_chars, role, intent, _now_month()))
        conn.close()


def log_sms(call_sid: str, client_id: str, to_number: str, body: str,
            direction: str = "outbound"):
    segments = max(1, (len(body) + 159) // 160)
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        conn.execute("""
            INSERT INTO sms
                (call_sid, client_id, ts, to_number, segments,
                 body_len, direction, month)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (call_sid or "", client_id, _now_ts(), to_number, segments,
              len(body), direction, _now_month()))
        conn.close()


def sms_count_for_call(call_sid: str) -> int:
    if not call_sid:
        return 0
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sms WHERE call_sid = ? AND direction='outbound'",
            (call_sid,),
        ).fetchone()
        conn.close()
        return int(row["n"] or 0)


# ── Read path — dashboards + alerts ────────────────────────────────────

def monthly_summary(client_id: str, month: Optional[str] = None) -> dict:
    """Aggregate metrics + cost + margin for one client in one month."""
    month = month or _now_month()
    with _db_lock:
        conn = _connect()
        _init_schema(conn)

        calls_row = conn.execute("""
            SELECT
              COUNT(*)                                       AS total,
              SUM(CASE WHEN outcome IN ('spam_number','spam_phrase','silence_timeout') THEN 1 ELSE 0 END)
                                                             AS filtered,
              SUM(CASE WHEN duration_s IS NOT NULL THEN duration_s ELSE 0 END)
                                                             AS total_seconds,
              SUM(emergency)                                 AS emergencies
            FROM calls WHERE client_id = ? AND month = ?
        """, (client_id, month)).fetchone()

        turns_row = conn.execute("""
            SELECT
              COALESCE(SUM(input_tokens), 0)  AS in_tokens,
              COALESCE(SUM(output_tokens), 0) AS out_tokens,
              COALESCE(SUM(tts_chars), 0)     AS tts_chars
            FROM turns WHERE client_id = ? AND month = ?
        """, (client_id, month)).fetchone()

        sms_row = conn.execute("""
            SELECT COALESCE(SUM(segments), 0) AS segments
              FROM sms WHERE client_id = ? AND month = ?
        """, (client_id, month)).fetchone()

        conn.close()

    total_calls = int(calls_row["total"] or 0)
    filtered = int(calls_row["filtered"] or 0)
    handled = total_calls - filtered
    total_seconds = int(calls_row["total_seconds"] or 0)
    total_minutes = total_seconds / 60.0
    in_tok = int(turns_row["in_tokens"] or 0)
    out_tok = int(turns_row["out_tokens"] or 0)
    tts_chars = int(turns_row["tts_chars"] or 0)
    sms_segs = int(sms_row["segments"] or 0)
    emergencies = int(calls_row["emergencies"] or 0)

    # Cost calculation
    cost = (
        (in_tok / 1000.0) * _rate("llm_input_per_1k_tokens")
        + (out_tok / 1000.0) * _rate("llm_output_per_1k_tokens")
        + (tts_chars / 1000.0) * _rate("voice_synthesis_per_1k_chars")
        + total_minutes * _rate("stt_per_minute")
        + total_minutes * _rate("platform_voice_per_minute")
        + total_minutes * _rate("twilio_inbound_per_minute")
        + sms_segs * _rate("sms_per_segment")
        + _rate("twilio_number_monthly")
    )

    return {
        "client_id": client_id,
        "month": month,
        "total_calls": total_calls,
        "calls_handled": handled,
        "calls_filtered": filtered,
        "emergencies": emergencies,
        "total_minutes": round(total_minutes, 2),
        "llm_input_tokens": in_tok,
        "llm_output_tokens": out_tok,
        "tts_chars": tts_chars,
        "sms_segments": sms_segs,
        "platform_cost_usd": round(cost, 4),
    }


def margin_for(client: dict, month: Optional[str] = None) -> dict:
    """Combine usage cost with plan revenue to compute margin."""
    summary = monthly_summary(client["id"], month=month)
    revenue = float((client.get("plan") or {}).get("monthly_price") or 0)
    cost = summary["platform_cost_usd"]
    margin = revenue - cost
    margin_pct = (margin / revenue * 100) if revenue > 0 else 0.0
    return {
        **summary,
        "revenue_usd": revenue,
        "margin_usd": round(margin, 2),
        "margin_pct": round(margin_pct, 1),
    }


def recent_calls(client_id: Optional[str] = None, limit: int = 50) -> list:
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        if client_id:
            rows = conn.execute("""
                SELECT * FROM calls WHERE client_id = ?
                 ORDER BY start_ts DESC LIMIT ?
            """, (client_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM calls ORDER BY start_ts DESC LIMIT ?
            """, (limit,)).fetchall()
        conn.close()
    return [dict(r) for r in rows]
