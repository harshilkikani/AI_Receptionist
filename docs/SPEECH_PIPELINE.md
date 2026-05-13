# Speech Pipeline — architectural map

This document is the single reference for how a caller's utterance
becomes our audio response, layer by layer. Read this BEFORE editing
anything in `main.py::_run_pipeline`, `src/tts.py`, or `src/audio_cache.py`.

Last updated: V8.11.

---

## High-level flow

```
Twilio webhook
    │
    ▼
/voice/incoming or /voice/setlang or /voice/gather
    │
    ├─ first-encounter checks (TwilioSignature, RequestID, rate limit)
    │
    ├─ if /voice/gather:
    │     ├─ V8.9a empty-speech handling (actionOnEmptyResult retries)
    │     ├─ V3.x spam filter
    │     ├─ V3.A call duration cap
    │     │
    │     └─ V8.9b endpointing-filler dispatch (if enabled):
    │           1. spawn _think_worker thread → _run_pipeline
    │           2. emit TwiML <Play filler.mp3><Redirect /voice/respond>
    │           Twilio plays cached "Mhm —" while LLM runs in parallel
    │
    ▼
_run_pipeline (sync or background thread)
    │
    ├─ V4.7  recall.build_recall_block       → prompt context
    ├─        llm.chat_with_usage             → Claude reply (structured JSON)
    ├─ V4.3  anti_robot.scrub                → strip corporate-speak
    ├─ V4.4  grounding.verify_reply          → replace invented prices
    ├─ V8.3  emergency-keyword guard         → downgrade unfounded high-priority
    ├─ V7.2  disfluency.add_disfluency       → sentence-initial filler injection
    │        (V8.11.2: skipped when V8.9b endpointing is enabled)
    │
    └─ returns dict {reply, intent, priority, sentiment, caller}
        │
        ▼
_emit_pipeline_result
    │
    ├─ V3.7  sentiment_tracker → escalation if frustrated for N turns
    ├─        emergency routing (V8.1: ElevenLabs throughout) or normal
    │
    ▼
_respond (normal path) or _emit_audio (terminal paths)
    │
    ├─ V4.2  humanize_speech.humanize_for_speech
    │        (numbers/prices/phones/times spelled out for spoken delivery)
    │
    └─ tts.render(text, client, prewarm=False)
        │
        ▼
ElevenLabsProvider.render
    │
    ├─ V8.10a model_for(client) → tts_runtime_model (eleven_flash_v2_5)
    ├─ V8.10a _hash_key includes model         → per-model cache files
    ├─ Cache hit?  → return /audio/<hash>.mp3 URL (~5ms)
    ├─ Cap check (V5.8 plan.elevenlabs_monthly_cap_chars)
    └─ Cache miss → V5.7 _fetch_elevenlabs
                    POST /v1/text-to-speech/{voice_id}/stream
                    with model_id, stability, similarity_boost,
                    style (V8.5), use_speaker_boost (V8.5),
                    output_format=mp3_22050_32 (V5.7)
        │
        └─ V3.3 voice_style.apply_ssml (Polly fallback only — ElevenLabs
                                         ignores SSML markup)
    ▼
TwiML response → Twilio plays audio to caller
```

---

## Layer reference

Each layer below: **what it does**, **when introduced**, **applies to which provider**, **status today**.

### V3.3 — voice_style.apply_ssml
- **What:** Wraps Polly TTS text in SSML for prosody (`<prosody rate="...">`).
- **Introduced:** v3.0 voice quality pass.
- **Applies to:** Polly fallback path only. ElevenLabs ignores SSML markup.
- **Status:** **Keep as fallback decoration.** Never fires on the
  ElevenLabs happy path. Removing it would lose Polly prosody when
  ElevenLabs is unavailable.

### V4.2 — humanize_speech
- **What:** Converts written numbers/prices/phones/times/addresses into
  spelled-out forms ("$475" → "four hundred seventy-five dollars",
  "8am" → "eight A M", "555-1234" → "five five five, one two three four").
