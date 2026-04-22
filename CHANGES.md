# CHANGES — Margin Protection + Multi-Tenant Refactor + Production Ship

_Branch: `margin-protection-refactor` → `ship-production`_
_Starting commit: baseline_

This document is a running log. Each section below corresponds to one commit on the branch.

---

# Branch: ship-production — production hardening (P0–P11)

Follows `margin-protection-refactor`. 11 priorities — each gets a code commit
and a docs commit. All new enforcement defaults to shadow mode / opt-in.

---

## P0 — Admin security _(complete)_

**Why:** `/admin` is public-shaped even though it's read-only. Before exposing
via a tunnel to a prospect or client we need auth on by default, a rate limit
against accidental scraping/DoS, and baseline headers so a misconfigured
response can't be content-sniffed or leak referrers.

**Files added:**
- `src/security.py` — `AdminRateLimitMiddleware` (token bucket, 60/min per IP,
  429 on exceed) and `SecurityHeadersMiddleware` (nosniff + no-referrer).
- `tests/test_security.py` — 8 tests: headers everywhere, rate-limit
  boundary, non-admin bypass, Basic auth 401 + accept/reject, XFF-split
  buckets.

**Files modified:**
- `main.py` — Adds both middlewares. Order: headers middleware added first so
  rate-limit 429 responses still carry headers.
- `.env.example` — `ADMIN_USER` / `ADMIN_PASS` documented as required before
  any public exposure; new `ADMIN_RATE_LIMIT_PER_MIN` knob (default 60).
- `ROLLOUT.md` — "before exposing via tunnel" checklist updated.

**Design notes:**
- In-memory token bucket per client IP. Process-local — multi-instance
  deployments should swap for Redis (documented in the module).
- `X-Forwarded-For` honored so bucket keys differentiate real callers
  behind cloudflared.
- Basic auth was already scaffolded in `src/admin.py`; nothing changed
  there — only the env-var documentation and the front-door rate limit.

**Test:**
```bash
pytest tests/test_security.py -v
# 8 passed
```

**Risk:** Low. Rate limit cap (60/min) is well above any realistic human
browsing. Headers are additive. Basic auth only enforces when both env
vars are set; local-only operation is unchanged.

---

## P1 — Client-facing portal _(complete)_

**Why:** We need one stable URL per client that they can bookmark and see
their own activity in — without exposing any operator-only fields (cost,
margin, per-client revenue) or letting one client see another's data.

**Files added:**
- `src/client_portal.py` — APIRouter with three routes plus a CLI:
    - `GET /client/{client_id}?t=<token>` — current-month summary
      (calls handled, emergencies routed, bookings captured, minutes used /
      plan limit, last call, filtered-call count). NO cost/margin fields.
    - `GET /client/{client_id}/calls?t=<token>` — recent call log
      (timestamp, duration, outcome, inferred intent).
    - `GET /client/{client_id}/invoice/{YYYY-MM}?t=<token>` — printable
      monthly invoice. Uses `src.invoices` if available (P2), otherwise a
      light fallback computed from `usage.monthly_summary` + plan YAML.
    - `python -m src.client_portal issue <client_id>` → prints the full
      signed URL. Refuses if `CLIENT_PORTAL_SECRET` is unset.

- `tests/test_client_portal.py` — 15 tests covering token roundtrip,
  rotation, 403 on missing/bad token, unknown-client leak check, no
  operator vocabulary in client-facing HTML, CLI behavior.

**Files modified:**
- `main.py` — Mounts `client_portal.router`.
- `.env.example` — Adds `CLIENT_PORTAL_SECRET`, `PUBLIC_BASE_URL`.

**Token design:**
- Signed with HMAC-SHA256 over `"{client_id}|{issued_ts}"` using
  `CLIENT_PORTAL_SECRET`.
- Serialization: `"{issued_ts}.{hex_signature}"`.
- Never expires. Rotate by changing the secret — all existing tokens
  become invalid on next request.
- If `CLIENT_PORTAL_SECRET` is empty or unset, BOTH issuing and
  verifying fail closed. The portal is effectively disabled until a
  secret is set. This prevents accidental unauthenticated exposure.

**Route hygiene:**
- Unknown client ID → 403 (not 404) so existence of a client can't be
  probed by URL enumeration.
- IDs starting with `_` (e.g. `_default`, `_template`) are unreachable
  via the portal even with a valid HMAC — these are reserved configs.
- Inline HTML + CSS (no Jinja) — matches the `src/admin.py` style and
  keeps dependencies flat. Print-friendly CSS hides the nav for the
  invoice view.

