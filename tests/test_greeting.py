"""V7.3 — greeting variation tests.

Time bucket boundaries, tz fallback, recall-aware override, named
returning caller, language coverage. Deterministic via `now=`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src import greeting


# ── time bucket boundaries ─────────────────────────────────────────────

@pytest.mark.parametrize("hour, bucket", [
    (5, "morning"), (8, "morning"), (10, "morning"),
    (11, "afternoon"), (13, "afternoon"), (16, "afternoon"),
    (17, "evening"), (19, "evening"), (20, "evening"),
    (21, "late_night"), (23, "late_night"),
    (0, "late_night"), (3, "late_night"), (4, "late_night"),
])
def test_time_bucket(hour, bucket):
    dt = datetime(2026, 5, 13, hour, 30, tzinfo=timezone.utc)
    assert greeting._time_bucket(dt) == bucket


# ── timezone resolution ────────────────────────────────────────────────

def test_resolve_tz_valid():
    tz = greeting._resolve_tz("America/Los_Angeles")
    # Just verify we got something usable, not a string
    assert hasattr(tz, "utcoffset") or tz is timezone.utc


def test_resolve_tz_invalid_falls_back():
    tz = greeting._resolve_tz("Not/A/Zone")
    # Should be a tzinfo, not raise
    assert tz is not None


def test_resolve_tz_none_falls_back():
    tz = greeting._resolve_tz(None)
    assert tz is not None


# ── plain bucket greetings per language ────────────────────────────────

@pytest.mark.parametrize("lang, bucket_hour, accepted_markers", [
    # V8.12.4 — EN templates tightened to local-business cadence.
    # Bucket-specific markers like "evening"/"tonight" are intentionally
    # gone (they were performative). Joanna's name is the universal
    # signature; late-night still announces itself.
    ("en", 9,  ("joanna",)),
    ("en", 14, ("joanna",)),
    ("en", 19, ("joanna",)),
    ("en", 23, ("after hours", "on-call")),
    ("es", 9,  ("buenos dias",)),
    ("es", 14, ("hola",)),
    ("es", 19, ("buenas tardes",)),
    ("es", 23, ("despues de horas",)),
    ("hi", 9,  ("subah",)),
    ("hi", 14, ("kya hua",)),
    ("hi", 19, ("shaam",)),
    ("hi", 23, ("emergency",)),
    ("gu", 9,  ("savar",)),
    ("gu", 14, ("kahejo",)),
    ("gu", 19, ("saanj",)),
    ("gu", 23, ("emergency",)),
])
def test_lang_bucket_greetings(lang, bucket_hour, accepted_markers):
    now = datetime(2026, 5, 13, bucket_hour, 30, tzinfo=timezone.utc)
    client = {"name": "Ace HVAC", "timezone": "UTC"}
    out = greeting.greeting_for(client, lang, now=now)
    low = out.lower()
    assert any(m in low for m in accepted_markers), (
        f"{lang}/{bucket_hour}h missing any of {accepted_markers!r}: {out!r}")
    assert "Ace HVAC" in out, f"company missing: {out!r}"


# ── named returning caller (English only path) ────────────────────────

def test_named_returning_caller_english():
    now = datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)
    client = {"name": "Ace HVAC", "timezone": "UTC"}
    caller = {"name": "John Smith", "type": "return"}
    out = greeting.greeting_for(client, "en", caller=caller, now=now)
    assert "John" in out
    assert "Smith" not in out   # only first name


def test_new_caller_no_name():
    now = datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)
    client = {"name": "Ace HVAC", "timezone": "UTC"}
    caller = {"name": "", "type": "new"}
    out = greeting.greeting_for(client, "en", caller=caller, now=now)
    assert "Joanna" in out


def test_named_returning_falls_through_for_non_english():
    """Named-returning is English-only — Spanish caller should get
    plain bucket greeting."""
    now = datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)
    client = {"name": "Ace HVAC", "timezone": "UTC"}
    caller = {"name": "Juan Garcia", "type": "return"}
    out = greeting.greeting_for(client, "es", caller=caller, now=now)
    assert "Juan" not in out
    assert "Hola" in out


def test_unknown_name_does_not_inject():
    now = datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)
    client = {"name": "Ace HVAC", "timezone": "UTC"}
    caller = {"name": "Unknown caller", "type": "return"}
    out = greeting.greeting_for(client, "en", caller=caller, now=now)
    assert "Unknown" not in out


# ── recall-aware override ──────────────────────────────────────────────

def test_recall_block_triggers_callback_greeting():
    now = datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)
    client = {"name": "Ace HVAC", "timezone": "UTC"}
    recall = "## Recent calls from this same number\n- 1 hour ago"
    out = greeting.greeting_for(client, "en",
                                 recall_block=recall, now=now)
    # Either "calling back" or "called earlier" — both valid recall greetings
    lower = out.lower()
    assert "calling back" in lower or "called earlier" in lower


def test_recall_block_empty_uses_normal_bucket():
    now = datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)
    client = {"name": "Ace HVAC", "timezone": "UTC"}
    out = greeting.greeting_for(client, "en",
                                 recall_block="", now=now)
    assert "calling back" not in out.lower()


def test_recall_block_non_english_falls_through():
    """Recall override is English-only for now."""
    now = datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)
    client = {"name": "Ace HVAC", "timezone": "UTC"}
    recall = "## Recent calls"
    out = greeting.greeting_for(client, "es", recall_block=recall, now=now)
    assert "calling back" not in out.lower()
    assert "Hola" in out


# ── priority: recall > named > bucket ──────────────────────────────────

def test_recall_takes_precedence_over_named():
    """If both signals fire, recall wins (caller is calling back — that's
    the most relevant context)."""
    now = datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)
    client = {"name": "Ace HVAC", "timezone": "UTC"}
    caller = {"name": "John Smith", "type": "return"}
    recall = "## Recent calls"
    out = greeting.greeting_for(client, "en",
                                 caller=caller, recall_block=recall, now=now)
    lower = out.lower()
    # Either form of recall greeting accepted
    assert "calling back" in lower or "called earlier" in lower


# ── timezone honor ─────────────────────────────────────────────────────

def test_timezone_shifts_bucket():
    """14:00 UTC = 09:00 ET (morning) but = 06:00 PT (still morning).
    14:00 UTC = 23:00 in Asia/Tokyo (late_night). Verify the shift.
    """
    utc_14 = datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)
    company = {"name": "X"}
    et = greeting.greeting_for({**company, "timezone": "America/New_York"},
                                "en", now=utc_14)
    jp = greeting.greeting_for({**company, "timezone": "Asia/Tokyo"},
                                "en", now=utc_14)
    # ET sees 9am (morning), Japan sees 11pm (late_night). V8.12.4
    # templates no longer say "after-hours" with a hyphen — accept the
    # new shape too.
    assert "morning" in et.lower() or "what's going on" in et.lower()
    assert ("after hours" in jp.lower()
            or "after-hours" in jp.lower()
            or "on-call" in jp.lower()
            or "emergency" in jp.lower())


def test_missing_timezone_falls_back_to_default():
    now = datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)
    out = greeting.greeting_for({"name": "X"}, "en", now=now)
    # No tz specified → DEFAULT_TZ (America/New_York) → 14:00 UTC = 9am ET.
    # Should land in morning bucket; V8.12.4 templates may or may not
    # literally say "morning" depending on rotation, but BOTH morning
    # variants contain "joanna" + company name + a question mark.
    assert "joanna" in out.lower()
    assert "X" in out


def test_garbage_timezone_falls_back():
    now = datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)
    out = greeting.greeting_for(
        {"name": "X", "timezone": "Mars/Olympus_Mons"}, "en", now=now)
    assert out   # didn't crash
    assert "X" in out


# ── deterministic rotation ─────────────────────────────────────────────

def test_same_date_same_template():
    """Calls on the same day return the same template variation
    (no stutter mid-shift)."""
    now = datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)
    client = {"name": "Ace", "timezone": "UTC"}
    a = greeting.greeting_for(client, "en", now=now)
    b = greeting.greeting_for(client, "en", now=now)
    c = greeting.greeting_for(client, "en", now=now)
    assert a == b == c


# ── client fallback ────────────────────────────────────────────────────

def test_no_client_doesnt_crash():
    """Defensive: greeting_for(None, ...) should never raise."""
    out = greeting.greeting_for(None, "en")
    assert out   # got something


def test_no_company_name_uses_fallback():
    out = greeting.greeting_for({}, "en")
    assert "the office" in out


# ── delegation through main._greeting_for ──────────────────────────────

def test_main_greeting_for_delegates(monkeypatch):
    """main._greeting_for is a thin wrapper around greeting.greeting_for.
    Verify the delegation works and keeps backwards-compatible 2-arg call.

    V8.12.4 — not every template variant says "Joanna" (some late-night
    templates use the company name only, like a real on-call greeting).
    Verify the company name + a question mark or period (sentence shape).
    """
    import main
    out = main._greeting_for({"name": "Ace HVAC", "timezone": "UTC"}, "en")
    assert "Ace HVAC" in out
    assert "." in out or "?" in out


def test_main_greeting_for_falls_back_on_helper_failure(monkeypatch):
    """If greeting.greeting_for raises, main._greeting_for must still
    return a valid string — never blocks the call."""
    import main
    from src import greeting as _greeting

    def boom(*a, **k):
        raise RuntimeError("greeting broke")
    monkeypatch.setattr(_greeting, "greeting_for", boom)
    out = main._greeting_for({"name": "Ace HVAC"}, "en")
    assert "Ace HVAC" in out
    assert "Joanna" in out
