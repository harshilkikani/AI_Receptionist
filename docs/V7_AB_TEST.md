# V7 Voice Quality A/B Test

How to evaluate the v7 voice changes against earlier versions, with a
scoring rubric specific enough that two operators land on the same
ship/no-ship decision.

## Three test points

| Point | What's running | Run before |
|---|---|---|
| **A — Baseline (pre-V7.1)** | Polly Joanna Neural, static greeting | Today (v6.0 tip) |
| **B — ElevenLabs only (post-V7.1)** | Real ElevenLabs voice + V7.2 disfluency + V7.3 contextual greeting | After flipping `tts_provider: elevenlabs` on the live tenant + filling `ELEVENLABS_API_KEY` |
| **C — Speech-to-speech (post-V7.4)** | Twilio Media Streams + ElevenLabs Conversational | After the transport rewrite |

You only need point A and at least one of B/C to make a ship decision.

## Recording the calls

The repo already records every call when `record_calls: true` is on
the tenant (v4.5). For the live demo:

```bash
# Confirm recording flag (one-time)
grep record_calls clients/ace_hvac.yaml
# If absent or false, set it to true and restart uvicorn.

# Make the call
# (call +1 (844) 940-3274 from your phone)

# Find the recording in admin
python -m src.client_portal issue ace_hvac   # if you need a portal token
# or browse /admin/calls in the admin dashboard
```

Each call's recording appears at `/admin/recording/{call_sid}.mp3` once
Twilio's `/voice/recording` callback fires (usually 30-60 s after hang
up). Download the mp3, save under a name like `voice-test-A-<sid>.mp3`,
`voice-test-B-<sid>.mp3`, `voice-test-C-<sid>.mp3`.

## Standard test script

Same words, same pace, same problem on every call so the only variable
is the AI. Read it from this page; don't improvise.

```
Caller: "Yeah, hi — my AC quit working last night and the house is
         getting hot. Can you send somebody out today?"

Caller: "I'm at 412 Maple Street, apartment B. What's it gonna run me?"

Caller: "Hmm, okay. Yeah let's get it on the calendar. My number's
         the one I'm calling from. Talk soon."

Caller:  *hangs up*
```

Three turns is enough to surface: greeting, address parsing, price
quoting (or grounding-mediated decline), wrap-up phrasing.

## Scoring rubric

Each dimension scored 1–5 (1 = robotic / bad / broken, 5 = production-
quality). Average across dimensions. Ship to a paying client when
average ≥ 4.0.

### 1. Latency — first-word time after the caller stops speaking

| Score | Description |
|---|---|
| 1 | > 4 s. Awkward silence; caller assumes line dropped. |
| 2 | 2.5–4 s. Noticeable lag. |
| 3 | 1.5–2.5 s. Tolerable but feels like a phone tree. |
| 4 | 800 ms – 1.5 s. Reads as "thinking briefly" — fine. |
| 5 | < 800 ms. Indistinguishable from a human pause. |

Method: stopwatch from end of your last word to first audible AI
syllable. Phone speakers add ~100 ms — not enough to matter.

### 2. Voice naturalness — does it sound like a person?

| Score | Description |
|---|---|
| 1 | Obvious TTS — uncanny inflection, robotic vowels. |
| 2 | Solid TTS but clearly not human (Polly Joanna falls here). |
| 3 | Good TTS — believable for 1–2 sentences, then the pattern reveals. |
| 4 | Could pass for human in casual listening; gives up only on edge cases. |
| 5 | Indistinguishable from a real receptionist. |

### 3. Conversational flow — disfluency, pauses, turn-taking

| Score | Description |
|---|---|
| 1 | Speaks in complete templated sentences; no fillers, no thinking sounds. |
| 2 | Some variety but still feels scripted. |
| 3 | Reads naturally most of the time, occasional clunky transitions. |
| 4 | Drops fillers ("hmm", "lemme see") at the right moments; takes natural pauses. |
| 5 | Could interrupt and be interrupted naturally; turn-taking matches a real call. |

V7.2 should bump this 2 → 3-ish on B; V7.4 should land it at 4-5 on C.

### 4. Accuracy — does it get the facts right?

| Score | Description |
|---|---|
| 1 | Made up a price or service detail (this is a hard fail — V4.4 grounding should prevent it). |
| 2 | Confused about basic details (hours, services). |
| 3 | Correct on the basics, vague on edge cases. |
| 4 | Tenant-specific info accurate (price quoted matches yaml, address recorded). |
| 5 | Both accurate AND demonstrates context (refers to KB sections, prior calls). |

### 5. Recovery — what happens when something goes wrong?

| Score | Description |
|---|---|
| 1 | Caller hears "We are sorry, an application error has occurred" or dead air. |
| 2 | Hard hangup with no explanation. |
| 3 | Polite "let me get someone" + hangup, but doesn't actually escalate. |
| 4 | V6.2 failsafe TwiML or a graceful "let me check and call you back". |
| 5 | Transfers to escalation_phone, owner gets SMS, caller stays informed. |

Test this by intentionally tripping the LLM (use the failsafe test:
temporarily set `ANTHROPIC_API_KEY=invalid` and place a call). Should
score ≥ 4 with V6.2 in place.

### 6. Hangup feel — does the call end naturally?

| Score | Description |
|---|---|
| 1 | Abrupt cut-off, no goodbye. |
| 2 | "Goodbye." (one word) then hangup. |
| 3 | Generic closer ("Have a great day."). |
| 4 | Contextual closer ("I'll have John call you in the hour. Talk soon."). |
| 5 | Same as 4 + appropriate energy match to the call (calmer if emergency, brighter if scheduling). |

## Scoring sheet

```
Test point: ____  Call SID: __________________
Date/time:  ____________________________________________

Latency:         [1] [2] [3] [4] [5]
Naturalness:     [1] [2] [3] [4] [5]
Flow:            [1] [2] [3] [4] [5]
Accuracy:        [1] [2] [3] [4] [5]
Recovery:        [1] [2] [3] [4] [5]
Hangup:          [1] [2] [3] [4] [5]
                 ─────────────────
AVERAGE:         _____
```

Ship when average ≥ 4.0 across two independent listeners.

## Comparison table (fill in after testing)

| Dimension | A (baseline) | B (V7.1+V7.2+V7.3) | C (V7.4 streams) |
|---|---|---|---|
| Latency | | | |
| Naturalness | | | |
| Flow | | | |
| Accuracy | | | |
| Recovery | | | |
| Hangup | | | |
| **Average** | | | |

## Why this matters

The v4-v6 work was infrastructure. Until you do an A/B with the
rubric above, "the voice feels different" is a vibe, not a metric.
The rubric makes the upgrade auditable and gives you a clear bar to
clear before a paying client hears it.

Run the test today (point A). Run it again after V7.1 ships (point B).
Compare row-by-row. That's how you know what to charge for.
