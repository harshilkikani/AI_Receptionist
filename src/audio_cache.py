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
import threading
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
#
# V10.0 — pool expanded 8 → 16. The pre-V10 set cycled visibly across
# any call of more than a few turns; the audit showed the same 4-5
# fillers dominating. Doubling the pool plus the per-call no-repeat-
# within-3 memory in filler_payload_for() means a caller is unlikely to
# hear the same filler twice in a normal conversation.
PREWARM_FILLERS: tuple = (
    "Mhm,",
    "Okay,",
    "Yeah,",
    "Right,",
    "Lemme see —",
    "One sec —",
    "Okay so —",
    "Sure thing,",
    # V10.0 additions — chosen to avoid the AI-cheer category, no
    # exclamation marks, no "perfect"/"absolutely"/"gotcha", all
    # match the natural shape of a real receptionist's between-turn
    # micro-fillers.
    "Hm,",
    "Yeah so —",
    "Alright,",
    "Got it,",
    "Hold on —",
    "Okay yeah,",
    "Yep,",
    "Right so —",
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


# V10.0 — per-call short-term memory of recent filler picks. The 8-pool
# in pre-V10 cycled visibly within a few turns; with a 16-pool and a
# 3-turn no-repeat memory, the same filler shouldn't fire twice in a
# normal conversation. Keyed by call_sid, bounded so memory doesn't
# leak across long-running processes.
_FILLER_HISTORY: dict = {}
_FILLER_HISTORY_MAX = 256          # bounded LRU on the call_sid keyspace
_FILLER_NO_REPEAT_WITHIN = 3       # don't reuse within the last N picks
_filler_lock = threading.Lock() if False else None  # cheap import-time stub
# (filler_payload_for runs in a hot loop on the voice path; locking is
# unnecessary because each Twilio call has its own call_sid and concurrent
# turns within one call are sequential by Twilio design.)


def _remember_filler(call_sid: str, text: str) -> None:
    """Record the just-served filler so the next call avoids repeating
    it within _FILLER_NO_REPEAT_WITHIN picks."""
    if not call_sid:
        return
    hist = _FILLER_HISTORY.get(call_sid)
    if hist is None:
        # Bound the global keyspace so we don't grow forever.
        if len(_FILLER_HISTORY) >= _FILLER_HISTORY_MAX:
            try:
                _FILLER_HISTORY.pop(next(iter(_FILLER_HISTORY)))
            except StopIteration:
                pass
        hist = []
        _FILLER_HISTORY[call_sid] = hist
    hist.append(text)
    if len(hist) > _FILLER_NO_REPEAT_WITHIN:
        del hist[0]


def _recent_fillers(call_sid: str) -> list:
    if not call_sid:
        return []
    return list(_FILLER_HISTORY.get(call_sid, []))


def filler_payload_for(client: Optional[dict], *,
                        rng=None, call_sid: str = ""):
    """V8.9b — return a TtsPayload for a randomly-picked endpointing
    filler. Only succeeds if the tenant is on a play-capable provider
    AND the filler is already cached (we MUST NOT trigger a live
    render on the critical path — that would defeat the whole point).
    Returns None when no cached filler is available; callers fall
    through to the synchronous path.

    V10.0 — when `call_sid` is provided, skips fillers played in the
    last _FILLER_NO_REPEAT_WITHIN turns of this same call. The same
    filler shouldn't fire twice back-to-back; a caller hearing
    "Mhm, … Mhm, … Mhm, …" three turns running is a giveaway."""
    import random
    if not _is_elevenlabs_tenant(client):
        return None
    r = rng if rng is not None else random
    # Try each filler in a shuffled order; return the first that has a
    # cached file on disk. Lazy import avoids a cycle with src.tts.
    from src import tts as _tts
    recent = _recent_fillers(call_sid)
    candidates = [f for f in PREWARM_FILLERS if f not in recent]
    if not candidates:
        # Memory holds the entire pool — clear oldest and try again.
        # Should be extremely rare with a 16-pool + window=3, but the
        # fallback keeps the voice path responsive.
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
            _remember_filler(call_sid, text)
            return _tts.TtsPayload(
                kind="play",
                url=f"{base}/audio/{h}.mp3",
                duration_estimate_ms=int(len(text) * 60),
            )
    return None


def invalidate_text(client: dict, text: str, *,
                    runtime: bool = True,
                    prewarm: bool = True) -> dict:
    """V8.13 — drop the cached audio for ONE phrase under this client's
    voice config. Lets the operator re-render a single greeting after a
    voice-settings tweak without flushing the whole cache (which would
    burn the next full prewarm against the ElevenLabs budget).

    Both the prewarm-model variant and the runtime-model variant are
    invalidated by default — they hash to different files but the
    operator usually wants both re-rendered (the prewarm path covers
    greetings/acks/fillers; the runtime path covers fresh LLM turns).

    Returns {"removed": [hash, ...], "missing": [hash, ...]}.

    Safe to call when the audio dir doesn't exist — returns empty.
    """
    out = {"removed": [], "missing": []}
    if not _is_elevenlabs_tenant(client) or not (text or "").strip():
        return out
    from src import tts as _tts
    voice_id = _tts.voice_id_for(client) or ""
    settings = _tts.voice_settings_for(client)
    models = []
    if prewarm:
        m = _tts.model_for(client, prewarm=True)
        if m:
            models.append(m)
    if runtime:
        m = _tts.model_for(client, prewarm=False)
        if m and m not in models:
            models.append(m)
    for model in models:
        h = _tts._hash_key(text, voice_id, "elevenlabs",
                            model=model, settings=settings)
        path = _AUDIO_DIR / f"{h}.mp3"
        if path.exists():
            try:
                path.unlink()
                out["removed"].append(h)
            except OSError as e:
                log.warning("invalidate_text unlink %s failed: %s",
                            h, e)
        else:
            out["missing"].append(h)
    return out


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


# ── CLI ───────────────────────────────────────────────────────────────
#
# Small operator-facing entrypoint for V8.13 voice tuning. Lets the
# operator invalidate one phrase, run stats, or trigger a prewarm
# without writing a Python one-liner each time.
#
#   python -m src.audio_cache stats
#   python -m src.audio_cache invalidate ace_hvac "the phrase"
#   python -m src.audio_cache prewarm ace_hvac

def _cli(argv: Optional[list] = None) -> int:
    import sys
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help"):
        print("usage: python -m src.audio_cache <command> [args]")
        print("  stats")
        print("  invalidate <client_id> <text>")
        print("  prewarm <client_id>")
        return 0
    cmd = args[0]
    if cmd == "stats":
        s = cache_stats()
        print(f"file_count   {s['file_count']}")
        print(f"total_bytes  {s['total_bytes']} "
              f"({s['total_bytes'] / 1024:.1f} KiB)")
        return 0
    if cmd == "invalidate":
        if len(args) < 3:
            print("usage: invalidate <client_id> <text>")
            return 2
        from src import tenant
        client = tenant.load_client_by_id(args[1])
        if not client:
            print(f"unknown client: {args[1]}")
            return 2
        r = invalidate_text(client, args[2])
        print(f"removed: {len(r['removed'])} ({r['removed']})")
        print(f"missing: {len(r['missing'])} ({r['missing']})")
        return 0
    if cmd == "prewarm":
        if len(args) < 2:
            print("usage: prewarm <client_id>")
            return 2
        from src import tenant
        client = tenant.load_client_by_id(args[1])
        if not client:
            print(f"unknown client: {args[1]}")
            return 2
        r = prewarm_for_tenant(client)
        print(f"prewarm result: {r}")
        return 0
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