- **Introduced:** v4.2 voice quality pass.
- **Applies to:** Both Polly and ElevenLabs paths.
- **Status:** **Keep, with audit deferred to V8.12.** Polly era assumed
  TTS couldn't naturally read these. ElevenLabs Multilingual / Flash
  read most of them well natively. Need A/B listening before deciding
  whether to disable for ElevenLabs tenants — could be a prosody win
  ("$475" pronounced naturally with inflection) or a regression
  (phone numbers might run together).

### V4.3 — anti_robot.scrub
- **What:** Strips corporate-speak phrases ("Certainly,", "I apologize,",
  "Of course,", "How may I assist you today?"). Substitutes some
  ("Certainly," → "sure,").
- **Introduced:** v4.3 voice quality pass.
- **Applies to:** All providers.
- **Status:** **Keep — defense in depth.** V8.3 / V8.11 prompts both
  tell Claude not to use these phrases, but it occasionally slips.
  Lightweight regex; no measurable cost.

### V4.4 — grounding.verify_reply
- **What:** Detects sentences containing dollar amounts the tenant
  never advertised; replaces them with "let me check the exact number —
  {owner}'ll call you back."
- **Introduced:** v4.4 strict grounding.
- **Applies to:** All providers.
- **Status:** **Critical — never touch.** Trust feature. Customer
  hears "$475" expects $475. Without grounding, an LLM hallucination
  could quote $249 → company has to honor a price they never set.

### V4.7 — recall.build_recall_block
- **What:** Pulls the caller's prior calls (V3.4 summary, V4.5 metadata)
  and injects them as a "recent calls" block into the system prompt.
- **Applies to:** Prompt construction, not the speech path itself.
- **Status:** **Keep.** V5.9 TTL cache makes the lookup cheap (~5ms).

### V7.2 — disfluency.add_disfluency
- **What:** Prepends a sentence-initial filler ("Hmm,", "Yeah, so", "Right —")
  to ~15% of LLM replies.
- **Introduced:** v7.2 voice naturalness pass.
- **Applies to:** Pre-TTS text shaping. All providers.
- **Status (V8.11.2):** **Conditional.** Skipped when
  `endpointing_fillers: true` is set on the tenant (V8.9b already
  plays a cached filler between turns — running V7.2 on top creates
  two consecutive fillers per turn). Active for non-endpointing
  tenants where it still provides natural variation.

### V8.1 — ElevenLabs voice consistency
- **What:** New `_emit_audio()` helper used by terminal flows (emergency,
  capped, spam reject, force-end) so they go through `tts.render()`
  instead of bypassing to Polly via `vr.say(...)`.
- **Introduced:** v8.1 voice perception pass.
- **Applies to:** Every terminal path. Voice stays consistent end-to-end.
- **Status:** **Keep — load-bearing.** Without this, the live demo had
  a voice-change-mid-call bug.

### V8.2 — ElevenLabs Flash model default
- **What:** Switched the default ElevenLabs model from
  `eleven_turbo_v2_5` → `eleven_flash_v2_5`.
- **Status (V8.10a):** **Superseded by asymmetric model selection.**
  Flash is now ONLY the default `tts_runtime_model`. Prewarm uses
  `eleven_multilingual_v2`.

### V8.3 — Prompt rewrite + emergency keyword guard
- **What:** Prompt rewritten for brevity (8-15 word target), native
  mid-sentence fillers allowed, narrow emergency criteria. Post-LLM
  guard downgrades `priority=high` when no real emergency keyword is
  in caller speech.
- **Status (V8.11.1):** **Prompt extended with explicit punctuation
  guidance** for live prosody. Emergency guard unchanged.

### V8.4 — Pre-warmed short acks + terminal goodbyes
- **What:** 8 ack phrases + 3 goodbyes added to `audio_cache.prewarm`,
  cached on startup, served instantly when matched.
- **Status:** **Keep.** Cache hits land in <50ms.

### V8.5 — Voice settings tuning for phone
- **What:** ace_hvac `stability` 0.55→0.40, `similarity` 0.80→0.75.
- **Status (V8.10a):** **Extended with `style: 0.30` and
  `use_speaker_boost: true`** for deliberate prosody (honored by
  Multilingual and Turbo; no-op on Flash).

