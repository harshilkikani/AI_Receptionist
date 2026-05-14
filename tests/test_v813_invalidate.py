"""V8.13 — selective cache invalidation.

invalidate_text(client, text) drops one cached file by recomputing the
prewarm+runtime hashes from the tenant's voice config. Lets the
operator re-render a single phrase after a settings tweak without
flushing the entire cache (which would force the next prewarm to bill
~2k chars against the ElevenLabs budget).

Failure modes covered:
 - Polly tenants — no audio files exist, function must no-op
 - Empty text — must no-op rather than wildcard-delete
 - File present → removed; file absent → reported as missing
 - prewarm_model == runtime_model — only one file, not double-deleted
 - prewarm_model != runtime_model — both files attempted
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src import audio_cache, tts


@pytest.fixture
def fake_audio_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_cache, "_AUDIO_DIR", tmp_path)
    return tmp_path


def _client(**overrides) -> dict:
    base = {
        "id": "ace_hvac",
        "name": "Ace HVAC",
        "tts_provider": "elevenlabs",
        "tts_voice_id": "VID",
        "tts_prewarm_model": "eleven_turbo_v2_5",
        "tts_runtime_model": "eleven_flash_v2_5",
    }
    base.update(overrides)
    return base


def _write_cached(audio_dir: Path, text: str, client: dict,
                  *, prewarm: bool) -> Path:
    """Drop a fake mp3 at the hash the prewarm or runtime path expects."""
    voice_id = tts.voice_id_for(client) or ""
    settings = tts.voice_settings_for(client)
    model = tts.model_for(client, prewarm=prewarm)
    h = tts._hash_key(text, voice_id, "elevenlabs",
                      model=model, settings=settings)
    path = audio_dir / f"{h}.mp3"
    path.write_bytes(b"fake-audio")
    return path


# ── no-op cases ────────────────────────────────────────────────────────

def test_invalidate_polly_tenant_noop(fake_audio_dir):
    client = {"id": "x", "tts_provider": "polly"}
    out = audio_cache.invalidate_text(client, "hi")
    assert out == {"removed": [], "missing": []}


def test_invalidate_empty_text_noop(fake_audio_dir):
    out = audio_cache.invalidate_text(_client(), "")
    assert out == {"removed": [], "missing": []}


def test_invalidate_whitespace_text_noop(fake_audio_dir):
    out = audio_cache.invalidate_text(_client(), "   \n\t  ")
    assert out == {"removed": [], "missing": []}


# ── happy path ─────────────────────────────────────────────────────────

def test_invalidate_removes_prewarm_and_runtime(fake_audio_dir):
    """With different prewarm vs runtime models, BOTH files exist and
    both must be removed."""
    client = _client()
    text = "Test phrase"
    p_prewarm = _write_cached(fake_audio_dir, text, client, prewarm=True)
    p_runtime = _write_cached(fake_audio_dir, text, client, prewarm=False)
    assert p_prewarm.exists() and p_runtime.exists()
    assert p_prewarm != p_runtime  # different models → different hashes

    out = audio_cache.invalidate_text(client, text)
    assert len(out["removed"]) == 2
    assert out["missing"] == []
    assert not p_prewarm.exists()
    assert not p_runtime.exists()


def test_invalidate_returns_missing_when_no_cache(fake_audio_dir):
    client = _client()
    out = audio_cache.invalidate_text(client, "never rendered")
    assert out["removed"] == []
    assert len(out["missing"]) == 2  # prewarm + runtime hashes


def test_invalidate_handles_one_cached_one_missing(fake_audio_dir):
    client = _client()
    text = "Half-cached phrase"
    _write_cached(fake_audio_dir, text, client, prewarm=True)
    # runtime file deliberately not written
    out = audio_cache.invalidate_text(client, text)
    assert len(out["removed"]) == 1
    assert len(out["missing"]) == 1


# ── model collapse ────────────────────────────────────────────────────

def test_invalidate_same_prewarm_and_runtime_model(fake_audio_dir):
    """If a tenant sets both models to the same value, there's only ONE
    cache file — must not be reported twice."""
    client = _client(tts_prewarm_model="eleven_flash_v2_5",
                     tts_runtime_model="eleven_flash_v2_5")
    text = "Single-model tenant"
    _write_cached(fake_audio_dir, text, client, prewarm=True)
    out = audio_cache.invalidate_text(client, text)
    assert len(out["removed"]) == 1
    assert len(out["missing"]) == 0


# ── selective flags ───────────────────────────────────────────────────

def test_invalidate_only_runtime(fake_audio_dir):
    """`prewarm=False` skips the prewarm hash."""
    client = _client()
    text = "Runtime-only invalidate"
    p_prewarm = _write_cached(fake_audio_dir, text, client, prewarm=True)
    p_runtime = _write_cached(fake_audio_dir, text, client, prewarm=False)
    out = audio_cache.invalidate_text(client, text, prewarm=False)
    assert len(out["removed"]) == 1
    assert p_prewarm.exists()      # untouched
    assert not p_runtime.exists()


def test_invalidate_only_prewarm(fake_audio_dir):
    client = _client()
    text = "Prewarm-only invalidate"
    p_prewarm = _write_cached(fake_audio_dir, text, client, prewarm=True)
    p_runtime = _write_cached(fake_audio_dir, text, client, prewarm=False)
    out = audio_cache.invalidate_text(client, text, runtime=False)
    assert len(out["removed"]) == 1
    assert not p_prewarm.exists()
    assert p_runtime.exists()      # untouched


# ── settings sensitivity ──────────────────────────────────────────────

def test_invalidate_respects_voice_settings(fake_audio_dir):
    """A cached file rendered under stability=0.5 should NOT be
    invalidated when the operator now reads stability=0.6 in the YAML
    (the hash is different — different file)."""
    text = "Settings-aware"
    # Write file under stability=0.5
    old_client = _client(tts_voice_settings={"stability": 0.5})
    p_old = _write_cached(fake_audio_dir, text, old_client, prewarm=True)
    # Now operator has stability=0.6 in YAML
    new_client = _client(tts_voice_settings={"stability": 0.6})
    out = audio_cache.invalidate_text(new_client, text, runtime=False)
    # No matching hash for stability=0.6 → reports missing, old file
    # stays (would be cleaned up by age-based eviction eventually).
    assert out["removed"] == []
    assert len(out["missing"]) == 1
    assert p_old.exists()


# ── CLI smoke ─────────────────────────────────────────────────────────

def test_cli_help_returns_zero(capsys):
    rc = audio_cache._cli([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "usage:" in out


def test_cli_invalidate_unknown_client(capsys):
    rc = audio_cache._cli(["invalidate", "no_such_tenant", "hi"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "unknown client" in out


def test_cli_unknown_command(capsys):
    rc = audio_cache._cli(["wat"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "unknown command" in out


def test_cli_invalidate_missing_args(capsys):
    rc = audio_cache._cli(["invalidate"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "usage:" in out
