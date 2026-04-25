"""V4.1 — TTS abstraction + ElevenLabs adapter tests."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import tts


@pytest.fixture(autouse=True)
def _reset_tts_state(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_AUDIO_DIR", tmp_path / "audio")
    tts.reset_stats()
    yield
    tts.reset_stats()


# ── provider resolution ──────────────────────────────────────────────

def test_resolve_polly_default():
    p = tts.resolve_provider({"id": "x"})
    assert isinstance(p, tts.PollyProvider)


def test_resolve_explicit_polly():
    p = tts.resolve_provider({"tts_provider": "polly"})
    assert isinstance(p, tts.PollyProvider)


def test_resolve_elevenlabs():
    p = tts.resolve_provider({"tts_provider": "elevenlabs"})
    assert isinstance(p, tts.ElevenLabsProvider)


def test_resolve_unknown_falls_back_to_polly():
    p = tts.resolve_provider({"tts_provider": "kazoo_synth"})
    assert isinstance(p, tts.PollyProvider)


def test_resolve_none_client():
    assert isinstance(tts.resolve_provider(None), tts.PollyProvider)


def test_voice_id_for_pulls_from_yaml():
    assert tts.voice_id_for({"tts_voice_id": "Rachel"}) == "Rachel"
    assert tts.voice_id_for({"tts_voice_id": ""}) is None
    assert tts.voice_id_for(None) is None


def test_voice_settings_for_validates_dict():
    assert tts.voice_settings_for({"tts_voice_settings": {"stability": 0.6}}) == {"stability": 0.6}
    assert tts.voice_settings_for({"tts_voice_settings": "junk"}) == {}
    assert tts.voice_settings_for(None) == {}


# ── PollyProvider ────────────────────────────────────────────────────

def test_polly_render_returns_polly_payload():
    p = tts.PollyProvider()
    out = p.render("hello", lang="en")
    assert out.kind == "polly"
    assert out.text == "hello"
    assert "Polly.Joanna" in out.polly_voice


def test_polly_render_picks_lang_voice():
    out = tts.PollyProvider().render("hola", lang="es")
    assert "Lupe" in out.polly_voice


def test_polly_render_uses_voice_id_override():
    out = tts.PollyProvider().render("x", voice_id="Polly.Matthew-Neural")
    assert out.polly_voice == "Polly.Matthew-Neural"


# ── ElevenLabsProvider ──────────────────────────────────────────────

def test_elevenlabs_falls_back_without_api_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    out = tts.ElevenLabsProvider().render("hi", lang="en")
    # Falls back to Polly transparently
    assert out.kind == "polly"
    stats = tts.render_stats()
    assert stats["fallback"] >= 1


def test_elevenlabs_falls_back_without_public_base_url(monkeypatch, tmp_path):
    """Cache hit but PUBLIC_BASE_URL unset → fall back to Polly."""
    # Pre-create a cached file so the cache_hit branch fires
    h = tts._hash_key("hello", "voice_xyz", "elevenlabs")
    cache_dir = tts._AUDIO_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{h}.mp3").write_bytes(b"fake audio")

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    out = tts.ElevenLabsProvider().render("hello", lang="en", voice_id="voice_xyz")
    assert out.kind == "polly"
    stats = tts.render_stats()
    assert stats["fallback"] >= 1


def test_elevenlabs_cache_hit_returns_play(monkeypatch):
    h = tts._hash_key("cached", "vid", "elevenlabs")
    cache_dir = tts._AUDIO_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{h}.mp3").write_bytes(b"fake audio bytes")

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    out = tts.ElevenLabsProvider().render("cached", voice_id="vid")
    assert out.kind == "play"
    assert out.url == f"https://example.com/audio/{h}.mp3"
    assert tts.render_stats()["cache_hit"] >= 1


def test_elevenlabs_handles_api_failure(monkeypatch):
    """On miss, if the API call fails, fall back to Polly without raising."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(tts, "_fetch_elevenlabs",
                        lambda text, vid, settings, path, **k: (False, "http_500"))
    out = tts.ElevenLabsProvider().render("uncached", voice_id="vid")
    assert out.kind == "polly"
    assert tts.render_stats()["fallback"] >= 1


def test_elevenlabs_render_writes_cache_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")

    def fake_fetch(text, vid, settings, path, **k):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio data")
        return True, None

    monkeypatch.setattr(tts, "_fetch_elevenlabs", fake_fetch)
    out = tts.ElevenLabsProvider().render("first time", voice_id="vid")
    assert out.kind == "play"
    assert "/audio/" in out.url


# ── render() top-level ──────────────────────────────────────────────

def test_render_empty_text_returns_polly_payload():
    out = tts.render("")
    assert out.kind == "polly"
    assert out.text == ""


def test_render_swallows_provider_exceptions(monkeypatch):
    """A provider that raises must not leak to the caller."""
    class BadProvider(tts.TtsProvider):
        def render(self, text, lang="en", voice_id=None, settings=None):
            raise RuntimeError("provider crashed")
    monkeypatch.setitem(tts._PROVIDERS, "bad", lambda: BadProvider())
    out = tts.render("hi", client={"tts_provider": "bad"})
    assert out.kind == "polly"   # fell back


# ── /audio endpoint ─────────────────────────────────────────────────

@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


def test_audio_endpoint_404_unknown(app_client):
    r = app_client.get("/audio/abcd1234.mp3")
    assert r.status_code == 404


def test_audio_endpoint_404_path_traversal(app_client):
    r = app_client.get("/audio/..%2F..%2Fetc%2Fpasswd.mp3")
    assert r.status_code in (404, 400)


def test_audio_endpoint_404_non_mp3(app_client):
    r = app_client.get("/audio/abcd1234.wav")
    assert r.status_code == 404


def test_audio_endpoint_serves_existing_file(app_client, tmp_path, monkeypatch):
    h = "test123abc456def"
    monkeypatch.setattr(tts, "_AUDIO_DIR", tmp_path / "audio_serve")
    (tmp_path / "audio_serve").mkdir(parents=True, exist_ok=True)
    (tmp_path / "audio_serve" / f"{h}.mp3").write_bytes(b"some mp3 bytes")
    # Reload main so the route picks up the patched _AUDIO_DIR
    import importlib, main
    importlib.reload(main)
    c = TestClient(main.app)
    r = c.get(f"/audio/{h}.mp3")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == b"some mp3 bytes"


# ── stats ──────────────────────────────────────────────────────────

def test_stats_track_provider_choice():
    tts.reset_stats()
    tts.PollyProvider().render("a")
    tts.PollyProvider().render("b")
    assert tts.render_stats()["polly"] == 2
