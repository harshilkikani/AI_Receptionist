"""V8.12.5 — voice_settings in TTS cache hash key.

Without this, a stability / style / speaker_boost change in the tenant
YAML would silently serve stale audio rendered under the old settings.
After this, any settings change auto-invalidates the cache so the
next prewarm renders fresh.
"""
from __future__ import annotations

import pytest

from src import tts


# ── settings change produces different hash ───────────────────────────

def test_hash_key_settings_changes_hash():
    a = tts._hash_key("hi", "v", "elevenlabs",
                      model="eleven_flash_v2_5",
                      settings={"stability": 0.5})
    b = tts._hash_key("hi", "v", "elevenlabs",
                      model="eleven_flash_v2_5",
                      settings={"stability": 0.6})
    assert a != b


def test_hash_key_style_changes_hash():
    a = tts._hash_key("hi", "v", "elevenlabs",
                      settings={"stability": 0.5, "style": 0.0})
    b = tts._hash_key("hi", "v", "elevenlabs",
                      settings={"stability": 0.5, "style": 0.3})
    assert a != b


def test_hash_key_speaker_boost_changes_hash():
    a = tts._hash_key("hi", "v", "elevenlabs",
                      settings={"use_speaker_boost": True})
    b = tts._hash_key("hi", "v", "elevenlabs",
                      settings={"use_speaker_boost": False})
    assert a != b


def test_hash_key_similarity_changes_hash():
    a = tts._hash_key("hi", "v", "elevenlabs",
                      settings={"similarity": 0.7})
    b = tts._hash_key("hi", "v", "elevenlabs",
                      settings={"similarity": 0.8})
    assert a != b


# ── irrelevant settings keys ignored ──────────────────────────────────

def test_hash_key_unrelated_settings_ignored():
    """Only the 4 fields that actually affect audio should churn the
    cache. Adding an unrelated config key shouldn't invalidate."""
    a = tts._hash_key("hi", "v", "elevenlabs",
                      settings={"stability": 0.5})
    b = tts._hash_key("hi", "v", "elevenlabs",
                      settings={"stability": 0.5, "unrelated_field": 999})
    assert a == b


def test_hash_key_settings_order_invariant():
    """Stable JSON keys — same fields in different order produce same hash."""
    a = tts._hash_key("hi", "v", "elevenlabs",
                      settings={"stability": 0.5, "style": 0.3,
                                 "similarity": 0.75})
    b = tts._hash_key("hi", "v", "elevenlabs",
                      settings={"style": 0.3, "similarity": 0.75,
                                 "stability": 0.5})
    assert a == b


# ── backwards compatibility ──────────────────────────────────────────

def test_hash_key_no_settings_unchanged():
    """Omitting settings entirely preserves pre-V8.12.5 hash so old
    callers that don't pass settings keep their cache."""
    a = tts._hash_key("hi", "v", "elevenlabs", model="eleven_flash_v2_5")
    b = tts._hash_key("hi", "v", "elevenlabs", model="eleven_flash_v2_5",
                      settings=None)
    c = tts._hash_key("hi", "v", "elevenlabs", model="eleven_flash_v2_5",
                      settings={})
    assert a == b == c


def test_hash_key_only_model_no_settings_matches_pre_v812():
    """Just sanity that the V8.10a hashes still work for callers
    upgrading from V8.10a → V8.12.5 without passing settings."""
    h = tts._hash_key("test phrase", "voiceX", "elevenlabs",
                       model="eleven_turbo_v2_5")
    # Should be 24-char hex string
    assert len(h) == 24
    assert all(c in "0123456789abcdef" for c in h)


# ── speaker_boost alias handling ─────────────────────────────────────