**Test:**
```bash
pytest tests/test_client_portal.py -v    # 15 passed
pytest tests/                            # 75 passed total
```

**Risk:** Low. Stateless, signed tokens; no DB writes on the portal path.
Summary/calls routes are read-only wrappers around existing `usage`
aggregates. Invoice route degrades gracefully to a fallback body until
P2 lands.

---

## P2 — Automated monthly billing _(complete)_

**Why:** Invoicing was manual (export-CSV out of /admin, price by hand, send
PDF). For this to scale to multiple clients, the invoice needs to generate,
render (HTML + CSV), and dispatch itself once a month via the same
transport the daily digest uses.

**Files added:**
- `src/invoices.py` — `generate_invoice`, `render_invoice_html`,
  `render_invoice_csv`, `send_invoice`, `send_monthly_invoices`. CLI:
  `python -m src.invoices preview|csv|send|send-all`.
- `tests/test_invoices.py` — 16 tests covering generation (base only,
  with overage, filtered calls excluded, reserved-client refusal),
  rendering (HTML total + CSV roundtrip + no internal vocabulary),
  dispatch gates (disabled → skipped, SMTP without owner_email → skip,
  previous-month wrap, day/hour gate), client-portal integration, CLI.

**Files modified:**
- `src/alerts.py` — daily digest loop now also calls
  `_maybe_send_monthly_invoices` after each digest send. Fires only when
  today matches `monthly_invoice.send_on_day` + `send_hour_utc`.
