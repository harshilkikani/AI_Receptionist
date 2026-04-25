"""V5.1 — shared-state leak audit + bounded-state guarantees.

These tests verify that every in-memory dict the app keeps grows in a
bounded way, AND that record_end-style cleanup actually fires from
every terminal call path.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import call_timer, scheduler, security, sentiment_tracker, signup


# ── Sentiment tracker ────────────────────────────────────────────────

def test_sentiment_tracker_record_end_clears_state():
    sentiment_tracker.reset_state()
    sentiment_tracker.record("CA_x", "frustrated")
    assert "CA_x" in sentiment_tracker.snapshot()
    sentiment_tracker.record_end("CA_x")
    assert "CA_x" not in sentiment_tracker.snapshot()


def test_sentiment_tracker_capped_at_max():
    sentiment_tracker.reset_state()
    # Force the cap to a small value for the test
    original = sentiment_tracker.MAX_TRACKED_CALLS
    sentiment_tracker.MAX_TRACKED_CALLS = 5
    try:
        for i in range(20):
            sentiment_tracker.record(f"CA_{i}", "neutral")
        # Dict should never exceed the cap
        assert len(sentiment_tracker.snapshot()) <= 5
    finally:
        sentiment_tracker.MAX_TRACKED_CALLS = original


def test_call_timer_record_end_also_clears_sentiment():
    """Centralized cleanup: call_timer.record_end transitively clears
    sentiment_tracker so callers don't have to remember both."""
    sentiment_tracker.reset_state()
    call_timer.record_start("CA_e2e", "ace_hvac")
    sentiment_tracker.record("CA_e2e", "frustrated")
    assert "CA_e2e" in sentiment_tracker.snapshot()
    call_timer.record_end("CA_e2e")
    assert "CA_e2e" not in sentiment_tracker.snapshot()
    assert "CA_e2e" not in call_timer.snapshot()


# ── call_timer cap ───────────────────────────────────────────────────

def test_call_timer_capped_at_max():
    # Reset
    with call_timer._state_lock:
        call_timer._calls.clear()
    original = call_timer.MAX_CONCURRENT_CALLS
    call_timer.MAX_CONCURRENT_CALLS = 10
    try:
        for i in range(50):
            call_timer.record_start(f"CA_full_{i}", "ace_hvac")
        assert len(call_timer.snapshot()) <= 10
    finally:
        call_timer.MAX_CONCURRENT_CALLS = original
        with call_timer._state_lock:
            call_timer._calls.clear()


def test_call_timer_oldest_evicted():
    with call_timer._state_lock:
        call_timer._calls.clear()
    original = call_timer.MAX_CONCURRENT_CALLS
    call_timer.MAX_CONCURRENT_CALLS = 3
    try:
        # Insert in time order
        call_timer.record_start("CA_evict_1", "x")
        # Simulate the first one being older
        with call_timer._state_lock:
            call_timer._calls["CA_evict_1"]["start_ts"] = 1000.0
        call_timer.record_start("CA_evict_2", "x")
        with call_timer._state_lock:
            call_timer._calls["CA_evict_2"]["start_ts"] = 2000.0
        call_timer.record_start("CA_evict_3", "x")
        with call_timer._state_lock:
            call_timer._calls["CA_evict_3"]["start_ts"] = 3000.0
        # 4th should evict the oldest (CA_evict_1)
        call_timer.record_start("CA_evict_4", "x")
        snap = call_timer.snapshot()
        assert "CA_evict_1" not in snap
        assert "CA_evict_4" in snap
    finally:
        call_timer.MAX_CONCURRENT_CALLS = original
        with call_timer._state_lock:
            call_timer._calls.clear()


# ── security buckets ─────────────────────────────────────────────────

def test_security_buckets_capped_at_max():
    security.reset_buckets()
    original = security.MAX_BUCKETS
    security.MAX_BUCKETS = 10
    try:
        for i in range(50):
            security._take_token(f"10.0.0.{i}", rate_per_min=60)
        # Should never exceed the cap
        with security._bucket_lock:
            assert len(security._buckets) <= 10
    finally:
        security.MAX_BUCKETS = original
        security.reset_buckets()


def test_security_buckets_evict_oldest_lru():
    security.reset_buckets()
    original = security.MAX_BUCKETS
    security.MAX_BUCKETS = 3
    try:
        import time as _t
        # Manually insert with controlled `last` timestamps
        with security._bucket_lock:
            security._buckets["A"] = {"tokens": 60.0, "last": 1.0}
            security._buckets["B"] = {"tokens": 60.0, "last": 2.0}
            security._buckets["C"] = {"tokens": 60.0, "last": 3.0}
        # Adding D should evict A (oldest)
        security._take_token("D", rate_per_min=60)
        with security._bucket_lock:
            assert "A" not in security._buckets
            assert "D" in security._buckets
    finally:
        security.MAX_BUCKETS = original
        security.reset_buckets()


