"""V8.13 — voice_ab harness tests.

The point of voice_ab is to enable cheap iteration on voice settings
without burning ElevenLabs credits. So the tests cover:
  - the slugifier (filenames are operator-visible)
  - --list (catalog rendering)
  - --dry-run cost preview (mustn't trigger renders)
  - --variants validation (catches typos before charging)
  - the variant catalog itself (regression guard against silently
    dropping an option)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ isn't on sys.path by default — replicate what running
# `python scripts/voice_ab.py` does.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
import voice_ab  # noqa: E402


# ── slug ──────────────────────────────────────────────────────────────

def test_slugify_basic():
    assert voice_ab._slugify("Hello world!") == "hello-world"


def test_slugify_strips_punctuation():
    assert voice_ab._slugify("What's going on?") == "what-s-going-on"


def test_slugify_caps_length():
    long = "a" * 200
    assert len(voice_ab._slugify(long)) <= 32


def test_slugify_empty_falls_back():
    assert voice_ab._slugify("") == "phrase"
    assert voice_ab._slugify("???") == "phrase"


# ── variant catalog ───────────────────────────────────────────────────

def test_variant_catalog_has_required_entries():
    """V8.13 — the production-baseline variants (turbo-default,
    flash-default) must be present so an operator can always compare
    'what production sounds like now' vs 'what the proposed change
    sounds like'."""
    assert "turbo-default" in voice_ab.VARIANTS
    assert "flash-default" in voice_ab.VARIANTS


def test_variant_each_has_model():
    for name, cfg in voice_ab.VARIANTS.items():
        assert "model" in cfg, f"{name} missing model"
        assert cfg["model"].startswith("eleven_"), f"{name} has weird model"
        assert "settings" in cfg, f"{name} missing settings"
        assert isinstance(cfg["settings"], dict), f"{name} settings type"


def test_default_variant_set_is_subset_of_catalog():
    for v in voice_ab.DEFAULT_VARIANT_SET:
        assert v in voice_ab.VARIANTS, f"default {v} not in catalog"


def test_default_variant_set_size_is_small():
    """Discipline — a default that renders 10 variants would silently
    burn budget. Keep the default tight."""
    assert len(voice_ab.DEFAULT_VARIANT_SET) <= 5


# ── CLI plumbing ──────────────────────────────────────────────────────

def test_list_returns_zero(capsys):
    rc = voice_ab.main(["--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "turbo-default" in out
    assert "flash-default" in out


def test_no_args_errors(capsys):
    rc = voice_ab.main([])
    err = capsys.readouterr().err
    assert rc == 2
    assert "text argument required" in err


def test_empty_text_errors(capsys):
    rc = voice_ab.main(["   "])
    err = capsys.readouterr().err
    assert rc == 2


def test_unknown_variant_rejected(capsys):
    rc = voice_ab.main(["hello", "--variants", "not-real-variant"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown" in err.lower()


def test_dry_run_does_not_render(tmp_path, monkeypatch):
    """The whole point of --dry-run is to preview cost without
    spending. Wire a network failure that would explode if a render
    were attempted; --dry-run must short-circuit before that."""
    monkeypatch.setattr(voice_ab, "_OUT_DIR", tmp_path)
    monkeypatch.setattr(voice_ab, "_CACHE_DIR", tmp_path / "audio")
    (tmp_path / "audio").mkdir()

    called = []
    def boom(*a, **k):
        called.append("hit")
        raise RuntimeError("dry-run must not call render")
    monkeypatch.setattr(voice_ab, "_render_one", boom)
    rc = voice_ab.main(["test phrase", "--dry-run",
                         "--variants", "turbo-default,flash-default"])
    assert rc == 0
    assert called == []


def test_dry_run_reports_cost(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(voice_ab, "_OUT_DIR", tmp_path)
    monkeypatch.setattr(voice_ab, "_CACHE_DIR", tmp_path / "audio")
    (tmp_path / "audio").mkdir()
    rc = voice_ab.main(["12345", "--dry-run",
                         "--variants", "turbo-default,flash-default"])
    out = capsys.readouterr().out
    assert rc == 0
    # 5 chars × 2 variants = 10
    assert "~10" in out


def test_dry_run_counts_cache_hits(tmp_path, monkeypatch, capsys):
    """If the cache file already exists, --dry-run should report 0
    fresh chars needed for that variant."""
    monkeypatch.setattr(voice_ab, "_OUT_DIR", tmp_path)
    monkeypatch.setattr(voice_ab, "_CACHE_DIR", tmp_path / "audio")
    (tmp_path / "audio").mkdir()

    # Pre-create the cache file for the turbo-default variant.
    from src import tts
    voice_id = "EXAVITQu4vr4xnSDxMaL"
    cfg = voice_ab.VARIANTS["turbo-default"]
    h = tts._hash_key("12345", voice_id, "elevenlabs",
                       model=cfg["model"], settings=cfg["settings"])
    (tmp_path / "audio" / f"{h}.mp3").write_bytes(b"fake")

    rc = voice_ab.main(["12345", "--dry-run",
                         "--variants", "turbo-default,flash-default"])
    out = capsys.readouterr().out
    assert rc == 0
    # Only the uncached flash-default → 5 chars
    assert "~5" in out
    assert "1 need rendering" in out
    assert "1 cached" in out