### V8.7 — voice_perf.py benchmark
- **What:** `scripts/voice_perf.py` — signed Twilio call simulation
  through the live tunnel; reports per-turn latency and TwiML shape.
- **Status:** **Keep — primary regression detector.** Re-run after
  every change in this directory.

### V8.8 — Tunnel watchdog
- **What:** `scripts/tunnel_watchdog.py` — wraps reclaim_tunnel with
  a /health ping loop; auto-restart on 2 consecutive failures.
- **Status:** **Keep.** Has saved the live demo twice from stale
  trycloudflare URLs.

### V8.9a — Empty-speech retry budget
- **What:** `actionOnEmptyResult=true` on every Gather. `call_timer`
  tracks consecutive empty SpeechResults. After 2 retries → polite
  end. Coherent speech resets the counter.
- **Status:** **Keep — production-critical.** Closed the "call ends
  on caller pause" bug from live testing.

### V8.9b — Endpointing filler with parallel LLM
- **What:** When `endpointing_fillers: true` on a tenant: /voice/gather
  spawns a `_think_worker` thread, returns `<Play filler.mp3><Redirect
  /voice/respond?t=token>` instantly. /voice/respond pulls the LLM
  result from the token store. Caller hears cached filler in ~300 ms.
- **Status:** **Keep — primary latency mask.** Memory.json atomic
  write + thread-safe lock was added in the same release to keep the
  background-thread pipeline safe.

### V8.10a — Asymmetric model selection
- **What:** `tts_prewarm_model: eleven_multilingual_v2` (slower, more
  prosody-rich, paid once) vs `tts_runtime_model: eleven_flash_v2_5`
  (fast, paid per-render). Hash key includes model so they don't
  collide. Prewarm covers every V7.3 greeting variant + recall
  templates. Background-thread prewarm avoids blocking server boot.
- **Status:** **Keep — current best architecture.**

### V8.11.1 — Prompt prosody guidance
- **What:** Explicit punctuation rules in the system prompt: periods
  to separate facts and questions, one em-dash per reply for the
  conversational pivot, commas inside a single thought only.
- **Applies to:** Every LIVE LLM-generated reply. Latency-neutral
  (no new code, no transformation layer — just better LLM output
  that ElevenLabs naturally paces from the punctuation).
- **Status:** **Keep — current.**

### V8.11.2 — V7.2 conditional disable
- See V7.2 above.

---

## What I deliberately did NOT remove or refactor

The following were CANDIDATES for removal during V8.11 audit but kept:

| Layer | Why kept |
|---|---|
| V3.3 SSML | Polly fallback decoration; harmless on ElevenLabs path. |
| V4.2 humanize_speech | Audit deferred to V8.12 — needs A/B listening to confirm whether ElevenLabs handles numbers better with or without the spell-out. |
| V4.3 anti_robot | Defense-in-depth. Cheap, observable, occasionally fires. |
| V7.2 disfluency | Conditionally disabled (V8.11.2), not removed — still serves non-endpointing tenants. |

## What I'm watching for V8.12

| Concern | Trigger to act |
|---|---|
| V4.2 humanize_speech may flatten ElevenLabs prosody | A/B listen test: same caller speech, render reply with/without humanize. |
| Multiple intent classifications + sentiment tracking + emergency guard overlap | If a future caller is mis-routed despite the guard, audit interaction. |
| V8.9b token store growth on multi-tenant scale | Currently bounded at 1000 entries × 30s TTL. Watch under sustained call load. |
| Prewarm cache size | V5.6 evicts files >30 days OR when total >500MB. Asymmetric models increase per-tenant footprint. Monitor `cache_stats()`. |

## How to read voice quality measurements

`scripts/voice_perf.py` reports per-turn latency. The signals that
matter:

| Metric | Healthy |
|---|---|
| /voice/gather dispatch (endpointing on) | < 200 ms |
| /voice/setlang greeting (cached) | < 200 ms |
| /voice/respond happy path | < 2500 ms (LLM time) |
| TwiML kind | `play (ElevenLabs)` for all turns |
| `render_stats.fallback` | 0 |
| Voice consistency | one provider end-to-end (V8.1 invariant) |

A regression in any of these is a ship-blocker.