# ── signup buckets ───────────────────────────────────────────────────

def test_signup_bucket_pruning(monkeypatch):
    signup._reset_rate_limits()
    monkeypatch.setenv("SIGNUP_RATE_LIMIT_PER_HOUR", "5")
    # Simulate stale buckets that should age out
    import time as _t
    with signup._rate_lock:
        old_ts = _t.time() - 7200  # 2 hours ago
        signup._rate_buckets["stale_ip"] = [old_ts]
    # A new fresh hit on a different IP should leave stale_ip in place
    # (pruning is opportunistic, runs every ~50 calls), so we directly
    # call the rate-limit check to bring stale_ip's bucket down to []
    signup._check_rate_limit("stale_ip")
    with signup._rate_lock:
        # The stale entry's old timestamps are filtered out; only the
        # fresh just-now timestamp remains
        assert len(signup._rate_buckets["stale_ip"]) == 1


# ── scheduler dedup pruning ──────────────────────────────────────────

def test_scheduler_dedup_prunes_old_keys():
    scheduler._reset_state()
    # Insert keys spanning recent + ancient dates
    scheduler._sent_today[("ace_hvac", "2026-04-22")] = True   # recent
    scheduler._sent_today[("ace_hvac", "2024-01-01")] = True   # ancient
    scheduler._sent_today[("septic_pro", "2024-06-15")] = True  # ancient
    # Today: 2026-04-23
    scheduler._prune_old_dedup_keys("2026-04-23")
    keys = set(scheduler._sent_today.keys())
    assert ("ace_hvac", "2026-04-22") in keys
    assert ("ace_hvac", "2024-01-01") not in keys
    assert ("septic_pro", "2024-06-15") not in keys


def test_scheduler_dedup_handles_malformed_keys():
    """Bad date strings should not crash the prune."""
    scheduler._reset_state()
    scheduler._sent_today[("ace_hvac", "not-a-date")] = True
    scheduler._sent_today[("ace_hvac", "2026-04-22")] = True
    # Should not raise
    scheduler._prune_old_dedup_keys("2026-04-23")
    # Malformed key kept (max-ord fallback); valid key kept
    assert ("ace_hvac", "not-a-date") in scheduler._sent_today
    assert ("ace_hvac", "2026-04-22") in scheduler._sent_today


# ── steady-state simulation: many calls, bounded memory ─────────────

def test_thousand_calls_bounded_state():
    """Simulate 1000 calls coming and going — memory must stay bounded
    and call_timer.record_end must clear sentiment_tracker too."""
    with call_timer._state_lock:
        call_timer._calls.clear()
    sentiment_tracker.reset_state()

    for i in range(1000):
        sid = f"CA_steady_{i}"
        call_timer.record_start(sid, "ace_hvac")
        sentiment_tracker.record(sid, "neutral")
        call_timer.record_end(sid)

    # Both should be empty (or nearly so) after the cleanup chain
    assert len(call_timer.snapshot()) == 0
    assert len(sentiment_tracker.snapshot()) == 0


def test_pipeline_calls_record_end_on_terminal_status():
    """Integration: /voice/status terminal call clears both call_timer
    AND sentiment_tracker state."""
    import importlib
    sentiment_tracker.reset_state()
    with call_timer._state_lock:
        call_timer._calls.clear()
    import main
    importlib.reload(main)
    # Bypass Twilio sig
    monkey_env = {"TWILIO_VERIFY_SIGNATURES": "false"}
    import os
    for k, v in monkey_env.items():
        os.environ[k] = v
    try:
        client = TestClient(main.app)
        # Simulate a call cycle:
        call_timer.record_start("CA_lifecycle", "ace_hvac")
        sentiment_tracker.record("CA_lifecycle", "neutral")
        # /voice/status with 'completed' should clear both
        r = client.post("/voice/status",
                        data={"From": "+14155550100",
                              "To": "+18449403274",
                              "CallSid": "CA_lifecycle",
                              "CallStatus": "completed",
                              "CallDuration": "60"})
        assert r.status_code == 200
        assert "CA_lifecycle" not in call_timer.snapshot()
        assert "CA_lifecycle" not in sentiment_tracker.snapshot()
    finally:
        for k in monkey_env:
            os.environ.pop(k, None)