- `config/alerts.json` — adds a `monthly_invoice` block with `enabled`,
  `send_on_day`, `send_hour_utc`, `transport` (default
  `same_as_digest`, which falls back to the digest's transport).
- `clients/ace_hvac.yaml`, `clients/_default.yaml`,
  `clients/_template.yaml`, `clients/example_client.yaml` — add
  `owner_email`, `owner_cell`, `timezone` fields (used by P2, P3, P4
  respectively). All default empty/safe so no existing client routing
  breaks.
- `src/client_portal.py` — invoice route already had an
  `ImportError` fallback; it now resolves `src.invoices` and renders
  the richer body.

**Line items produced:**
- Monthly service plan (plan base, always)
- Included calls / included minutes (informational)
- Calls handled / minutes used / SMS segments / emergencies
  (informational)
- Call overage (if `included_calls` AND `overage_rate_per_call` set)
- Minute overage (if `overage_rate_per_minute` set — new optional plan field)
- SMS overage (if `overage_rate_per_sms` + `included_sms_segments` set)
- Emergency surcharge (if `emergency_surcharge` set per emergency)

Only the billable rows count toward `total`; info rows show qty +
render `—` in the amount column.

**Transport behavior:**
- SMTP: HTML body + CSV attachment sent to `client.owner_email`.
  If `owner_email` is missing, the client is skipped with a
  `reason=owner_email_missing` entry in the result.
- Webhook: POSTs `{type, client_id, month, html, csv, line_items, total}`
  to the alerts webhook URL.
- Transport resolution: `monthly_invoice.transport` can be `smtp`,
  `webhook`, or `same_as_digest` (default) — the last inherits from
  `config/alerts.json::transport`.

**Operator levers:**
- `python -m src.invoices preview ace_hvac 2026-04` → HTML to stdout
- `python -m src.invoices csv ace_hvac 2026-04` → CSV to stdout
- `python -m src.invoices send ace_hvac 2026-04` → one-off send
- `python -m src.invoices send-all --month 2026-04` → force send for all

**Test:**
```bash
pytest tests/test_invoices.py -v  # 16 passed
pytest tests/                     # 91 passed total
```

**Risk:** Low. `monthly_invoice.enabled=false` by default — nothing fires
automatically until an operator flips the flag. Shadow-equivalent. Re-send
via CLI is always available so an errored batch can be retried manually.

---

## P3 — Emergency owner SMS push _(complete)_

**Why:** When the AI transfers an emergency, the owner's phone rings — but
without context. They answer a blind call. Pushing an SMS to the owner's
cell the instant the AI makes the call lets them see caller number, the
one-line summary, and the address-on-file BEFORE the ringing phone
reaches them.

**Files added:**
- `src/owner_notify.py` — `notify_emergency(client, caller_phone, summary,
  address, call_sid, twilio_client, twilio_from)` → dict. Best-effort,
  never raises. Uses `sms_limiter.cap_length` so the body never exceeds
  320 chars.
- `tests/test_owner_notify.py` — 12 tests: body construction,
  length cap, owner_cell → escalation_phone fallback, DB logging as
  `direction='owner_alert'`, caller-cap exclusion, shadow-mode
  (`ENFORCE_OWNER_EMERGENCY_SMS=false`), kill switch, twilio-
  unavailable, send-error resilience.

**Files modified:**
- `main.py` — `/voice/gather` emergency branch now calls
  `owner_notify.notify_emergency` BEFORE `vr.dial(on_call)`. The
  outbound SMS fires instantly so the owner's phone lights up with
  context while the Twilio bridge is still forming.
- `.env.example` — adds `ENFORCE_OWNER_EMERGENCY_SMS=true` (default on
  — notifications are safe) with shadow-mode + kill-switch notes.
- `clients/*.yaml` — new optional `owner_cell` field (P2 commit
  already added this across all YAMLs).

**Billing semantics:**
- SMS row logged with `direction='owner_alert'` — caller's per-call
  outbound cap is unaffected (`sms_limiter.should_send` only counts
  `direction='outbound'`).
- `usage.monthly_summary` sums segments across all directions, so the
  owner_alert DOES count toward the client's billable SMS — which
  matches the spec.

**Shadow mode:**
- Setting `ENFORCE_OWNER_EMERGENCY_SMS=false` logs an
  `owner_alert_shadow` row and skips the real send. Useful for
  calibrating during rollout without generating live SMS charges.

**Test:**
```bash
pytest tests/test_owner_notify.py -v   # 12 passed
pytest tests/                          # 103 passed total
```

**Risk:** Low. Wrapped in try/except in `main.py` — even a bug in the
module can't disrupt the caller's emergency transfer. If credentials or
numbers are unavailable the send skips with a clear reason.

---

## P4 — Owner end-of-day digest _(complete)_

**Why:** Alerts (P-past) fire only when a threshold is crossed. Owners still
want a quiet nightly wrap-up: how many calls today, how many emergencies
routed, how many bookings captured. Pushed automatically at 10 PM local
time per-tenant.

**Files added:**
- `src/owner_digest.py` — `build_digest(client, local_date)` returns a
  dict `{calls_total, emergencies, bookings_captured, spam_filtered,
  avg_response_s, top_issue_themes, owner_cell, owner_email}`. Also
  `render_sms`, `render_email`, `send_digest`, plus a CLI.
- `src/scheduler.py` — one asyncio task wakes every 60s, walks active
  clients, fires the digest for any whose local time matches
  `OWNER_DIGEST_HOUR_LOCAL`. Dedup per `(client_id, local_date)`.
- `tests/test_owner_digest.py` — 15 tests: zero-activity digest,
  activity counting with seeded calls, reserved-client refusal, SMS
  length cap, email subject/body, SMS-preferred send, shadow mode,
  no-channel skip, scheduler tick in-hour / off-hour / reserved-client
  exclusion, CLI paths.

**Files modified:**
- `main.py` — lifespan starts/stops the scheduler.
- `.env.example` — `ENFORCE_OWNER_DIGEST` (default true) and
  `OWNER_DIGEST_HOUR_LOCAL` (default 22).

**Timezone handling:**
- Uses `zoneinfo.ZoneInfo(client.timezone)` (stdlib, Python ≥3.9).
- Default timezone on a client YAML is `America/New_York`.
- Day boundaries are computed in the client's local time, so "today's
  digest" at 10 PM local reflects exactly the calls that happened that
  day regardless of UTC offset.

**Transport order (per send):**
1. If `owner_cell` set + Twilio available → SMS (body capped at 320).
2. Else if `owner_email` set + SMTP configured → HTML email.
3. Else → logged + skipped (`reason='no_channel_available'`).

SMS row logged with `direction='owner_digest'`. Doesn't count against
the caller's per-call SMS cap; counts toward billable SMS.

**Test:**
```bash
pytest tests/test_owner_digest.py -v   # 15 passed
pytest tests/                          # 118 passed total
```

**Risk:** Low. Scheduler is one coroutine that catches its own exceptions
per-client, so a YAML with a typo in `timezone` logs an error and moves
on — doesn't kill the loop. In-memory dedup state is lost on restart
(worst case: a duplicate digest if bounced at 22:00:30 local; acceptable).






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

---

## Section I — Conversation Flow Audit _(complete)_

**Files modified:**
- `prompts/receptionist_core.md` — Added **Minimum info** rule (don't collect what you don't need: emergency = just address + callback; wrong number = nothing; existing customer = confirm, don't re-ask). Added **Don't repeat back** rule (no "So your name is… address is… number is…" echoes).

Section B already established the baseline (batched questions, early exits for wrong number / hours / directions / price-only). Section I tightens the behavior with two specific anti-patterns that burn turns.

**Test:** `llm._render_system_prompt` now returns 4408 chars including both new sections. Live voice behavior validation requires an actual call — operator should test the `/chat` endpoint with a script:
```bash
curl -X POST http://localhost:8765/chat \
  -H 'Content-Type: application/json' \
  -d '{"caller_id":"sarah","message":"wrong number, sorry"}'
# Expected: one-sentence sign-off, not an info collection attempt
```

**Risk:** Low. Prompt-only change. Rollback = revert this section in `prompts/receptionist_core.md`.

---

## Section J — Admin Dashboard _(complete)_

**Files added:**
- `src/admin.py` — FastAPI router with 5 read-only endpoints

**Files modified:**
- `main.py` — Mounts `admin.router` on the app

**Endpoints:**
- `GET /admin` — per-client margin table (color-coded: red<0%, yellow<50%, green≥50%)
- `GET /admin/calls?limit=50&client_id=ace_hvac` — recent call log
- `GET /admin/export.csv?month=2026-04` — CSV download for overage billing
- `GET /admin/flags` — current feature-flag values + descriptions
- `GET /admin/alerts/trigger` — force a digest send (for testing alert config)

**Auth:** optional HTTP Basic via `ADMIN_USER` / `ADMIN_PASS` env vars. If either is unset, admin is open (intended for local-only ops).

**Design notes:**
- Inline HTML + CSS, no Jinja2 (zero new deps — spec constraint)
- No mutation endpoints — admin is read-only by design. Config changes require editing YAML/JSON + restart.
- CSV export ready for pasting into billing spreadsheets

**Test:**
```bash
uvicorn main:app --port 8765 &
curl http://localhost:8765/admin                   # HTML overview
curl http://localhost:8765/admin/calls             # HTML call log
curl http://localhost:8765/admin/export.csv        # CSV download
curl http://localhost:8765/admin/flags             # HTML flag view
```

Routes registered successfully: `/admin`, `/admin/calls`, `/admin/export.csv`, `/admin/flags`, `/admin/alerts/trigger`.

**Risk:** Low. Read-only. No admin = no ability to break production. Failure modes: if auth is misconfigured, endpoint returns 401 rather than a 500.

---

## Section K — Tests _(complete)_

**Files added:**
- `pytest.ini` — pytest config
- `tests/__init__.py`
- `tests/conftest.py` — shared fixtures (env isolation, temp SQLite DB per test, client fixtures)
- `tests/test_tenant.py` — 5 tests (Section G)
- `tests/test_call_timer.py` — 11 tests (Section A)
- `tests/test_spam_filter.py` — 12 tests (Section C)
- `tests/test_usage.py` — 6 tests (Section E)
- `tests/test_sms_limiter.py` — 7 tests (Section D)
- `tests/test_alerts.py` — 6 tests (Section F, webhook stubbed)
- `tests/test_kill_switch.py` — 4 cross-module kill-switch tests

**Total: 51 tests, all passing in ~33 seconds.**

**Coverage per spec requirement:**
- ✅ Call duration cap triggers at 240s
- ✅ Emergency extension to 360s
- ✅ Grace period for critical-info collection
- ✅ Spam filter rejects obvious spam phrases
- ✅ Spam filter does NOT reject legit calls with override keywords
- ✅ Usage tracking increments correctly
- ✅ Alert thresholds at 60/80/100/150%
- ✅ Kill switch bypasses every enforcement module
- ✅ Multi-tenant routing via inbound number

**Isolation:** `conftest.py::_isolate_env` autouse fixture:
1. Strips all `ENFORCE_*` + `MARGIN_PROTECTION_ENABLED` env vars to default state
2. Redirects `usage.DB_PATH` to a per-test tmp path
3. Calls `tenant.reload()` to clear YAML cache

Each test starts with a clean slate.

**`_test_suite.py` (legacy integration smoke test) is unchanged** — still runs independently against a live server if operator wants end-to-end verification.

**Test run:**
```
$ pytest tests/
...................................................                      [100%]
51 passed in 33.23s
```

**Risk:** N/A — tests are code-only.

---

## Section L — Shadow Mode + ROLLOUT.md _(complete)_

**Files added:**
- `ROLLOUT.md` — 7-day rollout plan with explicit day-by-day steps, rollback procedures, and emergency kill-switch usage.

**Feature flags (already wired through all sections):**

| Flag | Default | Purpose |
|---|---|---|
| `MARGIN_PROTECTION_ENABLED` | `true` | Global kill switch — bypasses ALL enforcement when false |
| `ENFORCE_CALL_DURATION_CAP` | `false` | Shadow mode: logs would-force-end, doesn't actually end |
| `ENFORCE_SPAM_FILTER` | `false` | Shadow mode: logs matches, doesn't reject |
| `ENFORCE_SMS_CAP` | `false` | Shadow mode: logs cap events, doesn't block SMS |
| `ENFORCE_USAGE_ALERTS` | `true` | Safe default — notifications don't disrupt calls |

**Rollout order (from ROLLOUT.md):**
- Day 1–2: shadow mode, observe
- Day 3: enable spam filter
- Day 4: enable SMS cap
- Day 5: enable duration cap (**highest risk**)
- Day 6–7: tune + margin review
- After: merge to `main`, tag `v1.0-margin-protection`

**Risk:** N/A — rollout plan is a doc.

---

## Phase 3 — Client Onboarding Kit _(complete)_

**Files added:**
- `docs/NEW_CLIENT_CHECKLIST.md` — 7-step onboarding from first call to going live, with pitfalls
- `docs/DEMO_SCRIPT.md` — 30-minute prospect walkthrough with objection responses

**Risk:** N/A — docs only.

---

## Audit pass — post-refactor regression hunt _(complete)_

Full integration audit run across the whole system. Found and fixed **5 issues**:

### Bugs fixed

1. **Empty `To` in web/test context fell through to `_default` tenant.**
   - Voice callers from Twilio always include `To`, but web chat (`/chat`, `/recover`) has no phone context → tenant resolution returned generic "this service".
   - **Fix:** `tenant.load_client_by_number("")` now returns the sole real tenant if exactly one is configured; `_default` when multiple.
   - Files: `src/tenant.py`, `main.py` (passes through in `/chat` + `/recover`).

2. **Admin dashboard listed reference/example tenants as if they were live.**
   - `clients/example_client.yaml` (Bob's Septic) appeared in `/admin` table and CSV export with $297 revenue / $0 cost — misleading the operator about active accounts.
   - **Fix:** Filter out configs with empty `inbound_number` from admin overview + alerts. Reference configs stay in repo as documentation.
   - Files: `src/admin.py`, `src/alerts.py`, `clients/example_client.yaml` (cleared `inbound_number`).

3. **Spam filter override was too permissive.**
   - Generic scheduling/pricing words ("quote", "estimate", "schedule", "appointment") were on the override list. Spam pitches like "solar quote" and "free estimate" contained them → bypassed filter.
   - **Fix:** Trimmed `override_keywords` in `config/spam_phrases.json` to service-specific terms only (plumbing, pipe, furnace, flood, "my house", street, etc.). Added `_comment_override` explaining the rationale.
   - Re-tested: pure spam phrases now reject correctly; legit service+spam mixes still allow via the concrete override.

4. **Legacy `_test_suite.py` didn't pass `To` form field.**
   - 4 tests failed after Section G multi-tenant refactor — they were testing against `_default` tenant by accident.
   - **Fix:** Added `_ACE = "+18449403274"` constant, all voice tests now pass it. Also updated XML-parsing helpers to handle the Gather-wrapped Say shape (from voice-optimization work).
   - Also added new test `test_voice_incoming_returning_caller_skips_menu` to cover the returning-caller path.

5. **`tests/test_tenant.py::test_template_client_does_not_route`** expected empty phone → `_default`. Now returns the sole tenant by design.
   - **Fix:** Renamed expectation, added a new test `test_empty_phone_with_multiple_real_tenants_falls_to_default` using a temp clients dir with two real tenants — verifies the fallback heuristic behaves correctly in both modes.

### Results

- **52 pytest tests pass** (was 51; added 1)
- **19 legacy `_test_suite.py` integration tests pass** (was 18; added 1 new test for returning-caller greeting)
- **8 voice-flow end-to-end scenarios verified live** (language menu, setlang, scheduling, follow-up, empty speech, emergency, status callback, DTMF 0)
- **5 admin dashboard endpoints verified** (overview, calls, CSV, flags, alerts/trigger)
- **16 edge-case scenarios verified** (unknown numbers, empty inputs, spam override matrix, SMS length cap, memory persistence, log format)

### Not a bug but worth noting

- `/voice/incoming` with `From=""` returns 422 (FastAPI form validation). That's correct — Twilio never sends empty `From` in production.
- Anthropic free-tier rate limiting (~5 RPM on Haiku 4.5) causes timeouts when tests fire many LLM calls back-to-back. Not a bug in the app. In real phone calls, turns are naturally spaced 5-15s apart.
