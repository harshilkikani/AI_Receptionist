"""P3 — emergency owner SMS push tests."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import owner_notify, sms_limiter, usage


def _fake_twilio(capture: list) -> SimpleNamespace:
    messages = SimpleNamespace(
        create=lambda to, from_, body: capture.append(
            {"to": to, "from": from_, "body": body}) or None
    )
    return SimpleNamespace(messages=messages)


def test_build_body_basic():
    body = owner_notify.build_body(
        caller_phone="+14155550142",
        summary="water heater just burst, water everywhere",
        address="18 Elm Court",
        client_name="Ace HVAC",
    )
    assert "+14155550142" in body
    assert "water heater" in body
    assert "18 Elm Court" in body


def test_build_body_capped_at_320():
    long = "something " * 100
    body = owner_notify.build_body(caller_phone="+14155550142", summary=long)
    assert len(body) <= 320


def test_resolve_prefers_owner_cell():
    c = {"owner_cell": "+15551234567", "escalation_phone": "+15559876543"}
    assert owner_notify._resolve_owner_number(c) == "+15551234567"


def test_resolve_falls_back_to_escalation():
    c = {"owner_cell": "", "escalation_phone": "+15559876543"}
    assert owner_notify._resolve_owner_number(c) == "+15559876543"


def test_resolve_returns_empty_when_nothing_set():
    assert owner_notify._resolve_owner_number({}) == ""


def test_notify_skips_when_no_owner_number(client_ace):
    # ace_hvac.yaml has empty owner_cell AND empty escalation_phone
    r = owner_notify.notify_emergency(
        client_ace, caller_phone="+14155550142", summary="burst pipe")
    assert r["sent"] is False
    assert r["reason"] == "no_owner_number"


def test_notify_sends_via_twilio(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_OWNER_EMERGENCY_SMS", "true")
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    capture = []
    r = owner_notify.notify_emergency(
        client_ace,
        caller_phone="+14155550142",
        summary="burst pipe!!",
        address="18 Elm Court",
        call_sid="CA_em_1",
        twilio_client=_fake_twilio(capture),
        twilio_from="+18449403274",
    )
    assert r["sent"] is True
    assert capture and capture[0]["to"] == "+15551234567"
    assert "burst pipe" in capture[0]["body"]
    # Logged in usage DB with direction='owner_alert'
    from src.usage import _connect, _init_schema, _db_lock
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        row = conn.execute(
            "SELECT direction FROM sms WHERE call_sid=? ORDER BY id DESC LIMIT 1",
            ("CA_em_1",),
        ).fetchone()
        conn.close()
    assert row["direction"] == "owner_alert"


def test_notify_does_not_count_against_caller_sms_cap(client_ace, monkeypatch):
    """Per-call outbound cap should not be eaten by the owner_alert send."""
    monkeypatch.setenv("ENFORCE_OWNER_EMERGENCY_SMS", "true")
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    capture = []
    owner_notify.notify_emergency(
        client_ace,
        caller_phone="+14155550142",
        summary="flooding!",
        call_sid="CA_cap_1",
        twilio_client=_fake_twilio(capture),
        twilio_from="+18449403274",
    )
    # sms_count_for_call only counts direction='outbound' → this is 0
    assert usage.sms_count_for_call("CA_cap_1") == 0


def test_notify_flag_off_shadow_mode(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_OWNER_EMERGENCY_SMS", "false")
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    capture = []
    r = owner_notify.notify_emergency(
        client_ace,
        caller_phone="+14155550142",
        summary="burst pipe!!",
        call_sid="CA_sh_1",
        twilio_client=_fake_twilio(capture),
        twilio_from="+18449403274",
    )
    assert r["sent"] is False
    assert r["reason"] == "flag_off"
    # Nothing hit Twilio
    assert capture == []
    # Shadow row logged for analytics
    from src.usage import _connect, _init_schema, _db_lock
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        row = conn.execute(
            "SELECT direction FROM sms WHERE call_sid=?", ("CA_sh_1",),
        ).fetchone()
        conn.close()
    assert row["direction"] == "owner_alert_shadow"


def test_kill_switch_blocks_send(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_OWNER_EMERGENCY_SMS", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "false")
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    capture = []
    r = owner_notify.notify_emergency(
        client_ace,
        caller_phone="+14155550142",
        summary="burst!",
        twilio_client=_fake_twilio(capture),
        twilio_from="+18449403274",
    )
    assert r["sent"] is False
    assert capture == []


def test_twilio_unavailable_returns_skip(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_OWNER_EMERGENCY_SMS", "true")
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    # Missing twilio_client → reason='twilio_unavailable'
    r = owner_notify.notify_emergency(
        client_ace, caller_phone="+14155550142",
        summary="burst!", twilio_client=None, twilio_from="",
    )
    assert r["sent"] is False
    assert r["reason"] == "twilio_unavailable"


def test_twilio_send_error_does_not_raise(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_OWNER_EMERGENCY_SMS", "true")
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    bad = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: (_ for _ in ()).throw(RuntimeError("twilio down"))))
    r = owner_notify.notify_emergency(
        client_ace, caller_phone="+14155550142",
        summary="burst!", twilio_client=bad, twilio_from="+18449403274",
    )
    assert r["sent"] is False
    assert r["reason"].startswith("send_error:")
