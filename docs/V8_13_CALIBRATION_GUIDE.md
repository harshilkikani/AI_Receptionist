# V8.13 — Voice Calibration Workflow

The agent cannot listen to audio. Every prosody decision in V8.5 →
V8.12 was theoretical, and the accumulation produced uncanny-valley
artifacts ("haaai its Joanna..."). V8.13 walks the loop back to a
human ear in the seat: the operator listens, picks a winner, applies
it. This doc is the playbook for doing that without burning the
ElevenLabs character budget.

## When to run a calibration cycle

Run this loop when one of these is true:

  * You've heard a real call (live demo, recording) and something
    sounded off — too slow, too dramatic, too flat, too synthetic.
  * You're about to change `tts_voice_settings` or
    `tts_prewarm_model` in a tenant YAML, and you don't already have
    A/B audio files in `data/voice_ab/` for the new combo.

If neither is true, skip this — the cache is warm, calls work, leave
it alone.

## Cost budget at a glance

ElevenLabs starter tier is 30,000 chars/month. A full prewarm for one
tenant is ~2,000 chars. A `voice_ab` run on a single phrase across
four variants is ~4 × phrase_length, usually 100–300 chars. The math
works out to roughly 100 single-phrase A/B comparisons per month
before you start eating into prewarm headroom.

Always run `python -m src.preflight --ping` before and after a
calibration cycle to see actual usage.

## The loop

### 1. Establish a baseline

If `data/voice_ab/` is empty for the phrase you care about, render
the current production settings first so you have something to
compare against.

```
python scripts/voice_ab.py "Ace HVAC & Plumbing, this is Joanna. What's going on?" \
       --client ace_hvac --variants turbo-default
```

This puts `ace-hvac-plumbing-this-is-joanna__turbo-default.mp3` in
`data/voice_ab/`. Listen. Decide whether you're trying to make it
faster, slower, less performative, more grounded, etc.

### 2. Render the candidate variants

```
python scripts/voice_ab.py "..." --client ace_hvac
```

Default variant set is `turbo-default, flash-default, turbo-steady,
turbo-lively`. That's four MP3s for ~4 × phrase_length chars (most
short greetings = ~200 chars total). If you want a different mix:

```
python scripts/voice_ab.py "..." --variants turbo-default,turbo-steady
```

Run `python scripts/voice_ab.py --list` for the full catalog.

### 3. Listen and rank

Open the MP3s in `data/voice_ab/` side by side. Things to listen for
on a phone call specifically:

  * **Onset clarity** — does the first word land cleanly, or does it
    smear into a soft "haaai" / "hhhmm"?
  * **Phone-call timbre** — does it sound like a person talking into
    a headset, or like a podcast voiceover?
  * **Sentence shape** — does it rise/fall like real speech, or does
    every sentence land with the same arc?
  * **Naturalness of the pause** — short ack ("Mhm,") shouldn't sound
    sing-song.
  * **Consistency** — across different phrases, does it sound like
    the same person?

The winner is the one a real customer wouldn't immediately clock as
synthetic. Not the one that sounds the most expressive.

### 4. Apply the winner

Edit `clients/ace_hvac.yaml`:

```yaml
tts_prewarm_model: eleven_turbo_v2_5     # change if a different model won
tts_voice_settings:                       # add ONLY if non-default
  stability: 0.65
  similarity: 0.75
```

If the winner was `turbo-default`, you don't need a
`tts_voice_settings` block at all — ElevenLabs defaults are the same
as omitting the block.

### 5. Invalidate the stale cache for affected phrases

The hash key includes model + settings, so changing them in the YAML
automatically invalidates everything on the next prewarm — BUT the
old MP3s stick around on disk until age-based eviction (30 days) or
size cap (500MB) sweeps them. That's fine; they just don't get
served.

If you want to re-render a specific greeting *now* (e.g., to verify
on a live call before doing a full prewarm), invalidate that one:

```
python -m src.audio_cache invalidate ace_hvac "Ace HVAC & Plumbing, this is Joanna. What's going on?"
```

Then the next call that hits that phrase renders fresh under the new
settings.

### 6. Re-prewarm (only if many phrases changed)

If you changed `tts_voice_settings` for the whole tenant, the entire
cache is now stale-by-hash. The next time the demo runs prewarm
(`main.py` startup) it'll re-render everything — ~2000 chars.

To force the re-prewarm immediately rather than wait for next
startup:

```
python -m src.audio_cache prewarm ace_hvac
```

Watch the output: `rendered` should match the previous prewarm count
exactly. `errors` should be 0. If `skipped > 0`, something's
fallback-pathing — check `ELEVENLABS_API_KEY` and quota.

### 7. Confirm via a real call

Theoretical validation ends here. Call **+1 (844) 940-3274** and
listen to the actual greeting + a few turns. If it still feels off,
go back to step 2 with a different variant.

## What NOT to do

  * **Don't run a full prewarm to test one phrase.** Use `voice_ab`
    on the specific phrase, listen, decide, then apply.
  * **Don't change multiple variables at once.** If you're testing
    stability, hold model and other settings constant. The variant
    catalog enforces this.
  * **Don't add settings to the YAML without an A/B that justifies
    them.** Every overshape layer (V8.5 style, V8.10a speaker_boost,
    V8.10a Multilingual) was theoretical and they stacked into the
    uncanny valley. If you can't point to two MP3s in
    `data/voice_ab/` where setting X clearly improved the result,
    don't set X.
  * **Don't aggressively invalidate the cache.** `evict_if_needed`
    handles aging. Use `invalidate_text` for surgical re-renders, not
    `rm data/audio/*.mp3`.
  * **Don't burn quota chasing tiny perceptual gains.** If two
    variants sound roughly the same after a careful listen, they ARE
    the same to a caller. Pick the cheaper one (Flash < Turbo <
    Multilingual on credit cost).

## Quota guardrails

  * Preflight (`python -m src.preflight --ping`) fails when quota is
    exhausted. If you see `[FAIL] ELEVENLABS_API_KEY ... EXHAUSTED`,
    every render in the demo is silently falling back to Polly — the
    voice you hear is NOT what you configured.
  * Preflight warns at ≥90% so you have a buffer before exhaustion.
  * The `tts_provider: elevenlabs` tenants check a per-tenant monthly
    cap (`plan.elevenlabs_monthly_cap_chars`) before each FRESH
    render. Cache hits are always free. Set the cap on a tenant if
    you want to bound spending per customer.

## Files referenced

  * `scripts/voice_ab.py` — A/B render tool. Built-in variant
    catalog. `--list` shows them.
  * `src/audio_cache.py` — `invalidate_text(client, text)`,
    `prewarm_for_tenant(client)`. Also has `_cli` for shell access.
  * `src/preflight.py` — `--ping` includes ElevenLabs quota fetch.
  * `clients/ace_hvac.yaml` — the tenant config. Voice config lives
    in `tts_voice_id`, `tts_provider`, `tts_prewarm_model`,
    `tts_runtime_model`, and (optional) `tts_voice_settings`.
