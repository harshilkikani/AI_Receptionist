"""Section D — SMS rate limiting + length capping."""

from src import sms_limiter, usage


def test_first_three_allowed(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_SMS_CAP", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "true")
    sid = "SMS_T1"
    for i in range(3):
        d = sms_limiter.should_send(sid, client_ace, f"msg {i}")
        assert d["allow"] is True
        usage.log_sms(sid, "ace_hvac", "+14155550142", d["body"])


def test_fourth_blocked(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_SMS_CAP", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "true")
    sid = "SMS_T2"
    for i in range(3):
        d = sms_limiter.should_send(sid, client_ace, f"msg {i}")
        usage.log_sms(sid, "ace_hvac", "+14155550142", d["body"])
    d = sms_limiter.should_send(sid, client_ace, "msg 4")
    assert d["allow"] is False
    assert d["reason"] == "sms_cap_reached"


def test_shadow_mode_always_allows(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_SMS_CAP", "false")
    sid = "SMS_T3"
    for i in range(5):
        d = sms_limiter.should_send(sid, client_ace, f"msg {i}")
        assert d["allow"] is True  # shadow: never blocks
        usage.log_sms(sid, "ace_hvac", "+14155550142", d["body"])


def test_length_cap_truncates_at_word_boundary():
    body = "Thanks for calling. " * 30  # way past 320
    truncated = sms_limiter.cap_length(body)
    assert len(truncated) <= 320
    assert truncated.endswith("…")


def test_length_cap_hard_cuts_long_word():
    body = "x" * 500
    truncated = sms_limiter.cap_length(body)
    assert len(truncated) <= 320


def test_length_cap_leaves_short_alone():
    body = "Short message."
    assert sms_limiter.cap_length(body) == body


def test_kill_switch_bypasses_cap(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_SMS_CAP", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "false")  # kill wins
    sid = "SMS_T4"
    for i in range(3):
        d = sms_limiter.should_send(sid, client_ace, f"msg {i}")
        usage.log_sms(sid, "ace_hvac", "+14155550142", d["body"])
    d = sms_limiter.should_send(sid, client_ace, "msg 4")
    assert d["allow"] is True  # kill switch overrides
