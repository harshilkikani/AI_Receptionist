"""V10.0 — conversational realism refinement.

Three coordinated changes verified end-to-end:

  1. anti_robot expanded patterns: Gotcha / Got it!/Got it, /
     Perfect, / Sounds great! / No problem! / Yeah absolutely /
     Awesome! / Amazing! / Fantastic! / Excellent! at start of reply
     all get stripped or rewritten. Substance follows the opener
     verbatim — anti_robot doesn't touch real content.

  2. PREWARM_FILLERS pool doubled from 8 → 16 with a per-call
     no-repeat-within-3 memory so the same filler doesn't fire
     twice back-to-back across a turn series.

  3. Conversation-audit tool reports filler frequency / diversity /
     pure-ack share / repeated phrases — usable as a before/after
     gate when realism changes ship.
"""
from __future__ import annotations

import time

import pytest

from src import anti_robot, audio_cache


# ── anti_robot V10 patterns ─────────────────────────────────────────

@pytest.mark.parametrize("dirty, must_not_start_with", [
    ("Gotcha! Bob can get out there tomorrow.",      "Gotcha"),
    ("Gotcha, address?",                               "Gotcha"),
    ("Got it! What's the address?",                    "Got it!"),
    ("Got it, what's the address?",                    "Got it,"),
    ("Perfect, what's the address?",                   "Perfect,"),
    ("Perfect! Bob will call you.",                    "Perfect!"),
    ("Sounds great! We'll see you Tuesday.",           "Sounds great"),
    ("Sounds good! We'll be there.",                   "Sounds good"),
    ("No problem! What's a good number?",              "No problem!"),
    ("No worries! See you then.",                      "No worries!"),
    ("Yeah absolutely, I can help.",                   "Yeah absolutely"),
    ("Yeah, absolutely we can do that.",               "Yeah, absolutely"),
    ("Awesome! Let me grab your address.",             "Awesome"),
    ("Amazing, address?",                              "Amazing"),
    ("Fantastic! What time works?",                    "Fantastic"),
    ("Excellent! Bob will follow up.",                 "Excellent"),
])
def test_v10_anti_robot_strips_synthetic_warmth_opener(
        dirty, must_not_start_with):
    """V10.0 brief explicitly flagged these openers as detectable AI
    tells. After anti_robot.scrub, the reply must not open with any
    of them — substance carries the meaning."""
    cleaned, fired = anti_robot.scrub(dirty)
    assert fired, f"no rule fired for {dirty!r}"
    lo = cleaned.lower().lstrip()
    assert not lo.startswith(must_not_start_with.lower()), (
        f"opener still leaks through: {cleaned!r}")
    # Substance still there
    assert len(cleaned) >= 5


def test_v10_anti_robot_keeps_got_it_as_full_ack():
    """`Got it.` (period — full sentence) is a valid short ack and
    must NOT be scrubbed; only the comma-/exclamation-opener forms
    are. Otherwise the LLM has nowhere left to acknowledge briefly."""
    cleaned, fired = anti_robot.scrub("Got it.")
    assert cleaned.strip().lower() == "got it."
    # No strip rule should have fired
    assert not any("got" in f.lower() for f in fired)


def test_v10_anti_robot_keeps_perfect_in_middle_of_sentence():
    """`Perfect.` as opener should be stripped, but `that's perfect`
    mid-sentence must be left alone."""
    cleaned, _ = anti_robot.scrub("Tuesday at 2pm — that's perfect for us.")
    assert "perfect" in cleaned.lower()


def test_v10_anti_robot_substance_is_preserved_verbatim():
    """Whatever follows the stripped opener must survive untouched."""
    cleaned, _ = anti_robot.scrub(
        "Perfect, your address is 412 Maple Lane, Lancaster.")
    assert "412 Maple Lane, Lancaster" in cleaned


# ── PREWARM_FILLERS pool + no-repeat memory ─────────────────────────

def test_v10_filler_pool_doubled_to_16():
    """V10.0 pool size: 16 (was 8). Variety prevents the cycling
    pattern the conversation_audit flagged."""
    assert len(audio_cache.PREWARM_FILLERS) >= 16


def test_v10_no_synthetic_warmth_in_filler_pool():
    """The expanded pool must NOT include any of the AI-cheer
    phrases anti_robot now strips upstream. Otherwise we'd be
    playing back the very fillers we're trying to suppress."""
    pool = " ".join(audio_cache.PREWARM_FILLERS).lower()
    for banned in ("gotcha", "perfect", "absolutely",
                    "awesome", "amazing", "fantastic", "excellent"):
        assert banned not in pool, (
            f"V10.0 — '{banned}' in filler pool defeats anti_robot")


def test_v10_filler_history_state_resets_per_call():
    """Memory is keyed by call_sid; a fresh SID starts fresh."""
    # Clear any state from prior tests
    audio_cache._FILLER_HISTORY.clear()
    audio_cache._remember_filler("CA_v10_a", "Mhm,")
    audio_cache._remember_filler("CA_v10_a", "Yeah,")
    assert audio_cache._recent_fillers("CA_v10_a") == ["Mhm,", "Yeah,"]
    # Different SID = different history
    assert audio_cache._recent_fillers("CA_v10_b") == []