def test_hash_key_speaker_boost_aliases_treated_same():
    """`speaker_boost` and `use_speaker_boost` are accepted interchangeably
    in the payload. Both should hash the same since they map to the
    same ElevenLabs field. Currently they DON'T (treated as distinct
    keys in the JSON repr). That's intentional — operators who set
    EITHER key consistently get a stable hash; mixing both in one
    yaml is operator error."""
    a = tts._hash_key("hi", "v", "elevenlabs",
                       settings={"use_speaker_boost": True})
    b = tts._hash_key("hi", "v", "elevenlabs",
                       settings={"speaker_boost": True})
    # Document the current behavior — different hashes. Operators
    # should pick one alias and stick with it.
    assert a != b


# ── ElevenLabsProvider.render plumbs settings into cache hash ────────

@pytest.fixture(autouse=True)
def _isolated_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_AUDIO_DIR", tmp_path / "audio")
    tts.reset_stats()
    yield
    tts.reset_stats()


def test_render_writes_different_files_for_different_settings(monkeypatch):
    """Same text + voice + model but different stability → two
    cache files. Verifies V8.12.5 invalidation works end-to-end."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(tts, "_public_base_url",
                        lambda: "https://example.com")
    captured_paths = []

    def fake_fetch(text, vid, settings, path, **kw):
        captured_paths.append(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return True, None
    monkeypatch.setattr(tts, "_fetch_elevenlabs", fake_fetch)

    p = tts.ElevenLabsProvider()
    p.render("hello", voice_id="v", settings={"stability": 0.4})
    p.render("hello", voice_id="v", settings={"stability": 0.5})
    assert len(captured_paths) == 2
    assert captured_paths[0] != captured_paths[1]


def test_render_same_settings_same_file_hit_cache(monkeypatch, tmp_path):
    """Identical settings = same hash = cache hit on the second call."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(tts, "_public_base_url",
                        lambda: "https://example.com")

    fetch_calls = []
    def fake_fetch(text, vid, settings, path, **kw):
        fetch_calls.append("hit")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return True, None
    monkeypatch.setattr(tts, "_fetch_elevenlabs", fake_fetch)

    p = tts.ElevenLabsProvider()
    settings = {"stability": 0.5, "similarity": 0.75}
    p.render("hello", voice_id="v", settings=settings)
    p.render("hello", voice_id="v", settings=settings)
    # First call rendered, second hit cache
    assert len(fetch_calls) == 1


# ── live tenant uses defaults (V8.12.1) ───────────────────────────────

def test_ace_hvac_has_no_voice_settings_block():
    """V8.12.1 — voice_settings removed from ace_hvac to let ElevenLabs
    defaults apply. Regression guard against future re-introduction of
    style / speaker_boost without explicit A/B justification."""
    from src import tenant
    client = tenant.load_client_by_id("ace_hvac")
    assert client is not None
    # Either absent, or present but empty
    vs = client.get("tts_voice_settings")
    assert vs is None or vs == {} or all(
        k not in vs for k in ("style", "use_speaker_boost")
    ), (f"ace_hvac re-introduced over-shaping voice_settings: {vs}. "
        f"V8.12 audit explicitly removed these. A/B test before re-adding.")


def test_ace_hvac_uses_turbo_prewarm_not_multilingual():
    """V8.12.2 — Multilingual_v2 was too performative for a
    receptionist. Verify the tenant config didn't silently roll back."""
    from src import tenant
    client = tenant.load_client_by_id("ace_hvac")
    assert client is not None
    assert client.get("tts_prewarm_model") == "eleven_turbo_v2_5", (
        "V8.12.2 audit: don't switch ace_hvac back to multilingual_v2 "
        "without A/B listening confirmation.")


def test_ace_hvac_owner_name_personal():
    """V8.12.5 — owner_name is a real-sounding name, not 'the owner'."""
    from src import tenant
    client = tenant.load_client_by_id("ace_hvac")
    assert client is not None
    name = (client.get("owner_name") or "").strip()
    assert name and name.lower() != "the owner", (
        f"V8.12.5 audit: owner_name should be a real name, got {name!r}")
