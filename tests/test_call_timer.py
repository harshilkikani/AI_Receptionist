"""Section A — call duration hard cap + grace + emergency extension."""

import time
import pytest
from src import call_timer


def _set_elapsed(sid: str, seconds: float):
    """Rewind the call's start_ts so `elapsed` is `seconds`."""
    call_timer._calls[sid]["start_ts"] = time.time() - seconds


def test_normal_under_threshold(client_ace):
    call_timer.record_start("t1", "ace_hvac")
    r = call_timer.check("t1", client_ace, "hi")
    assert r["action"] == "normal"


def test_soft_wrapup_at_180(client_ace):
    call_timer.record_start("t2", "ace_hvac")
    _set_elapsed("t2", 185)
    r = call_timer.check("t2", client_ace, "hi")
    assert r["action"] == "soft_wrapup"
    assert r["wrap_up_mode"] == "soft"


def test_hard_wrapup_at_225(client_ace):
    call_timer.record_start("t3", "ace_hvac")
    _set_elapsed("t3", 230)
    r = call_timer.check("t3", client_ace, "hi")
    assert r["action"] == "hard_wrapup"
    assert r["wrap_up_mode"] == "hard"


def test_force_end_at_240_with_enforcement(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_CALL_DURATION_CAP", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "true")
    call_timer.record_start("t4", "ace_hvac")
    _set_elapsed("t4", 250)
    r = call_timer.check("t4", client_ace, "hi")
    assert r["action"] == "force_end"
    assert r["enforcement_active"] is True


def test_shadow_mode_does_not_force_end(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_CALL_DURATION_CAP", "false")
    call_timer.record_start("t5", "ace_hvac")
    _set_elapsed("t5", 250)
    r = call_timer.check("t5", client_ace, "hi")
    # Past cap but enforcement off → escalates to hard_wrapup (logs only)
    assert r["action"] == "hard_wrapup"
    assert r["enforcement_active"] is False


def test_kill_switch_bypasses_enforcement(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_CALL_DURATION_CAP", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "false")  # kill
    call_timer.record_start("t6", "ace_hvac")
    _set_elapsed("t6", 250)
    r = call_timer.check("t6", client_ace, "hi")
    assert r["enforcement_active"] is False


def test_emergency_extends_to_360(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_CALL_DURATION_CAP", "true")
    call_timer.record_start("t7", "ace_hvac")
    call_timer.mark_emergency("t7")
    _set_elapsed("t7", 250)
    r = call_timer.check("t7", client_ace, "hi")
    # Still within 360 emergency cap → normal
    assert r["action"] == "normal"
    assert r["cap"] == 360


def test_emergency_hits_force_end_past_360(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_CALL_DURATION_CAP", "true")
    call_timer.record_start("t8", "ace_hvac")
    call_timer.mark_emergency("t8")
    _set_elapsed("t8", 365)
    r = call_timer.check("t8", client_ace, "hi")
    assert r["action"] == "force_end"


def test_grace_period_extended_for_critical_info(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_CALL_DURATION_CAP", "true")
    call_timer.record_start("t9", "ace_hvac")
    _set_elapsed("t9", 245)
    r = call_timer.check("t9", client_ace, "my address is 123 main street")
    # Still giving critical info → grace activates, don't force-end
    assert r["action"] == "soft_wrapup"
    assert r["note"] == "grace_period_extended"


def test_grace_not_extended_for_trivial_speech(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_CALL_DURATION_CAP", "true")
    call_timer.record_start("t10", "ace_hvac")
    _set_elapsed("t10", 245)
    r = call_timer.check("t10", client_ace, "uh okay yeah")
    assert r["action"] == "force_end"


def test_record_end_clears_state():
    call_timer.record_start("t11", "ace_hvac")
    assert "t11" in call_timer.snapshot()
    call_timer.record_end("t11")
    assert "t11" not in call_timer.snapshot()
