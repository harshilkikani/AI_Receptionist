"""V8.13 — voice A/B render harness.

Renders the same phrase under multiple ElevenLabs settings combinations,
writes labeled MP3s to data/voice_ab/, and reports per-variant char
cost. Built so the operator can listen and pick a winner without the
iteration burning a full prewarm cycle (~2000 chars) every time.

Why this exists:
    Every voice-prosody decision in V8.5 -> V8.12 was made theoretically
    because the agent making the changes can't listen to audio. V8.12
    over-shaped (style + speaker_boost + Multilingual) and produced
    uncanny-valley output ("haaai its Joanna..."). V8.13 walks the
    decision back to the human in the loop. This tool removes the
    iteration friction.

Cost discipline:
    - Cache-aware: a variant whose (text, voice, model, settings) tuple
      is already in data/audio/ is reused — only the labeled-copy file
      gets written. No second ElevenLabs hit.
    - Variants are explicit and small. Default set is 4 entries
      (~4 x len(text) chars). Pick `--variants name1,name2` to scope.
    - Always prints a char total before issuing renders so the operator
      can abort if it would blow the budget.

Usage:
    python scripts/voice_ab.py "Ace HVAC, this is Joanna. What's going on?"
    python scripts/voice_ab.py "..." --variants turbo-default,flash-default
    python scripts/voice_ab.py "..." --list
    python scripts/voice_ab.py "..." --label morning-greeting
    python scripts/voice_ab.py "..." --client ace_hvac     # use tenant voice
    python scripts/voice_ab.py "..." --dry-run             # cost preview only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass


# ── Variant catalog ────────────────────────────────────────────────────
#
# Each entry: {label: (model, settings)}. Keep the set small and
# meaningful — these are the dimensions that actually move perceptual
# quality on a phone call:
#
#   model               cost   prosody  who uses it
#   --------------------------------------------------------------------
#   eleven_flash_v2_5   low    flat     production runtime (live LLM
#                                       turns)
#   eleven_turbo_v2_5   mid    medium   prewarm path (greetings, acks)
#   eleven_multilingual_v2 high  rich   abandoned in V8.12 (too
#                                       performative)
#
# Settings: stability + style + use_speaker_boost interact non-linearly.
# Defaults are 0.5 / 0.0 / off. Hold similarity_boost at 0.75 for all
# variants so we're comparing one axis at a time.

VARIANTS: dict = {
    "turbo-default": {
        "model": "eleven_turbo_v2_5",
        "settings": {},   # ElevenLabs defaults (V8.12.1 production)
    },
    "flash-default": {
        "model": "eleven_flash_v2_5",
        "settings": {},
    },
    "turbo-steady": {
        # Higher stability — less prosodic variation, more "even".
        # Tests whether uncanny-valley comes from over-variation.
        "model": "eleven_turbo_v2_5",
        "settings": {"stability": 0.65, "similarity": 0.75},
    },
    "turbo-lively": {
        # Lower stability — more emotional range. Tests whether
        # baseline feels "flat" on the phone.
        "model": "eleven_turbo_v2_5",
        "settings": {"stability": 0.35, "similarity": 0.75},
    },
    "multilingual-default": {
        # The V8.12 rollback target — re-run only when you want to
        # confirm it's still too performative.
        "model": "eleven_multilingual_v2",
        "settings": {},
    },
}

DEFAULT_VARIANT_SET = ("turbo-default", "flash-default",
                        "turbo-steady", "turbo-lively")


_OUT_DIR = _ROOT / "data" / "voice_ab"
_CACHE_DIR = _ROOT / "data" / "audio"


def _slugify(text: str, max_len: int = 32) -> str:
    """Filename-safe label derived from the phrase."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len] or "phrase"


def _resolve_voice_id(client_id: Optional[str]) -> str:
    """Pick the voice the renders will use. With --client, read from
    tenant config; without, use the ElevenLabs Rachel default that
    matches ace_hvac."""
    if client_id:
        from src import tenant
        c = tenant.load_client_by_id(client_id)
        if not c:
            raise SystemExit(f"unknown client: {client_id}")
        from src import tts as _tts
        vid = _tts.voice_id_for(c)
        if not vid:
            raise SystemExit(f"{client_id} has no tts_voice_id configured")
        return vid
    return os.environ.get("ELEVENLABS_VOICE_ID",
                           "EXAVITQu4vr4xnSDxMaL")  # Rachel default


def _cache_path_for(text: str, voice_id: str,
                    model: str, settings: dict) -> Path:
    """Where the canonical render lives — same hash key the main TTS
    layer uses, so a voice_ab render also warms the production cache."""
    from src import tts as _tts
    h = _tts._hash_key(text, voice_id, "elevenlabs",
                       model=model, settings=settings)
    return _CACHE_DIR / f"{h}.mp3"


