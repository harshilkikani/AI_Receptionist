"""V5.8 — ElevenLabs cost telemetry + per-tenant monthly cap.

Two halves:
  - usage.bump_tts_chars / tts_chars_for / tts_chars_summary roundtrip
  - tts.ElevenLabsProvider falls back to Polly when the tenant has hit
    plan.elevenlabs_monthly_cap_chars for the current month
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src import tts, usage


# ── usage table ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """Each test gets its own SQLite file."""
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage_v58.db")
    usage._reset_schema_cache()
    yield


def test_bump_and_read_zero_when_empty():
    assert usage.tts_chars_for("client_a", "elevenlabs") == 0


def test_bump_increments_chars():
    usage.bump_tts_chars("client_a", "elevenlabs", 100)
    assert usage.tts_chars_for("client_a", "elevenlabs") == 100
    usage.bump_tts_chars("client_a", "elevenlabs", 250)
    assert usage.tts_chars_for("client_a", "elevenlabs") == 350


def test_bump_isolated_per_client():
    usage.bump_tts_chars("a", "elevenlabs", 10)
    usage.bump_tts_chars("b", "elevenlabs", 20)
    assert usage.tts_chars_for("a", "elevenlabs") == 10
    assert usage.tts_chars_for("b", "elevenlabs") == 20


def test_bump_isolated_per_provider():
    usage.bump_tts_chars("a", "elevenlabs", 10)
    usage.bump_tts_chars("a", "azure", 99)
    assert usage.tts_chars_for("a", "elevenlabs") == 10
    assert usage.tts_chars_for("a", "azure") == 99


def test_bump_no_op_on_empty_or_zero():
    usage.bump_tts_chars("", "elevenlabs", 100)
    usage.bump_tts_chars("a", "", 100)
    usage.bump_tts_chars("a", "elevenlabs", 0)
    usage.bump_tts_chars("a", "elevenlabs", -5)
    assert usage.tts_chars_for("a", "elevenlabs") == 0


def test_summary_orders_by_chars_desc():
    usage.bump_tts_chars("low", "elevenlabs", 5)
    usage.bump_tts_chars("hi", "elevenlabs", 500)
    usage.bump_tts_chars("mid", "elevenlabs", 50)
    rows = usage.tts_chars_summary()
    assert [r[0] for r in rows] == ["hi", "mid", "low"]


def test_summary_filters_by_month():
    usage.bump_tts_chars("a", "elevenlabs", 10)
    rows = usage.tts_chars_summary(month="2099-01")
    assert rows == []


# ── cap_chars_for + _over_monthly_cap helpers ───────────────────────────

def test_cap_chars_for_returns_none_when_unset():
    assert tts.cap_chars_for({}) is None
    assert tts.cap_chars_for({"plan": {}}) is None
    assert tts.cap_chars_for(None) is None


def test_cap_chars_for_reads_plan_field():
    assert tts.cap_chars_for(
        {"plan": {"elevenlabs_monthly_cap_chars": 50000}}) == 50000


def test_cap_chars_for_rejects_zero_or_negative():
    assert tts.cap_chars_for(
        {"plan": {"elevenlabs_monthly_cap_chars": 0}}) is None
    assert tts.cap_chars_for(
        {"plan": {"elevenlabs_monthly_cap_chars": -10}}) is None


def test_cap_chars_for_handles_garbage():
    assert tts.cap_chars_for(
        {"plan": {"elevenlabs_monthly_cap_chars": "fifty thousand"}}) is None


def test_over_monthly_cap_db_error_returns_false(monkeypatch):
    """Open by default — if usage.tts_chars_for raises, the cap check
    must not block the call."""
    def boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(usage, "tts_chars_for", boom)
    assert tts._over_monthly_cap("c", 1000, 100) is False


def test_over_monthly_cap_under_threshold():
    usage.bump_tts_chars("c", "elevenlabs", 100)
    assert tts._over_monthly_cap("c", 1000, 200) is False


def test_over_monthly_cap_at_threshold():
    usage.bump_tts_chars("c", "elevenlabs", 800)
    assert tts._over_monthly_cap("c", 1000, 200) is True


def test_over_monthly_cap_already_over():
    usage.bump_tts_chars("c", "elevenlabs", 1500)
    assert tts._over_monthly_cap("c", 1000, 1) is True


# ── ElevenLabsProvider integration with cap ────────────────────────────

@pytest.fixture
def isolated_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_AUDIO_DIR", tmp_path / "audio")
    tts.reset_stats()
    yield


def test_provider_under_cap_renders_normally(monkeypatch, isolated_audio):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    usage.bump_tts_chars("ace", "elevenlabs", 10)

    def fake_fetch(text, vid, settings, path, *, client_id=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return True, None
    monkeypatch.setattr(tts, "_fetch_elevenlabs", fake_fetch)

    p = tts.ElevenLabsProvider()
    out = p.render("hello", voice_id="v", client_id="ace", cap_chars=1000)
    assert out.kind == "play"


def test_provider_over_cap_falls_back_to_polly(monkeypatch, isolated_audio):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    usage.bump_tts_chars("ace", "elevenlabs", 1500)

    fetch_called = [False]
    def fake_fetch(*a, **k):
        fetch_called[0] = True
        return True, None
    monkeypatch.setattr(tts, "_fetch_elevenlabs", fake_fetch)

    p = tts.ElevenLabsProvider()
    out = p.render("hello", voice_id="v", client_id="ace", cap_chars=1000)
    assert out.kind == "polly"  # silently degraded
    assert not fetch_called[0]   # never even hit the API
    assert tts.render_stats()["cap_fallback"] >= 1


def test_provider_cache_hit_ignores_cap(monkeypatch, isolated_audio):
    """Cache hits cost nothing — they should NEVER fall back to Polly,
    even when the tenant is over cap."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    usage.bump_tts_chars("ace", "elevenlabs", 999_999)

    text = "cached phrase"
    h = tts._hash_key(text, "v", "elevenlabs")
    tts._AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    (tts._AUDIO_DIR / f"{h}.mp3").write_bytes(b"audio")

    p = tts.ElevenLabsProvider()
    out = p.render(text, voice_id="v", client_id="ace", cap_chars=1000)
    assert out.kind == "play"  # cache hit even though over cap
    assert tts.render_stats()["cache_hit"] >= 1


