"""V5.6 — pre-warm + bounded eviction tests.

Pre-warm shouldn't run for polly tenants and shouldn't blow up if
ElevenLabs is misconfigured. Eviction must be a no-op below thresholds
and must drop the oldest files first when over."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src import audio_cache


# ── Helpers ─────────────────────────────────────────────────────────────

@pytest.fixture
def fake_audio_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_cache, "_AUDIO_DIR", tmp_path)
    return tmp_path


def _write(path: Path, size_bytes: int, mtime: float):
    path.write_bytes(b"\x00" * size_bytes)
    import os
    os.utime(path, (mtime, mtime))


# ── prewarm_for_tenant ─────────────────────────────────────────────────

def test_prewarm_skips_polly_tenant():
    client = {"id": "x", "name": "Acme", "tts_provider": "polly"}
    out = audio_cache.prewarm_for_tenant(client)
    assert out == {"rendered": 0, "skipped": 0, "errors": 0}


def test_prewarm_skips_when_provider_unset():
    client = {"id": "x", "name": "Acme"}  # default = polly
    out = audio_cache.prewarm_for_tenant(client)
    assert out["rendered"] == 0


def test_prewarm_calls_tts_render_for_elevenlabs():
    """Mock _tts.render to return 'play' payloads — every phrase counts as rendered."""
    from src import tts as _tts
    client = {"id": "ace", "name": "Ace HVAC", "tts_provider": "elevenlabs",
              "owner_name": "Bob"}
    fake_payload = _tts.TtsPayload(kind="play", url="http://example/x.mp3")
    with patch.object(_tts, "render", return_value=fake_payload) as m:
        out = audio_cache.prewarm_for_tenant(client)
    # 4 greetings (en/es/hi/gu) + 1 force-end + 10 degraded = 15
    assert out["rendered"] == 15
    assert out["errors"] == 0
    assert m.call_count == 15
    # First greeting must include company name
    first_text = m.call_args_list[0].args[0]
    assert "Ace HVAC" in first_text


def test_prewarm_counts_polly_fallback_as_skipped():
    """If render falls back (e.g. PUBLIC_BASE_URL unset), payload.kind=='polly'
    — that's a skip, not a render."""
    from src import tts as _tts
    client = {"id": "x", "name": "X", "tts_provider": "elevenlabs",
              "owner_name": "Bob"}
    fake = _tts.TtsPayload(kind="polly", text="...", polly_voice="Polly.Joanna-Neural")
    with patch.object(_tts, "render", return_value=fake):
        out = audio_cache.prewarm_for_tenant(client)
    assert out["rendered"] == 0
    assert out["skipped"] == 15


def test_prewarm_counts_render_exceptions_as_errors():
    from src import tts as _tts
    client = {"id": "x", "name": "X", "tts_provider": "elevenlabs",
              "owner_name": "Bob"}
    with patch.object(_tts, "render", side_effect=RuntimeError("boom")):
        out = audio_cache.prewarm_for_tenant(client)
    assert out["errors"] == 15
    assert out["rendered"] == 0


# ── prewarm_all ────────────────────────────────────────────────────────

def test_prewarm_all_skips_underscore_and_demo_tenants(monkeypatch):
    """`_default`, `_template`, and `demo_*` tenants are excluded so we
    don't waste API calls on short-lived demos."""
    fake_clients = [
        {"id": "_default", "tts_provider": "elevenlabs"},
        {"id": "_template", "tts_provider": "elevenlabs"},
        {"id": "demo_20260425_xyz", "tts_provider": "elevenlabs"},
        {"id": "ace_hvac", "tts_provider": "polly"},
        {"id": "real_one", "tts_provider": "elevenlabs", "name": "Real",
         "owner_name": "Bob"},
    ]
    from src import tenant as _tenant, tts as _tts
    monkeypatch.setattr(_tenant, "list_all", lambda: fake_clients)
    monkeypatch.setattr(_tenant, "load_client_by_id",
                        lambda cid: next((c for c in fake_clients
                                          if c["id"] == cid), None))
    fake_payload = _tts.TtsPayload(kind="play", url="http://example/x.mp3")
    with patch.object(_tts, "render", return_value=fake_payload) as m:
        out = audio_cache.prewarm_all()
    # only `real_one` should have been pre-warmed
    assert out["tenants_prewarmed"] == 1
    assert "real_one" in out["detail"]
    assert m.call_count == 15


