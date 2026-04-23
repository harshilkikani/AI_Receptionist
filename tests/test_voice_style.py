"""V3.3 — SSML prosody tests."""
from __future__ import annotations

import pytest

from src import voice_style


# ── apply_ssml ────────────────────────────────────────────────────────

def test_no_style_returns_escaped_plain():
    assert voice_style.apply_ssml("hey there") == "hey there"
    # & gets escaped
    assert voice_style.apply_ssml("pipes & joints") == "pipes &amp; joints"
    # < gets escaped (to block injection)
    assert voice_style.apply_ssml("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_unknown_style_returns_escaped_plain():
    assert voice_style.apply_ssml("hi", style="mystery") == "hi"


def test_warm_style_wraps_prosody():
    out = voice_style.apply_ssml("Hey. What's up?", style="warm")
    assert out.startswith('<prosody rate="95%"')
    assert "</prosody>" in out
    assert 'pitch="-2%"' in out


def test_warm_style_injects_sentence_breaks():
    out = voice_style.apply_ssml("Hey. What's up? Tell me.", style="warm")
    # Two sentence boundaries → two breaks
    assert out.count('<break time="350ms"/>') == 2


def test_warm_style_injects_clause_breaks():
    out = voice_style.apply_ssml("Okay, got it, give me one moment.", style="warm")
    assert '<break time="180ms"/>' in out


def test_brisk_style_shorter_breaks():
    out = voice_style.apply_ssml("Hey. One sec.", style="brisk")
    assert 'rate="108%"' in out
    assert '<break time="180ms"/>' in out  # sentence break, brisk timing


def test_formal_style_settings():
    out = voice_style.apply_ssml("Hello. How may I help?", style="formal")
    assert 'rate="100%"' in out
    assert 'pitch="0%"' in out


def test_apply_ssml_handles_empty():
    assert voice_style.apply_ssml("") == ""
    assert voice_style.apply_ssml("", style="warm") == ""


def test_apply_ssml_does_not_split_numbers():
    """Don't break between '$129. total' — but our regex requires a
    capital letter after the period, so '$129. total' should NOT break."""
    out = voice_style.apply_ssml("It's $129. total", style="warm")
    # No break because 't' is lowercase
    assert '<break' not in out


def test_apply_ssml_preserves_apostrophes():
    out = voice_style.apply_ssml("what's going on", style="warm")
    # html.escape(quote=False) keeps apostrophes raw
    assert "what's" in out


# ── style_for ──────────────────────────────────────────────────────────

def test_style_for_no_client_returns_none():
    assert voice_style.style_for(None) is None


def test_style_for_transactional_always_none():
    client = {"voice_style": "warm"}
    assert voice_style.style_for(client, mode="transactional") is None


def test_style_for_main_returns_configured():
    client = {"voice_style": "brisk"}
    assert voice_style.style_for(client, mode="main") == "brisk"


def test_style_for_unknown_value_returns_none():
    client = {"voice_style": "gibberish"}
    assert voice_style.style_for(client) is None


def test_style_for_missing_field_returns_none():
    assert voice_style.style_for({}) is None


# ── available_styles ──────────────────────────────────────────────────

def test_available_styles_covers_expected():
    styles = voice_style.available_styles()
    assert "warm" in styles
    assert "formal" in styles
    assert "brisk" in styles