def test_provider_cap_unset_never_blocks(monkeypatch, isolated_audio):
    """No cap configured → never block, regardless of running total."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    usage.bump_tts_chars("ace", "elevenlabs", 5_000_000)

    def fake_fetch(text, vid, settings, path, *, client_id=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return True, None
    monkeypatch.setattr(tts, "_fetch_elevenlabs", fake_fetch)

    out = tts.ElevenLabsProvider().render(
        "hi", voice_id="v", client_id="ace", cap_chars=None)
    assert out.kind == "play"


# ── _fetch_elevenlabs records to DB on success ─────────────────────────

def test_fetch_persists_chars_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(tts, "_request_post",
                        lambda p, h, b, timeout=10.0: (200, b"audio", False))
    out = tmp_path / "x.mp3"
    tts._fetch_elevenlabs("hello world", "v", {}, out, client_id="ace")
    assert usage.tts_chars_for("ace", "elevenlabs") == len("hello world")


def test_fetch_no_persist_without_client_id(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(tts, "_request_post",
                        lambda p, h, b, timeout=10.0: (200, b"audio", False))
    tts._fetch_elevenlabs("hello", "v", {}, tmp_path / "x.mp3", client_id=None)
    # No client_id → no persisted row
    assert usage.tts_chars_summary() == []


def test_fetch_no_persist_on_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(tts, "_request_post",
                        lambda p, h, b, timeout=10.0: (500, b"", False))
    tts._fetch_elevenlabs("hi", "v", {}, tmp_path / "x.mp3", client_id="ace")
    assert usage.tts_chars_for("ace", "elevenlabs") == 0


# ── render() top-level passes client_id + cap_chars ────────────────────

def test_render_top_level_threads_client_id_and_cap(monkeypatch, isolated_audio):
    """The convenience render() must extract id + cap from the client
    dict and pass them to the provider."""
    captured = {}

    class TapProvider(tts.TtsProvider):
        name = "tap"
        def render(self, text, lang="en", voice_id=None, settings=None,
                   client_id=None, cap_chars=None):
            captured["client_id"] = client_id
            captured["cap_chars"] = cap_chars
            return tts.TtsPayload(kind="polly", text=text)

    monkeypatch.setitem(tts._PROVIDERS, "tap", lambda: TapProvider())
    client = {"id": "ace", "tts_provider": "tap",
              "plan": {"elevenlabs_monthly_cap_chars": 2500}}
    tts.render("hello", client=client)
    assert captured["client_id"] == "ace"
    assert captured["cap_chars"] == 2500