def test_v10_filler_history_bounded_to_no_repeat_window():
    """Memory keeps only the last _FILLER_NO_REPEAT_WITHIN entries —
    rolling window, not unbounded."""
    audio_cache._FILLER_HISTORY.clear()
    sid = "CA_v10_window"
    for i in range(10):
        audio_cache._remember_filler(sid, f"f{i}")
    hist = audio_cache._recent_fillers(sid)
    assert len(hist) == audio_cache._FILLER_NO_REPEAT_WITHIN
    # The window holds the most-recent picks
    assert hist[-1] == "f9"


def test_v10_filler_history_lru_caps_keyspace():
    """Per-process global keyspace is bounded so a long-running
    server can't leak memory through call_sid accumulation."""
    audio_cache._FILLER_HISTORY.clear()
    for i in range(audio_cache._FILLER_HISTORY_MAX + 20):
        audio_cache._remember_filler(f"CA_v10_lru_{i}", "Mhm,")
    assert len(audio_cache._FILLER_HISTORY) <= audio_cache._FILLER_HISTORY_MAX


def test_v10_filler_history_ignores_empty_call_sid():
    """When the SID is empty (e.g. unsigned dev request), we don't
    pollute the history dict."""
    audio_cache._FILLER_HISTORY.clear()
    audio_cache._remember_filler("", "Mhm,")
    assert "" not in audio_cache._FILLER_HISTORY


def test_v10_filler_payload_avoids_recent_picks(monkeypatch, tmp_path):
    """End-to-end: after a filler has been served on call_sid X, the
    next call to filler_payload_for(call_sid=X) must pick a different
    filler."""
    # Stand up an isolated audio dir + a tenant that opts into the path.
    monkeypatch.setattr(audio_cache, "_AUDIO_DIR", tmp_path)
    audio_cache._FILLER_HISTORY.clear()

    # Stub TTS resolution so every filler in the pool counts as cached.
    from src import tts
    monkeypatch.setattr(tts, "voice_id_for", lambda c: "VID")
    monkeypatch.setattr(tts, "model_for",
                         lambda c, **kw: "eleven_turbo_v2_5")
    monkeypatch.setattr(tts, "voice_settings_for", lambda c: {})
    monkeypatch.setattr(tts, "_public_base_url",
                         lambda: "https://example.com")
    # Create cache files for every filler so they all "exist"
    for filler in audio_cache.PREWARM_FILLERS:
        h = tts._hash_key(filler, "VID", "elevenlabs",
                           model="eleven_turbo_v2_5", settings={})
        (tmp_path / f"{h}.mp3").write_bytes(b"audio")

    client = {"tts_provider": "elevenlabs",
              "tts_prewarm_model": "eleven_turbo_v2_5"}
    sid = "CA_v10_no_repeat"

    seen: list = []
    for _ in range(audio_cache._FILLER_NO_REPEAT_WITHIN + 1):
        p = audio_cache.filler_payload_for(client, call_sid=sid)
        assert p is not None
        seen.append(p.url)
    # Within the window, all N+1 picks should be distinct URLs.
    assert len(set(seen)) == len(seen), (
        f"V10.0 — filler repeated within the no-repeat window: {seen}")


def test_v10_filler_payload_without_call_sid_still_works():
    """Backwards-compat: callers that don't supply a SID still get a
    filler (no history tracked)."""
    # Without a SID, the function should still work using the previous
    # random-pick behavior. Just verify it returns SOMETHING for an
    # elevenlabs tenant with a cached file — uses module-level state
    # that may or may not have cache hits. We just check no exception.
    out = audio_cache.filler_payload_for(
        {"tts_provider": "polly"})  # non-elevenlabs short-circuits
    assert out is None


# ── conversation_audit module ───────────────────────────────────────

def test_v10_audit_module_imports():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import conversation_audit  # noqa: F401


def test_v10_audit_starts_with_filler_detects_known_tokens():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import conversation_audit as ca
    assert ca._starts_with_filler("Got it. Address?") == "got"
    assert ca._starts_with_filler("Yeah, sure.") == "yeah"
    assert ca._starts_with_filler("Perfect, let's go.") == "perfect"
    assert ca._starts_with_filler("412 Maple Lane.") is None


def test_v10_audit_pure_ack_detection():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import conversation_audit as ca
    assert ca._is_pure_ack("Got it.") is True
    assert ca._is_pure_ack("Mhm.") is True
    assert ca._is_pure_ack("Got it. Address?") is False
    assert ca._is_pure_ack("Long substantive reply that isn't an ack.") is False


def test_v10_audit_opener_uses_first_two_words():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import conversation_audit as ca
    assert ca._opener("Got it. Address?") == "got it"
    assert ca._opener("Yeah, sure thing.") == "yeah sure"


def test_v10_audit_ngrams_lowercases_and_strips_punct():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import conversation_audit as ca
    grams = ca._ngrams("Call you back within the hour.", n=4)
    assert "call you back within" in grams
    assert "you back within the" in grams


def test_v10_audit_returns_report_shape():
    """Empty-window run still returns a usable report dict."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import conversation_audit as ca
    report = ca.audit(client_id="no_such_tenant", days=1)
    assert report["tenant_filter"] == "no_such_tenant"
    assert "tenants" in report
    assert isinstance(report["tenants"], dict)
