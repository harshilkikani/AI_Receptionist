"""Global kill switch must disable ALL enforcement across modules."""

import time
import pytest
from src import call_timer, spam_filter, sms_limiter, usage


def _set_elapsed(sid: str, seconds: float):
    call_timer._calls[sid]["start_ts"] = time.time() - seconds


def test_kill_switch_disables_call_timer(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_CALL_DURATION_CAP", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "false")
    call_timer.record_start("K1", "ace_hvac")
    _set_elapsed("K1", 1000)
    r = call_timer.check("K1", client_ace, "hi")
    assert r["enforcement_active"] is False


def test_kill_switch_disables_spam_number(monkeypatch, tmp_path):
    # Point spam_filter at a temp blocklist containing a number
    import json
    bl = tmp_path / "bl.json"
    bl.write_text(json.dumps({"numbers": ["+19995550123"],
                              "area_codes_high_risk": []}))
    monkeypatch.setattr(spam_filter, "_BLOCKLIST_PATH", bl)
    spam_filter.reload()

    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "false")
    r = spam_filter.check_number("+19995550123")
    assert r["reject"] is False


def test_kill_switch_disables_sms_cap(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_SMS_CAP", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "false")
    # Hit cap + try one more
    sid = "KS1"
    for i in range(5):
        d = sms_limiter.should_send(sid, client_ace, f"msg {i}")
        assert d["allow"] is True  # kill switch always wins
        usage.log_sms(sid, "ace_hvac", "+14155550142", d["body"])


def test_usage_tracking_always_on_even_with_kill_switch(monkeypatch):
    """Data collection doesn't stop just because enforcement is off."""
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "false")
    usage.start_call("KS_U1", "ace_hvac", "+14155550142", "+18449403274")
    usage.log_turn("KS_U1", "ace_hvac", "assistant",
                   input_tokens=100, output_tokens=20, tts_chars=30)
    summary = usage.monthly_summary("ace_hvac")
    assert summary["total_calls"] >= 1
    assert summary["llm_input_tokens"] >= 100
