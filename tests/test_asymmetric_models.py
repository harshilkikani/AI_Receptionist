"""V8.10a — asymmetric model selection tests.

Prewarm renders pay the model-quality cost ONCE per phrase; runtime
renders pay the latency cost on every live call. Different optimal
trade-offs → different model. Tests cover model resolution, hash-key
separation (so prewarm and runtime files don't shadow each other),
voice-settings plumbing (style + use_speaker_boost), and the
expanded greeting prewarm coverage.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src import tts, audio_cache


# ── model_for() resolution ─────────────────────────────────────────────

def test_model_for_returns_none_when_polly():
    assert tts.model_for({"tts_provider": "polly"}) is None
    assert tts.model_for({"tts_provider": "polly"}, prewarm=True) is None


def test_model_for_returns_none_when_no_client():
    assert tts.model_for(None) is None
    assert tts.model_for({}) is None


def test_model_for_runtime_default():
    """No tts_runtime_model on the tenant → DEFAULT_MODEL (Flash)."""
    out = tts.model_for({"tts_provider": "elevenlabs"})
    assert out == tts.ElevenLabsProvider.DEFAULT_MODEL


def test_model_for_prewarm_default():
    """No tts_prewarm_model on the tenant → eleven_multilingual_v2."""
    out = tts.model_for({"tts_provider": "elevenlabs"}, prewarm=True)
    assert out == "eleven_multilingual_v2"


def test_model_for_runtime_explicit():
    out = tts.model_for({"tts_provider": "elevenlabs",
                          "tts_runtime_model": "eleven_turbo_v2_5"})
    assert out == "eleven_turbo_v2_5"


def test_model_for_prewarm_explicit():
    out = tts.model_for({"tts_provider": "elevenlabs",
                          "tts_prewarm_model": "eleven_v3"},
                         prewarm=True)
    assert out == "eleven_v3"


def test_model_for_runtime_unaffected_by_prewarm_field():
    out = tts.model_for({"tts_provider": "elevenlabs",
                          "tts_prewarm_model": "eleven_multilingual_v2"})
    assert out == tts.ElevenLabsProvider.DEFAULT_MODEL


# ── hash key includes model ────────────────────────────────────────────

def test_hash_key_model_changes_hash():
    a = tts._hash_key("hello", "voiceA", "elevenlabs",
                      model="eleven_flash_v2_5")
    b = tts._hash_key("hello", "voiceA", "elevenlabs",
                      model="eleven_multilingual_v2")
    assert a != b


def test_hash_key_no_model_backwards_compat():
    """Omitting model preserves the pre-V8.10a hash so old cached
    files don't have to be re-rendered."""
    a = tts._hash_key("hello", "voiceA", "elevenlabs")
    b = tts._hash_key("hello", "voiceA", "elevenlabs", model=None)
    assert a == b


# ── render() passes prewarm flag through ───────────────────────────────

def test_render_routes_prewarm_to_prewarm_model(monkeypatch):
    """tts.render(client, prewarm=True) must hit the provider with
    the prewarm_model. Verifies the plumbing end-to-end."""
    captured = {}

    class _Tap(tts.TtsProvider):
        name = "tap"
        def render(self, text, lang="en", voice_id=None, settings=None,
                   client_id=None, cap_chars=None, model=None):
            captured["model"] = model
            captured["settings"] = settings
            return tts.TtsPayload(kind="polly", text=text)

    monkeypatch.setitem(tts._PROVIDERS, "tap", lambda: _Tap())
    client = {
        "id": "x", "tts_provider": "tap",
        "tts_prewarm_model": "eleven_multilingual_v2",
        "tts_runtime_model": "eleven_flash_v2_5",
    }
    # Have to special-case model_for for "tap" — it returns None for
    # non-elevenlabs providers. Force the tap as if elevenlabs to test
    # plumbing.
    monkeypatch.setattr(
        tts, "model_for",
        lambda c, prewarm=False: (
            c.get("tts_prewarm_model") if prewarm
            else c.get("tts_runtime_model")))

    tts.render("hi", client=client, prewarm=True)
    assert captured["model"] == "eleven_multilingual_v2"
    tts.render("hi", client=client, prewarm=False)
    assert captured["model"] == "eleven_flash_v2_5"


def test_render_default_prewarm_false():
    """`prewarm` defaults to False — runtime path."""
    captured = {}

    class _Tap(tts.TtsProvider):
        name = "tap"
        def render(self, text, lang="en", **kw):
            captured["called"] = True
            return tts.TtsPayload(kind="polly", text=text)

    with patch.dict(tts._PROVIDERS, {"tap": lambda: _Tap()}):
        out = tts.render("hi", client={"tts_provider": "tap"})
    assert captured.get("called")
    assert out is not None


# ── ElevenLabsProvider uses effective_model + caches per model ────────

