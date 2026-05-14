"""V5.6 — audio cache pre-warm + bounded LRU eviction.

ElevenLabs renders cost real money + take 1-3s. Cache hits cost nothing
and serve in <50ms. Two efficiencies on top of `src.tts`:

  1. Pre-warm common phrases (greeting per language, force-end goodbye,
     degraded fallbacks) for every elevenlabs tenant at startup so the
     first caller hears the upgraded voice instead of Polly fallback.

  2. Bound the cache. data/audio/ would otherwise grow forever — every
     unique LLM response gets a fresh hash. Two-pass eviction:
       a) drop files older than max_age_days (default 30)
       b) if total still over cap_mb (default 500), drop oldest by mtime

Both run from main.py's lifespan; eviction is safe to re-run any time.

Polly tenants are skipped — Twilio Polly synthesizes server-side, no
local audio is generated, no pre-warm needed.

The pre-warm phrase list mirrors `main._greeting_for`, the force-end
goodbye in `main.voice_gather`, and `llm._DEGRADED_PHRASES`. Keep them
in sync if you change copy in those modules.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("audio_cache")

_AUDIO_DIR = Path(__file__).parent.parent / "data" / "audio"


# Mirror llm._DEGRADED_PHRASES so the canned responses fired on Anthropic
# hiccups never miss the cache. Edit both lists together.
PREWARM_DEGRADED_PHRASES: tuple = (
    "Hang on one second— lot of calls coming in right now, bear with me.",
    "Sorry, give me a quick beat— I'll be right with you.",
    "Hang on, bad connection on my end— one sec.",
    "Give me a moment, I lost you for a second there.",
    "Let me grab someone real quick— one moment.",
    "One sec, I need to hand you off.",
    "Hang tight, hiccup on my end— give me ten seconds.",
    "Sorry, my system just blipped— hold one second.",
    "One moment, let me check on that.",
    "Gimme one second.",
)


# V8.4 — short acks. Most "Got it." style turns currently re-render
# a near-identical mp3 every time, wasting both latency and credits.
# Pre-warming these means the LLM's pure-ack turns serve from cache
# in <50ms (vs ~1500ms fresh render). The list is intentionally short;
# each entry needs to be a complete plausible turn the LLM might emit.
PREWARM_ACKS: tuple = (
    "Got it.",
    "Okay.",
    "Mhm.",
    "Sure thing.",
    "Yeah, no problem.",
    "Alright.",
    "Sounds good.",
    "Perfect.",
)


# V8.4 — terminal goodbyes that the V8.1 emit-audio paths emit
# verbatim. Pre-warming guarantees the call-end audio plays instantly.
PREWARM_GOODBYES: tuple = (
    "Thanks, we're not interested. Goodbye.",
    "Thanks, we're not taking calls from this number. Goodbye.",
    "No worries— have a good one.",
)


# V8.9b — endpointing fillers. These play IMMEDIATELY after the caller
# stops speaking (≤300ms perceived latency to first audio) while the
# real LLM call runs in parallel in a background thread. By the time
# the filler finishes (~800ms-1.2s of audio), the LLM result is ready
# and the real reply starts. Caller's brain interprets the filler as
# "the AI is thinking" instead of "the AI is dead air."
#
# Vocabulary chosen to:
#   - Sound natural (real receptionists say these between turns)
#   - Survive V4.3 anti_robot.scrub unchanged (already verified for
#     V7.2 disfluency openers — same shape)
#   - Be varied so a caller doesn't hear the same one each turn
#   - Be SHORT enough that the gap-to-substance is small but LONG
#     enough that the LLM has time to finish (~600-1200ms each)
PREWARM_FILLERS: tuple = (
    "Mhm,",
    "Okay,",
    "Yeah,",
    "Right,",
    "Lemme see —",
    "One sec —",
    "Okay so —",
    "Sure thing,",
)


def _greetings_for(client: dict) -> list:
    """V8.10a — render EVERY V7.3 greeting variant
    (language × time-of-day bucket × template index) so the live
    contextual greeting in main._greeting_for always hits cache.

    Before V8.10a: only 4 legacy strings ("Hey, this is Joanna from
    {company}— what's going on?" and its translations) — but greeting.
    greeting_for emits time-of-day variants that often DON'T match
    those, so the live greeting was rendered fresh on most calls.

    Falls back to the legacy 4-string set if importing greeting fails
    (e.g. zoneinfo unavailable in a stripped Python build).
    """
    company = (client or {}).get("name") or "the office"
    try:
        from src.greeting import _TEMPLATES_BY_LANG
    except Exception:
        return [
            f"Hey, this is Joanna from {company}— what's going on?",
            f"Hola, habla Joanna de {company}— en que te puedo ayudar?",
            f"Hey, main Joanna, {company} se— kya hua batao?",
            f"Hey, hu Joanna, {company} thi— shu thayum kahejo?",
        ]
    out = []
    seen = set()
    for lang_templates in _TEMPLATES_BY_LANG.values():
        for bucket_templates in lang_templates.values():
            for tpl in bucket_templates:
                rendered = tpl.format(company=company)
                if rendered not in seen:
                    seen.add(rendered)
                    out.append(rendered)
    # V8.10a — also prewarm the recall-aware greeting variants.
    # These have no caller-specific fields (just {company}) so we can
    # render them once per tenant. _NAMED_RETURN_EN is NOT prewarmed
    # because it requires the caller's first name.
    try:
        from src.greeting import _RECALL_EN
        for tpl in _RECALL_EN:
            rendered = tpl.format(company=company, first="")
            if rendered not in seen:
                seen.add(rendered)
                out.append(rendered)
    except Exception:
        pass
    return out


def _force_end_for(client: dict) -> str:
    """Mirror main.voice_gather force_end goodbye."""
    owner = (client or {}).get("owner_name") or "the owner"
    return f"Okay— {owner} will call you back within the hour. Talk soon."


def _is_elevenlabs_tenant(client: Optional[dict]) -> bool:
    if not client:
        return False
    return ((client.get("tts_provider") or "polly").lower().strip()
            == "elevenlabs")


def prewarm_for_tenant(client: dict) -> dict:
    """Pre-render every common phrase for one tenant. No-op for non-
    elevenlabs tenants. Returns
    {"rendered": int, "skipped": int, "errors": int} where:
      - rendered = audio file now exists
      - skipped = render didn't produce a play-style payload (e.g.
                  PUBLIC_BASE_URL unset, or fall-back to polly)
      - errors  = render raised
    """
    from src import tts as _tts
    out = {"rendered": 0, "skipped": 0, "errors": 0}
    if not _is_elevenlabs_tenant(client):
        return out
    phrases = (_greetings_for(client)
               + [_force_end_for(client)]
               + list(PREWARM_DEGRADED_PHRASES)
               + list(PREWARM_ACKS)         # V8.4 — instant ack hits
               + list(PREWARM_GOODBYES)     # V8.4 — terminal goodbyes
               + list(PREWARM_FILLERS))     # V8.9b — endpointing fillers
    for text in phrases:
        try:
            # V8.10a — prewarm=True routes through the slower /
            # prosody-rich tts_prewarm_model (multilingual_v2 by default).
            # Cached audio renders ONCE; the latency cost is paid at
            # startup, but every subsequent play is a <50ms cache hit.
            payload = _tts.render(text, client=client, prewarm=True)
            if payload.kind == "play" and payload.url:
                out["rendered"] += 1
            else:
                out["skipped"] += 1
        except Exception as e:
            out["errors"] += 1
            log.warning("prewarm failed for %r: %s", text[:48], e)
    return out


def prewarm_all() -> dict:
    """Run prewarm for every long-lived tenant. Skips templates
    (`_default`, `_template`) and short-lived demo tenants
    (`demo_*`). Returns aggregate counts plus per-tenant detail."""
    from src import tenant as _tenant
    total = {"rendered": 0, "skipped": 0, "errors": 0,
             "tenants_prewarmed": 0, "tenants_skipped": 0}
    detail = {}
    for entry in _tenant.list_all():
        cid = (entry.get("id") or "").strip()
        if not cid or cid.startswith("_") or cid.startswith("demo_"):
            continue
        if not _is_elevenlabs_tenant(entry):
            total["tenants_skipped"] += 1
            continue
        r = prewarm_for_tenant(entry)
        detail[cid] = r
        total["tenants_prewarmed"] += 1
        for k in ("rendered", "skipped", "errors"):
            total[k] += r[k]
    total["detail"] = detail
    return total


# ── Eviction ────────────────────────────────────────────────────────────

DEFAULT_MAX_AGE_DAYS = 30
DEFAULT_MAX_TOTAL_MB = 500


def evict_if_needed(max_age_days: int = DEFAULT_MAX_AGE_DAYS,
                    max_total_mb: int = DEFAULT_MAX_TOTAL_MB,
                    now: Optional[float] = None) -> dict:
    """Two-pass cleanup of data/audio/.
       Pass 1: drop files with mtime older than max_age_days.
       Pass 2: if total size still over max_total_mb, drop oldest by
               mtime until under.
    Returns
      {"evicted_age": n, "evicted_size": n, "kept": n, "bytes_freed": n}.
    Safe to re-run; missing directory returns zeros."""
    if not _AUDIO_DIR.exists():
        return {"evicted_age": 0, "evicted_size": 0,
                "kept": 0, "bytes_freed": 0}
    now = now if now is not None else time.time()
    cutoff = now - (max_age_days * 86400)
    cap_bytes = max_total_mb * 1024 * 1024

    files = []
    for p in _AUDIO_DIR.iterdir():
        if not p.is_file() or not p.name.endswith(".mp3"):
            continue
        try:
            st = p.stat()
            files.append([p, st.st_mtime, st.st_size])
        except OSError:
            continue

    evicted_age = 0
    bytes_freed = 0
    survivors = []
    for entry in files:
        p, mtime, size = entry
        if mtime < cutoff:
            try:
                p.unlink()
                evicted_age += 1
                bytes_freed += size
            except OSError as e:
                log.warning("evict_age unlink failed %s: %s", p.name, e)
        else:
            survivors.append(entry)

    total_size = sum(e[2] for e in survivors)
    evicted_size = 0
    if total_size > cap_bytes:
        survivors.sort(key=lambda t: t[1])  # oldest mtime first
        i = 0
        while total_size > cap_bytes and i < len(survivors):
            p, mtime, size = survivors[i]
            try:
                p.unlink()
                evicted_size += 1
                bytes_freed += size
                total_size -= size
            except OSError as e:
                log.warning("evict_size unlink failed %s: %s", p.name, e)
            i += 1
        survivors = survivors[i:]

    return {
        "evicted_age": evicted_age,
        "evicted_size": evicted_size,
        "kept": len(survivors),
        "bytes_freed": bytes_freed,
    }


def filler_payload_for(client: Optional[dict], *,
                        rng=None):
    """V8.9b — return a TtsPayload for a randomly-picked endpointing
    filler. Only succeeds if the tenant is on a play-capable provider
    AND the filler is already cached (we MUST NOT trigger a live
    render on the critical path — that would defeat the whole point).
    Returns None when no cached filler is available; callers fall
    through to the synchronous path."""
    import random
    if not _is_elevenlabs_tenant(client):
        return None
    r = rng if rng is not None else random
    # Try each filler in a shuffled order; return the first that has a
    # cached file on disk. Lazy import avoids a cycle with src.tts.
    from src import tts as _tts
    candidates = list(PREWARM_FILLERS)
    r.shuffle(candidates)
    voice_id = _tts.voice_id_for(client) or ""
    # V8.10a/V8.12.5 — fillers are rendered with the PREWARM model
    # and the tenant's voice_settings; the cache lookup must hash with
    # the same parameters or it'll miss.
    prewarm_model = _tts.model_for(client, prewarm=True)
    voice_settings = _tts.voice_settings_for(client)
    for text in candidates:
        h = _tts._hash_key(text, voice_id, "elevenlabs",
                            model=prewarm_model,
                            settings=voice_settings)
        cached = _AUDIO_DIR / f"{h}.mp3"
        if cached.exists():
            # Build the play URL via the same resolver used in render
            base = _tts._public_base_url()
            if not base:
                return None
            return _tts.TtsPayload(
                kind="play",
                url=f"{base}/audio/{h}.mp3",
                duration_estimate_ms=int(len(text) * 60),
            )
    return None


def cache_stats() -> dict:
    """Quick disk-usage snapshot — used by /admin and tests."""
    if not _AUDIO_DIR.exists():
        return {"file_count": 0, "total_bytes": 0}
    total = 0
    n = 0
    for p in _AUDIO_DIR.iterdir():
        if not p.is_file() or not p.name.endswith(".mp3"):
            continue
        try:
            total += p.stat().st_size
            n += 1
        except OSError:
            continue
    return {"file_count": n, "total_bytes": total}
