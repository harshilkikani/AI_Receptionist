"""P10 — /admin/analytics view tests."""
from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src import admin, usage


@pytest.fixture
def app_client(monkeypatch):
    from src import security
    security.reset_buckets()
    # Disable Twilio sig to simplify calls
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    import main
    importlib.reload(main)
    return TestClient(main.app)


def _seed_call_and_turn(sid: str, *, client_id="ace_hvac",
                        outcome="normal", intent="Scheduling",
                        hour_utc=10, emergency=False, duration=60,
                        month=None):
    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    # Build a UTC timestamp landing in the given hour of the current month
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, hour_utc, 30, 0,
                     tzinfo=timezone.utc)
    start_ts = int(start.timestamp())
    from src.usage import _connect, _init_schema, _db_lock
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        conn.execute("""
            INSERT OR REPLACE INTO calls
              (call_sid, client_id, from_number, to_number, start_ts, end_ts,
               duration_s, outcome, emergency, month)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (sid, client_id, "+14155550142", "+18449403274",
              start_ts, start_ts + duration, duration, outcome,
              1 if emergency else 0, month))
        conn.execute("""
            INSERT INTO turns
              (call_sid, client_id, ts, input_tokens, output_tokens,
               tts_chars, role, intent, month)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (sid, client_id, start_ts + 10, 50, 20, 40,
              "assistant", intent, month))
        conn.close()


def test_analytics_renders_empty(app_client):
    r = app_client.get("/admin/analytics")
    assert r.status_code == 200
    assert "Intent distribution" in r.text
    assert "Calls per hour" in r.text
    assert "Month-over-month" in r.text
    assert "Flagged clients" in r.text


def test_analytics_intent_distribution(app_client):
    _seed_call_and_turn("CA_intent_1", intent="Emergency", hour_utc=10)
    _seed_call_and_turn("CA_intent_2", intent="Emergency", hour_utc=11)
    _seed_call_and_turn("CA_intent_3", intent="Scheduling", hour_utc=12)
    r = app_client.get("/admin/analytics")
    assert r.status_code == 200
    assert "Emergency" in r.text
    assert "Scheduling" in r.text


def test_analytics_heatmap(app_client):
    # Seed calls at hour 14 and hour 22
    _seed_call_and_turn("CA_heat_1", hour_utc=14)
    _seed_call_and_turn("CA_heat_2", hour_utc=14)
    _seed_call_and_turn("CA_heat_3", hour_utc=22)
    r = app_client.get("/admin/analytics")
    assert "14:00" in r.text
    assert "22:00" in r.text


def test_analytics_flagged_high_spam_rate(app_client):
    # Seed 1 handled + 5 spam → 5/6 = 83% spam
    _seed_call_and_turn("CA_f1", outcome="normal")
    for i in range(5):
        _seed_call_and_turn(f"CA_fsp_{i}", outcome="spam_phrase")
    r = app_client.get("/admin/analytics")
    assert "ace_hvac" in r.text
    # The flagged section mentions "spam"
    assert "spam" in r.text.lower()


def test_analytics_mom_trend(app_client):
    # Seed calls this month only
    _seed_call_and_turn("CA_mom_1", hour_utc=9)
    _seed_call_and_turn("CA_mom_2", hour_utc=10)
    r = app_client.get("/admin/analytics")
    # MoM section shows ace_hvac with a call count
    assert "ace_hvac" in r.text


# ── pure helpers ───────────────────────────────────────────────────────

def test_previous_month_wrap():
    assert admin._previous_month("2026-01") == "2025-12"
    assert admin._previous_month("2026-07") == "2026-06"


def test_intent_counts_query(app_client):
    _seed_call_and_turn("CA_ic_1", intent="Scheduling")
    _seed_call_and_turn("CA_ic_2", intent="Scheduling")
    _seed_call_and_turn("CA_ic_3", intent="Emergency")
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    counts = admin._intent_counts(month)
    assert counts.get("Scheduling") == 2
    assert counts.get("Emergency") == 1


def test_calls_per_hour(app_client):
    _seed_call_and_turn("CA_cph_1", hour_utc=3)
    _seed_call_and_turn("CA_cph_2", hour_utc=3)
    _seed_call_and_turn("CA_cph_3", hour_utc=15)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    hours = admin._calls_per_hour(month)
    assert hours.get(3) == 2
    assert hours.get(15) == 1


def test_silence_rate(app_client):
    _seed_call_and_turn("CA_sr_1", outcome="normal")
    _seed_call_and_turn("CA_sr_2", outcome="silence_timeout")
    _seed_call_and_turn("CA_sr_3", outcome="silence_timeout")
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    rate = admin._silence_rate("ace_hvac", month)
    assert rate == pytest.approx(2/3, rel=1e-3)
