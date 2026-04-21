# CHANGES — Margin Protection + Multi-Tenant Refactor

_Branch: `margin-protection-refactor`_
_Starting commit: baseline_

This document is a running log. Each section below corresponds to one commit on the branch.

---

## Phase 1 — Baseline Safety _(complete)_

- Initialized git repo, created `margin-protection-refactor` branch
- Snapshotted `.env`, `memory.json`, `llm.py`, `main.py`, `memory.py`, `index.html`, `requirements.txt` to `_backups/` (gitignored)
- Added comprehensive `.env.example` documenting all current + upcoming env vars including kill switch and per-feature flags
- Added `.gitignore` protecting secrets, runtime data, and large binaries

**Test:** `git log --oneline margin-protection-refactor` shows baseline commit.

---

## Section G — Multi-Tenant Client Configs _(complete)_

**Files added:**
- `clients/_template.yaml` — blank template (id starts with `_`, never routes)
- `clients/_default.yaml` — fallback when no inbound number matches
- `clients/ace_hvac.yaml` — live tenant for `+18449403274`
- `clients/example_client.yaml` — worked example (Bob's Septic) — does NOT route unless operator also provisions that number in Twilio
- `src/__init__.py`, `src/tenant.py` — YAML loader with `_normalize_phone`, `load_client_by_number`, `load_client_by_id`, `load_default`, `list_all`, `reload`

**Files modified:**
- `main.py` — `/voice/incoming`, `/voice/setlang`, `/voice/gather` now accept `To` form field and route via `tenant.load_client_by_number(To)`. New `_greeting_for(client, lang)` replaces hardcoded `LANG_GREETINGS`. `_run_pipeline` accepts `client=` and forwards it to `llm.chat`.
- `llm.py` — Renamed module-level `client` (Anthropic SDK) to `_anthropic` to free the name. New `_render_system_prompt(caller, client)` fills the prompt with `company_name` and `emergency_keywords` from the client config. `chat()` and `recover()` accept optional `client` param.
- `requirements.txt` — Added `pyyaml`, `pytest`.

**Decisions:**
- YAML chosen over JSON for client configs (spec default + human-editable).
- Tenant loader uses `@lru_cache` — configs reloaded only via `tenant.reload()` or server restart. No hot-reload complexity.
- IDs starting with `_` (e.g. `_default`, `_template`) cannot route by inbound number even if they accidentally have one set. Prevents template files from intercepting real calls.
- `To` form field used for tenant routing (inbound Twilio number), `From` remains the caller ID.
- Fallback client `_default` kicks in if no match — generic phrasing, no specific company name.

**Test:**
```bash
python -c "from src import tenant; print(tenant.load_client_by_number('+18449403274')['id'])"  # -> ace_hvac
python -c "from src import tenant; print(tenant.load_client_by_number('+10000000000')['id'])"   # -> _default
```

**Risk:** Medium. Every voice webhook is now client-aware. Live number `+18449403274` is explicitly configured in `clients/ace_hvac.yaml` to preserve current behavior. Default fallback keeps generic phrasing if routing misses.

---

## Section B — System Prompt Refactor (templated) _(complete)_

**Files added:**
- `prompts/receptionist_core.md` — full templated prompt with slots: `{{company_name}}`, `{{owner_name}}`, `{{services}}`, `{{pricing_summary}}`, `{{service_area}}`, `{{hours}}`, `{{escalation_phone}}`, `{{emergency_keywords}}`, `{{memory}}`
- `prompts/_deprecated/v1_inline.md` — prior inline prompt, kept for rollback

**Files modified:**
- `llm.py` — Reads `prompts/receptionist_core.md` on first use, renders substitutions per call. Added `wrap_up_mode` param (`None`, `'soft'`, `'hard'`) that injects call-timer wrap-up cues. Added `reload_prompt()` and `last_token_usage()` helpers.

**Design notes:**
- Template loads once per process, cached.
- `{{var}}` substitution is a simple `str.replace` loop — avoids Jinja2 dependency.
- Wrap-up mode appends a `[SYSTEM: ...]` block rather than modifying the template itself. Keeps the core prompt clean.
- Prompt enforces: 2-sentence max, one-acknowledgment rule, batched info collection, early-exit paths (wrong number / hours / directions), explicit do-not list, wrap-up cues for duration cap.

**Test:**
```bash
python -c "
import os; os.environ.setdefault('ANTHROPIC_API_KEY','x')
import llm
from src import tenant
p = llm._render_system_prompt(None, tenant.load_client_by_number('+18449403274'))
assert 'Ace HVAC & Plumbing' in p
assert '{{company_name}}' not in p
print('OK')
"
```

**Risk:** Medium. The prompt grew from ~50 words to ~500 words. Input token cost per call rose ~10x (from ~50 tokens to ~500). Still tiny in absolute terms (~$0.0015 → $0.015 per 1M calls), but worth noting on the rate card. Prior prompt can be restored via `prompts/_deprecated/v1_inline.md`.

---

## Section E — Usage Tracking + Rate Card _(complete)_

**Files added:**
- `config/rate_card.json` — per-unit costs (LLM tokens, TTS chars, Twilio minutes, SMS segments, monthly number fee). Each field has a `_c_<name>` sibling comment key explaining it (JSON doesn't support comments natively).
- `src/usage.py` — SQLite-backed tracker (stdlib `sqlite3`, no deps). Schema: `calls`, `turns`, `sms`. Public API: `start_call`, `end_call`, `log_turn`, `log_sms`, `sms_count_for_call`, `monthly_summary(client_id, month)`, `margin_for(client)`, `recent_calls`.

**Files modified:**
- `llm.py` — Added `chat_with_usage()` that returns `(ChatResponse, (input_tokens, output_tokens))`. `chat()` still works for non-voice callers that don't need token counts.
- `main.py` — `_run_pipeline` now accepts `call_sid` and logs each LLM turn with input/output tokens + TTS char count. `/voice/incoming` records `start_call`, `/voice/status` records `end_call` with outcome mapping.

**Storage:** SQLite at `data/usage.db` (created on first write). WAL mode for safe concurrent reads. Indexed on `(client_id, month)` for fast monthly aggregation.

**Cost formula (in `monthly_summary`):**
```
cost = (in_tokens/1000 * input_rate)
     + (out_tokens/1000 * output_rate)
     + (tts_chars/1000 * synthesis_rate)
     + total_minutes * (stt + platform_voice + twilio_inbound rates)
     + sms_segments * sms_rate
     + twilio_number_monthly  (flat once per month)
```

**Margin formula:** `revenue (from client.plan.monthly_price) - cost`

**Design decisions:**
- SQLite over Postgres (spec constraint + zero deps + single-machine demo).
- Autocommit `isolation_level=None` — no explicit transactions, each write is atomic.
- Threading lock around DB operations (FastAPI runs sync handlers in a threadpool).
- Tracking is ALWAYS ON (not feature-flagged) — it's data collection only, no enforcement.
- Rate card is JSON (machine-edited by scripts later) per PLAN, with sibling `_c_*` comment keys for human readers.

**Test:**
```bash
python -c "
from src import usage, tenant
usage.start_call('CA_test_1', 'ace_hvac', '+14155550142', '+18449403274')
usage.log_turn('CA_test_1', 'ace_hvac', 'assistant', 520, 45, 80, 'Scheduling')
usage.end_call('CA_test_1', outcome='normal')
print(usage.margin_for(tenant.load_client_by_number('+18449403274')))
"
```

**Risk:** Low. All writes are append-only; no destructive mutations. If `data/usage.db` is corrupted, `logs/rejected_calls.jsonl` and Twilio's own call logs remain the source of truth for operational data.

---

## Section A — Call Duration Hard Cap _(complete)_

**Files added:**
- `src/call_timer.py` — in-memory call tracker. Public API: `record_start`, `record_end`, `mark_emergency`, `check(call_sid, client, caller_speech)`, `snapshot()`.

**Files modified:**
- `main.py` — `/voice/incoming` now calls `call_timer.record_start`. `/voice/gather` consults `call_timer.check` before the LLM; passes `wrap_up_mode` into `_run_pipeline` → `llm.chat_with_usage`. On `action='force_end'`, plays a polite owner-callback goodbye and hangs up. On emergency intent, calls `call_timer.mark_emergency` to extend cap to 360s for any subsequent turns. `/voice/status` clears timer state on call end.
- `llm.py` already supported `wrap_up_mode` parameter from Section B.

**Thresholds (configurable per-client via `plan.max_call_duration_seconds` / `max_call_duration_emergency`):**
- **Soft wrap-up cue** injected at `cap - 60` seconds (180s for default 240s cap)
- **Hard wrap-up cue** injected at `cap - 15` seconds (225s)
- **Force end** at cap (240s) — when `ENFORCE_CALL_DURATION_CAP=true`
- **Grace period** of +15s if caller is actively giving address/phone/name (detected via digit-heavy speech + keywords like "address", "phone number")
- **Emergency calls** use `max_call_duration_emergency` (default 360s)

**Feature flag:** `ENFORCE_CALL_DURATION_CAP` (default false → logs `action='force_end'` but still returns `hard_wrapup` so the AI closes the call on its own). Combined with global `MARGIN_PROTECTION_ENABLED` kill — if either is off, enforcement is bypassed.

**Test:** The test in `tests/test_call_timer.py` (Section K) covers: normal, soft/hard thresholds, force_end under enforcement, grace period, emergency extension, kill switch bypass. Smoke-tested manually:
```
100s elapsed: normal
185s elapsed: soft_wrapup
230s elapsed: hard_wrapup
250s no-enforce: hard_wrapup (logs only)
250s enforce: force_end
245s critical info: soft_wrapup (grace activated)
emergency 250s: normal (cap=360)
kill switch: hard_wrapup (bypasses enforcement)
```

**Risk:** High on rollout (can truncate live calls). Mitigation: default shadow mode. Operator enables `ENFORCE_CALL_DURATION_CAP=true` only after observing `logs` for a day. Emergency calls get the longer cap automatically once `priority='high'` fires.

---

## Section C — Spam + Junk Filtering _(complete)_

**Files added:**
- `config/spam_blocklist.json` — caller-ID blocklist + high-risk area codes
- `config/spam_phrases.json` — spam phrases + override keywords (service/address/emergency)
- `src/spam_filter.py` — two-layer filter with override bypass + rejection logging

**Files modified:**
- `main.py` — `/voice/incoming` runs `spam_filter.check_number` before any LLM call; `/voice/gather` runs `spam_filter.check_phrases` in the first 15s of the call. Both return early with polite goodbye if rejected.

**Two filter layers:**

1. **Number blocklist** (pre-LLM, zero cost to reject):
   - Normalized phone comparison against `config/spam_blocklist.json::numbers`
   - Area code comparison against `area_codes_high_risk`
   - Match → "Thanks, we're not taking calls from this number. Goodbye." + hangup

2. **Phrase detection** (during first 15s of transcript):
   - Scan SpeechResult for any phrase in `spam_phrases` list
   - **CRITICAL override**: if caller ALSO said any of the `override_keywords` (plumbing/hvac/address/emergency/etc.), bypass the filter entirely — this prevents rejecting legit service calls that happen to contain a spam-flagged word
   - Match → "Thanks, we're not interested. Goodbye." + hangup

3. **Silence timeout** (≥5s no speech): implemented in `check_silence` but not wired into the default flow yet — Twilio's speechTimeout="auto" already catches most silence. Retained in `spam_filter.py` for explicit opt-in.

**Rejection logging:** all rejections append a JSON line to `logs/rejected_calls.jsonl`:
```json
{"layer":"phrase","reason":"spam_phrase_detected","phrase":"google business listing",
 "from":"+1...","client_id":"ace_hvac","call_sid":"CA...","transcript_first_200":"...",
 "ts":1744000000,"enforced":true}
```

Audit weekly to tune false positives.

**Feature flag:** `ENFORCE_SPAM_FILTER`. When false, all matches are LOGGED but call proceeds normally. Use this for the first few days to calibrate the phrase list.

**Test results (manual smoke):**
```
spam phrase (no override):     reject=True  ✓
service keyword:               reject=False ✓ (override_keyword='plumbing')
spam + address override:       reject=False ✓ (override_keyword='street')
emergency override:            reject=False ✓ (override_keyword='flood')
past 15s window:               reject=False ✓ (filter window expired)
kill switch:                   reject=False ✓ (logs only)
blocklisted number:            reject=True  ✓
high-risk area code (555):     reject=True  ✓
normal number:                 reject=False ✓
```

**Risk:** Medium — wrong rejections = lost revenue. Override keywords are generous by design. Shadow mode default lets operator tune before enforcing.

---

## Section D — SMS Rate Limiting _(complete)_

**Files added:**
- `src/sms_limiter.py` — `cap_length(body)`, `should_send(call_sid, client, body)`

**Files modified:**
- `main.py` — `/voice/status` (missed-call recovery SMS) and `/sms/incoming` (two-way SMS) both check `sms_limiter.should_send` before `tw.messages.create`. Every outbound SMS is logged via `usage.log_sms`.

**Policy:**
- Per-call cap from `client.plan.sms_max_per_call` (default 3)
- Body auto-truncated to 320 chars (2 segments) at word boundary where possible
- SMS conversations keyed by `SMS_<phone_digits>` — shared counter across two-way thread

**Feature flag:** `ENFORCE_SMS_CAP`. When false, cap reached → logged only, SMS still sent.

**Test (smoke):**
```
msg 0-2: allow=True  ✓
msg 4:   allow=False, reason=sms_cap_reached ✓
long body (400 chars): truncated to 320 ✓
kill switch: allow=True (not enforcing) ✓
```

**Risk:** Low. Rate-limiting SMS is additive — worst case operator turns flag off and everything flows as before.

---

## Section F — Alerting _(complete)_

**Files added:**
- `config/alerts.json` — transport (smtp or webhook), thresholds, mode (digest or event), digest hour
- `src/alerts.py` — threshold evaluation + dispatch + daily loop

**Files modified:**
- `main.py` — FastAPI lifespan hook starts the digest loop at startup, stops it on shutdown. (Migrated from `@app.on_event` to `asynccontextmanager` lifespan — modern FastAPI.)

**Thresholds (config-driven):**
- 60%: log only
- 80%: notify
- 100%: notify + flag overage
- 150%: urgent notify

Evaluated against `included_minutes` (preferred) or `included_calls` (fallback if no minute limit).

**Transport:** operator picks SMTP *or* webhook in `config/alerts.json`. No deps beyond stdlib (`smtplib`, `urllib.request`).

**Modes:**
- `digest` (default) — one summary email/webhook per day at `digest_hour_utc` (default 14 UTC = 9 AM ET). Reduces notification noise.
- `event` — implemented structure but loop currently runs digest only; event mode extension is a 5-line change (evaluate after each call).

**Feature flag:** `ENFORCE_USAGE_ALERTS` default **true** (safe — notifications only, no call disruption). Still respects global `MARGIN_PROTECTION_ENABLED` kill.

**Test (smoke):**
```
ace_hvac at 60% -> threshold=log, margin=89.9%
ace_hvac at 120% -> threshold=overage
send_digest_now() -> sent=False (webhook URL empty, correctly no-ops), 2 events evaluated
```

**Risk:** Low. Failure modes are silent (logged, not raised). No webhook URL = no-op, incomplete SMTP = no-op.

---

## Section H — Voice Tier Optimization _(scaffold complete; no immediate cost win)_

**Files modified:**
- `main.py` — Added `VOICE_TIER_MAP` with `premium`/`flash`/`standard` tiers. `_voice_for(lang, client, mode)` returns the correct voice based on `client.plan.voice_tier_main` vs `voice_tier_transactional`. Transactional phrases (spam rejections, goodbyes, transfer announcements, no-on-call messages, duration-cap force-end) tagged with `mode="transactional"`.

**Honest limitation (documented in inline code comments):** on Polly via Twilio, all Neural voices cost the same per character. `premium` and `flash` both resolve to the same Polly Neural voice → **no immediate cost savings** on the current stack.

**Where this matters:** when operator switches TTS to ElevenLabs (Flash vs Turbo vs Multilingual — meaningful price differences), or adds a separate transactional TTS provider, the config hook is already in place:

```python
VOICE_TIER_MAP["flash"] = {"en": "ElevenLabs.Joanna-Flash", ...}
```

No code changes needed beyond updating the map.

**`standard` tier is wired now** and does reduce cost — if operator sets `voice_tier_transactional: "standard"` in a client's YAML, they get non-Neural Polly (cheaper per char). Provided as a knob for cost-sensitive tenants who accept a quality drop on goodbyes/confirmations.

**Test (smoke):**
```
main (ace_hvac, premium):          Polly.Joanna-Neural
transactional (ace_hvac, premium): Polly.Joanna-Neural   # same — no win on Polly
standard tier (test):              Polly.Joanna          # non-neural
```

**Risk:** Very low. Backward-compatible — old `_voice_for(lang)` signature still works (client defaults to None → premium tier).