@pytest.fixture(autouse=True)
def _reset_tts_state(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_AUDIO_DIR", tmp_path / "audio")
    tts.reset_stats()
    yield
    tts.reset_stats()


def test_eleven_render_cache_key_includes_model(monkeypatch):
    """Same text + voice + provider but different model → different
    cache file. Verifies prewarm and runtime don't collide."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(tts, "reset_base_url_cache", lambda: None)

    captured_paths = []
    def fake_fetch(text, vid, settings, path, **kw):
        captured_paths.append(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return True, None
    monkeypatch.setattr(tts, "_fetch_elevenlabs", fake_fetch)

    p = tts.ElevenLabsProvider()
    p.render("hello", voice_id="vid", model="eleven_flash_v2_5")
    p.render("hello", voice_id="vid", model="eleven_multilingual_v2")
    # Two different filenames written
    assert len(captured_paths) == 2
    assert captured_paths[0] != captured_paths[1]


def test_eleven_render_passes_model_to_fetch(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(tts, "reset_base_url_cache", lambda: None)
    seen = []
    def fake_fetch(text, vid, settings, path, *, client_id=None, model=None):
        seen.append(model)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return True, None
    monkeypatch.setattr(tts, "_fetch_elevenlabs", fake_fetch)
    p = tts.ElevenLabsProvider()
    p.render("hi", voice_id="v", model="eleven_multilingual_v2")
    assert seen == ["eleven_multilingual_v2"]


# ── _fetch_elevenlabs honors model + style + speaker_boost ────────────

def test_fetch_body_includes_overridden_model(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    sent_body = {}
    def fake_post(path, headers, body, timeout=10.0):
        import json
        sent_body["json"] = json.loads(body.decode("utf-8"))
        return 200, b"audio", False
    monkeypatch.setattr(tts, "_request_post", fake_post)
    tts._fetch_elevenlabs("hello", "v", {}, tmp_path / "x.mp3",
                          model="eleven_v3")
    assert sent_body["json"]["model_id"] == "eleven_v3"


def test_fetch_body_falls_back_to_env_when_no_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
    sent = {}
    def fake_post(path, headers, body, timeout=10.0):
        import json
        sent["body"] = json.loads(body.decode("utf-8"))
        return 200, b"audio", False
    monkeypatch.setattr(tts, "_request_post", fake_post)
    tts._fetch_elevenlabs("hi", "v", {}, tmp_path / "x.mp3")
    assert sent["body"]["model_id"] == "eleven_turbo_v2_5"


def test_fetch_body_includes_style_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    sent = {}
    def fake_post(path, headers, body, timeout=10.0):
        import json
        sent["body"] = json.loads(body.decode("utf-8"))
        return 200, b"audio", False
    monkeypatch.setattr(tts, "_request_post", fake_post)
    tts._fetch_elevenlabs("hi", "v", {"style": 0.3},
                          tmp_path / "x.mp3")
    assert sent["body"]["voice_settings"]["style"] == 0.3


def test_fetch_body_includes_speaker_boost_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    sent = {}
    def fake_post(path, headers, body, timeout=10.0):
        import json
        sent["body"] = json.loads(body.decode("utf-8"))
        return 200, b"audio", False
    monkeypatch.setattr(tts, "_request_post", fake_post)
    tts._fetch_elevenlabs("hi", "v", {"use_speaker_boost": True},
                          tmp_path / "x.mp3")
    assert sent["body"]["voice_settings"]["use_speaker_boost"] is True


def test_fetch_body_omits_style_when_not_set(monkeypatch, tmp_path):
    """Old tenants without style configured don't get style in the
    payload — keeps Flash request shape unchanged for them."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    sent = {}
    def fake_post(path, headers, body, timeout=10.0):
        import json
        sent["body"] = json.loads(body.decode("utf-8"))
        return 200, b"audio", False
    monkeypatch.setattr(tts, "_request_post", fake_post)
    tts._fetch_elevenlabs("hi", "v", {"stability": 0.5},
                          tmp_path / "x.mp3")
    assert "style" not in sent["body"]["voice_settings"]


# ── Greeting prewarm covers V7.3 templates ────────────────────────────

def test_greetings_for_includes_all_v73_buckets():
    """V8.10a — _greetings_for now generates one string per
    (lang, bucket, variant) tuple from V7.3 templates."""
    out = audio_cache._greetings_for({"name": "Test"})
    # Should be MORE than the legacy 4
    assert len(out) > 4
    # Every output mentions "Test" (company name)
    assert all("Test" in s for s in out)


def test_greetings_for_dedupes_collisions():
    """Different langs / buckets shouldn't dedupe to the same string,
    but if a template happens to match the legacy form for that lang,
    one entry is fine."""
    out = audio_cache._greetings_for({"name": "Acme"})
    assert len(set(out)) == len(out), "duplicates in _greetings_for output"


def test_greetings_for_falls_back_when_greeting_import_breaks(monkeypatch):
    """If greeting module fails to import for some reason, fall back
    to the legacy 4-string set so prewarm doesn't crash."""
    import sys
    # Hide the greeting module temporarily
    original = sys.modules.pop("src.greeting", None)
    try:
        # Block the import attempt
        sys.modules["src.greeting"] = None   # type: ignore
        out = audio_cache._greetings_for({"name": "X"})
        assert len(out) == 4   # legacy fallback
    finally:
        if original is not None:
            sys.modules["src.greeting"] = original
        else:
            sys.modules.pop("src.greeting", None)


# ── audio_cache.prewarm_for_tenant uses prewarm=True ──────────────────

def test_prewarm_uses_prewarm_flag(monkeypatch):
    """prewarm_for_tenant must call tts.render(prewarm=True) so
    the prewarm_model is used."""
    called = {"prewarm_seen": []}
    from src import tts as _tts
    def fake_render(text, *, client=None, lang="en", prewarm=False):
        called["prewarm_seen"].append(prewarm)
        return _tts.TtsPayload(kind="play", url="http://x/y.mp3")
    monkeypatch.setattr(_tts, "render", fake_render)

    out = audio_cache.prewarm_for_tenant(
        {"id": "ace", "name": "Ace HVAC", "tts_provider": "elevenlabs",
         "owner_name": "Bob"})
    assert out["rendered"] > 0
    # EVERY render call should have prewarm=True
    assert all(p is True for p in called["prewarm_seen"]), (
        f"prewarm flag not always True: {called['prewarm_seen']}")


# ── filler_payload_for hashes with prewarm model ──────────────────────

def test_filler_payload_lookup_uses_prewarm_model_hash(monkeypatch, tmp_path):
    """If the filler is cached under the prewarm-model hash but the
    lookup hashes with no-model (the V8.9b behavior), it'd miss.
    V8.10a fixes the lookup to use the same prewarm-model hash."""
    monkeypatch.setattr(audio_cache, "_AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(tts, "_AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(tts, "_public_base_url",
                        lambda: "http://example.com")

    voice_id = "voiceA"
    prewarm_model = "eleven_multilingual_v2"
    # Cache file exists ONLY under the prewarm-model hash
    text = audio_cache.PREWARM_FILLERS[0]
    h_with = tts._hash_key(text, voice_id, "elevenlabs",
                            model=prewarm_model)
    (tmp_path / "audio").mkdir(parents=True, exist_ok=True)
    (tmp_path / "audio" / f"{h_with}.mp3").write_bytes(b"audio")

    client = {
        "tts_provider": "elevenlabs",
        "tts_voice_id": voice_id,
        "tts_prewarm_model": prewarm_model,
        "tts_runtime_model": "eleven_flash_v2_5",
    }
    import random as _r
    rng = _r.Random()
    rng.shuffle = lambda lst: None    # preserve order so we hit the one we cached
    payload = audio_cache.filler_payload_for(client, rng=rng)
    assert payload is not None
    assert payload.kind == "play"


def test_filler_payload_missing_when_cache_only_under_no_model(monkeypatch, tmp_path):
    """A file cached WITHOUT a model in the hash (legacy V8.9b cache)
    should NOT be picked up by V8.10a's model-aware lookup. This
    forces a re-prewarm with the new model on deploy."""
    monkeypatch.setattr(audio_cache, "_AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(tts, "_AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(tts, "_public_base_url",
                        lambda: "http://example.com")

    voice_id = "voiceA"
    text = audio_cache.PREWARM_FILLERS[0]
    h_no_model = tts._hash_key(text, voice_id, "elevenlabs")
    (tmp_path / "audio").mkdir(parents=True, exist_ok=True)
    (tmp_path / "audio" / f"{h_no_model}.mp3").write_bytes(b"old")

    client = {
        "tts_provider": "elevenlabs",
        "tts_voice_id": voice_id,
        "tts_prewarm_model": "eleven_multilingual_v2",
    }
    payload = audio_cache.filler_payload_for(client)
    # File exists at the OLD hash but lookup uses the NEW hash → miss
    assert payload is None


# ── prewarm-in-background lifespan check ─────────────────────────────

def test_lifespan_dispatches_prewarm_in_thread(monkeypatch):
    """Booting the app should NOT block on prewarm. Verify the
    prewarm call happens in a thread (not on the main lifespan
    coroutine)."""
    import importlib
    from src import audio_cache as _ac

    called_on_thread = []
    main_thread_ident = None
    import threading
    main_thread_ident = threading.main_thread().ident

    def fake_prewarm_all():
        called_on_thread.append(threading.current_thread().ident)
        return {"tenants_prewarmed": 0, "rendered": 0,
                "skipped": 0, "errors": 0, "tenants_skipped": 0}
    monkeypatch.setattr(_ac, "prewarm_all", fake_prewarm_all)
    monkeypatch.setattr(_ac, "evict_if_needed",
                        lambda **k: {"evicted_age": 0, "evicted_size": 0,
                                      "kept": 0, "bytes_freed": 0})

    import main
    importlib.reload(main)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        c.get("/health")
        # Give the thread a moment
        import time
        for _ in range(20):
            if called_on_thread:
                break
            time.sleep(0.05)

    assert called_on_thread, "prewarm thread never fired"
    # Must NOT have run on the main thread
    assert called_on_thread[0] != main_thread_ident, (
        "prewarm ran on the main thread — it should be in a worker")