def _render_one(text: str, voice_id: str, *,
                model: str, settings: dict) -> tuple:
    """Fetch from ElevenLabs (or reuse cached) and return (path, fresh).
    fresh=False means it was already on disk."""
    from src import tts as _tts
    cache_path = _cache_path_for(text, voice_id, model, settings)
    if cache_path.exists():
        return cache_path, False
    ok, error = _tts._fetch_elevenlabs(
        text, voice_id, settings, cache_path, model=model)
    if not ok:
        raise SystemExit(f"render failed: {error}")
    return cache_path, True


def _list_variants() -> None:
    print(f"{len(VARIANTS)} built-in variants:")
    for name, cfg in VARIANTS.items():
        s = ", ".join(f"{k}={v}" for k, v in cfg["settings"].items()) or "defaults"
        print(f"  {name:24}  model={cfg['model']:24}  settings={s}")


def _parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="A/B render a phrase across ElevenLabs variants")
    p.add_argument("text", nargs="?",
                   help="phrase to render (quote it on the shell)")
    p.add_argument("--variants",
                   help="comma-separated variant names; default = "
                        + ",".join(DEFAULT_VARIANT_SET))
    p.add_argument("--client",
                   help="load voice_id from this tenant id (e.g. ace_hvac)")
    p.add_argument("--label",
                   help="filename prefix; default = slug of the text")
    p.add_argument("--list", action="store_true",
                   help="show the built-in variant catalog and exit")
    p.add_argument("--dry-run", action="store_true",
                   help="show cost estimate, don't render")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.list:
        _list_variants()
        return 0
    if not args.text:
        print("error: text argument required (or pass --list)",
              file=sys.stderr)
        return 2

    text = args.text.strip()
    if not text:
        print("error: text is empty after strip", file=sys.stderr)
        return 2

    variant_names = (args.variants.split(",") if args.variants
                     else list(DEFAULT_VARIANT_SET))
    variant_names = [v.strip() for v in variant_names if v.strip()]
    unknown = [v for v in variant_names if v not in VARIANTS]
    if unknown:
        print(f"unknown variants: {unknown}", file=sys.stderr)
        print("known variants:", ", ".join(VARIANTS.keys()), file=sys.stderr)
        return 2

    voice_id = _resolve_voice_id(args.client)
    label = args.label or _slugify(text)
    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Cost preview — count chars per variant that will actually hit
    # the network (cache misses only).
    char_len = len(text)
    planned = []
    for vname in variant_names:
        cfg = VARIANTS[vname]
        cache_path = _cache_path_for(text, voice_id,
                                      cfg["model"], cfg["settings"])
        already_cached = cache_path.exists()
        planned.append((vname, cfg, already_cached))

    fresh_count = sum(1 for _, _, c in planned if not c)
    estimated_chars = char_len * fresh_count

    print(f"voice_ab — '{text[:60]}{'...' if len(text) > 60 else ''}'")
    print(f"  voice_id     {voice_id}")
    print(f"  variants     {len(variant_names)} "
          f"({fresh_count} need rendering, "
          f"{len(variant_names) - fresh_count} cached)")
    print(f"  char cost    ~{estimated_chars} (text len={char_len})")
    print(f"  output dir   {_OUT_DIR}")
    print()

    if args.dry_run:
        print("[dry-run] no renders issued")
        return 0

    if not os.environ.get("ELEVENLABS_API_KEY"):
        print("error: ELEVENLABS_API_KEY not set", file=sys.stderr)
        return 1

    results = []
    for vname, cfg, was_cached in planned:
        t0 = time.perf_counter()
        try:
            cache_path, was_fresh = _render_one(
                text, voice_id,
                model=cfg["model"], settings=cfg["settings"])
        except SystemExit as e:
            print(f"  [{vname}] FAIL: {e}")
            results.append({"variant": vname, "status": "fail",
                            "error": str(e)})
            continue
        elapsed = (time.perf_counter() - t0) * 1000

        out_path = _OUT_DIR / f"{label}__{vname}.mp3"
        out_path.write_bytes(cache_path.read_bytes())
        size = out_path.stat().st_size
        status = "cached" if (was_cached or not was_fresh) else "rendered"
        print(f"  [{vname:24}] {status:8}  "
              f"{size:6}B  {elapsed:6.0f}ms  -> {out_path.name}")
        results.append({
            "variant": vname,
            "status": status,
            "path": str(out_path),
            "bytes": size,
            "elapsed_ms": round(elapsed, 1),
            "model": cfg["model"],
            "settings": cfg["settings"],
        })

    print()
    summary = {
        "text": text,
        "voice_id": voice_id,
        "label": label,
        "char_cost_estimated": estimated_chars,
        "results": results,
    }
    summary_path = _OUT_DIR / f"{label}__summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary written -> {summary_path}")
    print()
    print("listen to each MP3 and pick a winner. To apply: edit "
          "tts_voice_settings + tts_prewarm_model in the tenant YAML.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