def test_prewarm_all_counts_polly_tenants_as_skipped(monkeypatch):
    fake = [{"id": "ace", "tts_provider": "polly", "name": "Ace"}]
    from src import tenant as _tenant
    monkeypatch.setattr(_tenant, "list_all", lambda: fake)
    out = audio_cache.prewarm_all()
    assert out["tenants_prewarmed"] == 0
    assert out["tenants_skipped"] == 1


# ── evict_if_needed ────────────────────────────────────────────────────

def test_evict_no_directory_is_noop(monkeypatch, tmp_path):
    """If data/audio/ doesn't exist (fresh install) eviction returns zero
    counts rather than raising."""
    monkeypatch.setattr(audio_cache, "_AUDIO_DIR", tmp_path / "missing")
    out = audio_cache.evict_if_needed()
    assert out == {"evicted_age": 0, "evicted_size": 0,
                   "kept": 0, "bytes_freed": 0}


def test_evict_drops_files_older_than_age(fake_audio_dir):
    now = time.time()
    fresh = fake_audio_dir / "fresh.mp3"
    old = fake_audio_dir / "old.mp3"
    _write(fresh, 1024, now)
    _write(old, 2048, now - (60 * 86400))  # 60 days old
    out = audio_cache.evict_if_needed(max_age_days=30, max_total_mb=999, now=now)
    assert out["evicted_age"] == 1
    assert out["bytes_freed"] == 2048
    assert fresh.exists()
    assert not old.exists()


def test_evict_respects_size_cap_dropping_oldest_first(fake_audio_dir):
    """Five 1MB files when cap is 3MB → oldest 2 evicted, newest 3 kept."""
    now = time.time()
    files = []
    for i in range(5):
        p = fake_audio_dir / f"f{i}.mp3"
        # i=0 oldest, i=4 newest
        _write(p, 1024 * 1024, now - (5 - i) * 60)
        files.append(p)
    out = audio_cache.evict_if_needed(max_age_days=999, max_total_mb=3, now=now)
    assert out["evicted_size"] == 2
    assert out["kept"] == 3
    # the two oldest files are gone
    assert not files[0].exists()
    assert not files[1].exists()
    # the three newest survive
    assert files[2].exists()
    assert files[3].exists()
    assert files[4].exists()


def test_evict_under_threshold_is_noop(fake_audio_dir):
    now = time.time()
    p = fake_audio_dir / "x.mp3"
    _write(p, 1024, now)
    out = audio_cache.evict_if_needed(max_age_days=30, max_total_mb=10, now=now)
    assert out == {"evicted_age": 0, "evicted_size": 0,
                   "kept": 1, "bytes_freed": 0}
    assert p.exists()


def test_evict_ignores_non_mp3(fake_audio_dir):
    now = time.time()
    keep = fake_audio_dir / "x.mp3"
    skip = fake_audio_dir / "x.txt"
    _write(keep, 1024, now - (60 * 86400))
    _write(skip, 1024, now - (60 * 86400))
    audio_cache.evict_if_needed(max_age_days=30, max_total_mb=999, now=now)
    assert not keep.exists()
    assert skip.exists()  # non-mp3 untouched


def test_cache_stats(fake_audio_dir):
    now = time.time()
    _write(fake_audio_dir / "a.mp3", 100, now)
    _write(fake_audio_dir / "b.mp3", 200, now)
    _write(fake_audio_dir / "c.txt", 9999, now)  # ignored
    s = audio_cache.cache_stats()
    assert s == {"file_count": 2, "total_bytes": 300}


def test_cache_stats_no_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_cache, "_AUDIO_DIR", tmp_path / "nope")
    assert audio_cache.cache_stats() == {"file_count": 0, "total_bytes": 0}
