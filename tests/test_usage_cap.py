"""V3.11 — hard usage cap tests."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import usage, usage_cap


# ── cap_for ────────────────────────────────────────────────────────────

def test_cap_for_no_plan():
    assert usage_cap.cap_for(None) == 0
    assert usage_cap.cap_for({}) == 0


def test_cap_for_returns_plan_field():
    c = {"plan": {"hard_cap_calls": 250}}
    assert usage_cap.cap_for(c) == 250


def test_cap_for_invalid_falls_back_to_zero():
    c = {"plan": {"hard_cap_calls": "not a number"}}
    assert usage_cap.cap_for(c) == 0


# ── is_capped ──────────────────────────────────────────────────────────

def test_is_capped_no_cap_configured():
    c = {"id": "ace_hvac", "plan": {}}
    r = usage_cap.is_capped(c)
    assert r["capped"] is False
    assert r["cap"] == 0


def test_is_capped_below_cap():
    c = {"id": "ace_hvac", "plan": {"hard_cap_calls": 100}}
    r = usage_cap.is_capped(c)
    assert r["capped"] is False
    assert r["cap"] == 100


def test_is_capped_at_or_above_cap(monkeypatch):
    # Seed the calls table with 3 calls + set cap to 2
    for i in range(3):
        sid = f"CA_cap_test_{i}"
        usage.start_call(sid, "ace_hvac_cap_test", "+1", "+1")
        usage.end_call(sid, outcome="normal")
    c = {"id": "ace_hvac_cap_test", "plan": {"hard_cap_calls": 2}}
    r = usage_cap.is_capped(c)
    assert r["current"] >= 3
    assert r["capped"] is True


def test_is_capped_shadow_mode(monkeypatch):
    monkeypatch.setenv("ENFORCE_USAGE_HARD_CAP", "false")
    for i in range(3):
        sid = f"CA_cap_shadow_{i}"
        usage.start_call(sid, "ace_hvac_shadow", "+1", "+1")
        usage.end_call(sid, outcome="normal")
    c = {"id": "ace_hvac_shadow", "plan": {"hard_cap_calls": 2}}
    r = usage_cap.is_capped(c)
    assert r["capped"] is False
    assert r["current"] >= 3  # still tracked for visibility


def test_kill_switch_disables(monkeypatch):
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "false")
    for i in range(3):
        sid = f"CA_cap_kill_{i}"
        usage.start_call(sid, "ace_hvac_kill", "+1", "+1")
        usage.end_call(sid, outcome="normal")
    c = {"id": "ace_hvac_kill", "plan": {"hard_cap_calls": 2}}
    r = usage_cap.is_capped(c)
    assert r["capped"] is False


# ── capped_message ────────────────────────────────────────────────────

def test_capped_message_contains_name():
    c = {"name": "Ace HVAC"}
    msg = usage_cap.capped_message(c)
    assert "Ace HVAC" in msg
    assert "capacity" in msg.lower()


def test_capped_message_safe_default():
    msg = usage_cap.capped_message({})
    assert "our office" in msg.lower()


# ── integration into /voice/incoming ──────────────────────────────────

@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


def test_voice_incoming_triggers_cap(app_client, monkeypatch):
    """When calls exceed plan.hard_cap_calls, incoming gets the capped
    message + hangup rather than the greeting menu."""
    from src import tenant
    tenant.reload()
    ace = tenant.load_client_by_id("ace_hvac")
    ace.setdefault("plan", {})["hard_cap_calls"] = 1

    # Seed one call to hit the cap
    usage.start_call("CA_cap_seed", "ace_hvac", "+14155550100", "+18449403274")
    usage.end_call("CA_cap_seed", outcome="normal")

    r = app_client.post(
        "/voice/incoming",
        data={"From": "+14155550100", "To": "+18449403274",
              "CallSid": "CA_cap_trigger"},
    )
    assert r.status_code == 200
    assert "capacity" in r.text.lower()
    # Call should be marked outcome='capped' in usage
    from src.usage import _connect, _db_lock, _init_schema
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        row = conn.execute(
            "SELECT outcome FROM calls WHERE call_sid=?",
            ("CA_cap_trigger",)).fetchone()
        conn.close()
    assert row["outcome"] == "capped"
