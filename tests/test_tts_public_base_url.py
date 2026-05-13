"""V7.1 — public-base-URL resolution + tunnel hint fallback.

reclaim_tunnel.py persists its trycloudflare URL to data/tunnel_url.txt
on every (re)start, but the uvicorn process used to only check
os.environ['PUBLIC_BASE_URL']. After a tunnel restart that env var was
stale, so ElevenLabs <Play> URLs all fell back to Polly silently.
The new _public_base_url() resolver falls through to the hint file.
"""
from __future__ import annotations

import pytest

from src import tts


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_TUNNEL_HINT_FILE", tmp_path / "tunnel.txt")
    tts.reset_base_url_cache()
    yield
    tts.reset_base_url_cache()


def test_env_var_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://from-env.example")
    # Hint file ALSO set — env should win
    (tmp_path / "tunnel.txt").write_text("https://from-hint.example")
    tts.reset_base_url_cache()
    assert tts._public_base_url() == "https://from-env.example"


def test_env_unset_uses_hint_file(monkeypatch, tmp_path):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    (tmp_path / "tunnel.txt").write_text(
        "https://closing-foods-isa-satisfaction.trycloudflare.com")
    tts.reset_base_url_cache()
    assert tts._public_base_url() == (
        "https://closing-foods-isa-satisfaction.trycloudflare.com")


def test_env_empty_string_uses_hint_file(monkeypatch, tmp_path):
    """Common in .env files: `PUBLIC_BASE_URL=` (declared but empty)."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "")
    (tmp_path / "tunnel.txt").write_text("https://fromhint.example")
    tts.reset_base_url_cache()
    assert tts._public_base_url() == "https://fromhint.example"


def test_env_whitespace_only_uses_hint_file(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLIC_BASE_URL", "   ")
    (tmp_path / "tunnel.txt").write_text("https://fromhint.example")
    tts.reset_base_url_cache()
    assert tts._public_base_url() == "https://fromhint.example"


def test_both_missing_returns_empty(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    # Hint file does not exist in this fixture's tmp_path
    tts.reset_base_url_cache()
    assert tts._public_base_url() == ""


def test_trailing_slash_stripped(monkeypatch, tmp_path):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    (tmp_path / "tunnel.txt").write_text("https://example.com/")
    tts.reset_base_url_cache()
    assert tts._public_base_url() == "https://example.com"


def test_payload_for_uses_resolved_url(monkeypatch, tmp_path):
    """End-to-end: ElevenLabsProvider._payload_for now picks up the
    hint file URL, not just env."""
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    (tmp_path / "tunnel.txt").write_text("https://tunneled.example")
    tts.reset_base_url_cache()
    provider = tts.ElevenLabsProvider()
    payload = provider._payload_for("abc123", "hello")
    assert payload.kind == "play"
    assert payload.url == "https://tunneled.example/audio/abc123.mp3"


def test_payload_for_falls_back_to_polly_when_both_missing(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    tts.reset_base_url_cache()
    provider = tts.ElevenLabsProvider()
    payload = provider._payload_for("abc", "hello")
    # No base URL → Twilio can't fetch → fall back to Polly
    assert payload.kind == "polly"


def test_cache_avoids_repeated_file_reads(monkeypatch, tmp_path):
    """The hint file is read once, then cached for ~10s. Verify by
    counting read calls via a wrapping spy."""
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    hint = tmp_path / "tunnel.txt"
    hint.write_text("https://once.example")
    reads = []
    orig_read_text = type(hint).read_text

    def spy_read_text(self, *a, **k):
        reads.append("hit")
        return orig_read_text(self, *a, **k)

    monkeypatch.setattr(type(hint), "read_text", spy_read_text)
    tts.reset_base_url_cache()
    for _ in range(5):
        assert tts._public_base_url() == "https://once.example"
    assert len(reads) == 1, f"expected 1 read, got {len(reads)}"


def test_reset_base_url_cache_forces_re_read(monkeypatch, tmp_path):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    hint = tmp_path / "tunnel.txt"
    hint.write_text("https://first.example")
    tts.reset_base_url_cache()
    assert tts._public_base_url() == "https://first.example"
    hint.write_text("https://second.example")
    # Without reset, still cached as first
    assert tts._public_base_url() == "https://first.example"
    tts.reset_base_url_cache()
    assert tts._public_base_url() == "https://second.example"
