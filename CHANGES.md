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

## P5 — Onboarding wizard _(complete)_

**Why:** Copy-template + hand-edit YAML is error-prone (missing fields,
bad E.164, wrong YAML indentation). A guided wizard cuts onboarding time
from ~10 minutes (and occasional typos that route nothing) to ~3 minutes
with a validated YAML that can't silently misroute.

**Files added:**
- `src/onboarding.py` — CLI with three subcommands:
    - `new` — full interactive Q&A (17 fields, all validated)
    - `new-demo` — random-id 24h disposable tenant, with `demo: true`
      + `demo_expires_ts` so the server auto-purges on startup
    - `purge-expired` — manual sweep (startup also calls it)

  Validators: E.164, snake_case id, IANA timezone, non-empty,
  positive number. `_ask()` loop re-prompts until valid. I/O is
  injectable (reader/writer) so tests run headless — no stdin mocking.

- `tests/test_onboarding.py` — 17 tests covering validators, the
  re-prompt loop, full-Q&A happy path, id collision, demo build
  + expiry, purge semantics, YAML round-trip, followup output
  (portal URL + webhook URLs + curl hint), CLI paths.

**Files modified:**
- `main.py` — lifespan startup calls `onboarding.purge_expired_demos()`
  so stale demo YAMLs can never accidentally route live traffic after
  their 24h window.

**Followup printout** — after writing the YAML, the wizard prints:
1. Missing `.env` values (CLIENT_PORTAL_SECRET, PUBLIC_BASE_URL)
2. The three Twilio webhook URLs to paste into the Twilio console
   (uses `PUBLIC_BASE_URL` env or the tunnel hint file written by
   `scripts/reclaim_tunnel.py` in P9; placeholder otherwise)
3. A ready-to-send client portal URL (when secret is set)
4. A `python -c` tenant-routing sanity check
5. A `curl -X POST /voice/incoming` sanity test

**Test:**
```bash
pytest tests/test_onboarding.py -v   # 17 passed
pytest tests/                        # 135 passed total
```

**Risk:** Low. No production code path depends on the wizard — it's an
operator tool. The startup purge can only move files into `clients/_expired/`,
never delete. If a demo YAML is malformed, the purge skips it with a log
line rather than raising.

---

## P6 — Twilio webhook signature verification _(complete)_

**Why:** Anyone who learns the tunnel URL could POST a forged
`/voice/incoming` and trick the AI into placing real emergency transfers
or burning LLM budget. Twilio signs every webhook; validating that
signature is a $0 protection once wired.

**Files added:**
- `src/twilio_signature.py` — `TwilioSignatureMiddleware`. Guards the
  five webhook paths (`/voice/incoming`, `/voice/setlang`,
  `/voice/gather`, `/voice/status`, `/sms/incoming`). Honors
  `X-Forwarded-Proto` + `X-Forwarded-Host` so signatures from Twilio
  (which sign `https://public-domain/path`) validate when the app sees
  `http://localhost/path` through cloudflared. `PUBLIC_BASE_URL` takes
  full precedence when set.
- `tests/test_twilio_signature.py` — 9 tests: valid sig passes, missing
  sig 403s, wrong sig 403s, shadow mode passes, non-voice paths
  unaffected, downstream form parsing still works (body re-yield),
  `/sms/incoming` verified, missing token in shadow mode, PUBLIC_BASE_URL
  override path.

**Files modified:**
- `main.py` — installs the middleware (added LAST in the stack so it
  runs FIRST on inbound requests; 403 happens before any other logic).
- `_test_suite.py` — legacy integration harness now signs `/voice/*` and
  `/sms/incoming` POSTs with `TWILIO_AUTH_TOKEN` if present, so the
  suite works against a live server with or without the flag on.
- `.env.example` — `TWILIO_VERIFY_SIGNATURES=true` default, with a
  shadow-mode hint for the initial Twilio wiring phase.

**Shadow mode:**
- `TWILIO_VERIFY_SIGNATURES=false` keeps the middleware installed but
  logs `shadow-pass` warnings for invalid signatures and lets the
  request through. Use while you're verifying Twilio webhook shapes or
  debugging tunnel URLs.

**Body re-yield:** the middleware reads the body up-front to verify the
signature, then replaces `request._receive` so FastAPI's `Form(...)`
parsing downstream still sees a body. Verified by the
`test_form_body_still_parses_downstream` case.

**Test:**
```bash
pytest tests/test_twilio_signature.py -v    # 9 passed
pytest tests/                                # 144 passed total
```

**Risk:** Medium. A misconfigured auth token would 403 every real
webhook. Mitigation: the `.env.example` comment explicitly calls out
shadow mode as the right first step when wiring Twilio. Operator flips
to enforce after the first 24h of observing successful webhooks.

---

## P7 — Self-improving eval harness _(complete)_

**Why:** Prompt edits and model changes silently regress behavior. Without
a seed set we see regressions only when a customer complains. 20 seeded
cases + a nightly diff catches meaningful drift before it ships.

**Files added:**
- `evals/__init__.py`
- `evals/cases.jsonl` — 20 seed cases covering: new-lead + returning
  scheduling, 4 emergency variants (burst, gas, no-heat, flood),
  quote/price-shopper, follow-up + irate, wrong-number variants,
  hours/directions, spam pitches (SEO + warranty), ambiguous, rambler.
  Each case carries `id, client_id, caller_phone, turns, expected_intent,
  expected_priority, must_contain, must_not_contain`.
- `evals/runner.py` — `load_cases`, `run_case` (injectable `chat_fn` for
  testability), `run_cases`, `append_history`, `load_history`,
  `latest_summary`. CLI: `python -m evals.runner [--save] [--case id]`.
- `evals/regression_detector.py` — `detect(summary, previous)` returns
  `{regressed, delta_pct_points}`. `run(dry_run)` runs the suite,
  appends to history, and fires an alert via `src.alerts._dispatch` if
  the pass-rate drop exceeds 5pp (threshold hardcoded — easy to tune).
- `tests/test_evals.py` — 16 tests: cases load, score pass/fail variants,
  runner exception handling, summary shape, history roundtrip,
  regression-threshold logic, full integration (good run → bad run
  detects), admin view empty + populated.

**Files modified:**
- `src/admin.py` — adds `/admin/evals` view. Shows latest pass rate +
  10-run trend. Inline HTML matches the rest of the admin UI.
- `src/scheduler.py` — `tick()` now ALSO calls
  `_maybe_run_eval_regression` once per day at `EVAL_REGRESSION_HOUR_UTC`
  when `ENFORCE_EVAL_REGRESSION=true`. Default off — the nightly
  regression run spends real LLM tokens, so opt-in only.
- `.env.example` — `ENFORCE_EVAL_REGRESSION=false` and
  `EVAL_REGRESSION_HOUR_UTC=7` documented.

**Design:**
- Runner calls `llm.chat` directly (not via HTTP) so a full suite runs
  in ~30s against real Claude — fast enough for interactive use,
  skippable in CI (tests inject a fake chat_fn).
- History is append-only JSONL at `data/eval_history.jsonl`. Slim rows
  (no per-case results) keep the file small indefinitely.
- Regression detection is a simple percentage-point delta. Smaller
  suites would need something smarter (Wilson interval, etc.). At 20
  cases, 5pp = 1 case — a coarse-but-useful signal.

**Test:**
```bash
pytest tests/test_evals.py -v        # 16 passed
pytest tests/                        # 160 passed total
```

**Risk:** Low — opt-in scheduling. Manual runs are always free to kick
off; the only thing controlled by flag is automatic nightly firing.

---

## P8 — Prompt caching on LLM calls _(complete — structure shipped, savings $0 today)_

**Why:** The rendered system prompt is identical for every call under one
tenant (modulo the caller memory suffix). Anthropic's ephemeral prompt
cache can serve that stable prefix at ~10% of normal input cost, which
would be meaningful once the prefix crosses the model's cache minimum.

**Files modified:**
- `prompts/receptionist_core.md` — caller-memory section moved OUT of
  the template (no more `{{memory}}` placeholder). The template is now
  purely the stable per-tenant body.
- `llm.py`:
    - New `_render_stable_text(client)` → stable per-tenant text.
    - New `_render_system_blocks(caller, client, wrap_up_mode,
      recover_suffix)` → list of content blocks: the first block wraps
      the stable body with `cache_control={"type": "ephemeral"}`, the
      second carries the volatile memory + wrap-up + recover suffix.
    - `chat_with_usage` + `recover` now pass the blocks as `system=`
      to `beta.messages.parse`.
    - `last_token_usage` now also returns `cache_read_input_tokens`.
    - Process-local `cache_stats()` tracks reads/writes/totals.
    - `PROMPT_CACHE_ENABLED` env var (default `true`) lets operator A/B
      test or disable caching; when disabled, a single plain block is
      sent.
- `evals/cache_benchmark.py` — runs the first N cases twice and prints
  a summary (tokens, cache reads, estimated USD savings).
- `tests/test_prompt_caching.py` — 11 tests: block shape, cache_control
  placement, env toggle returns single block, stable text excludes
  memory, backward-compat string renderer still works, wrap-up + recover
  suffixes stay in the volatile block (would bust cache if in the
  cached block), `last_token_usage` extracts cache_read, cache_stats
  reset, per-tenant prefix stability check, cross-tenant prefix
  distinctness.

**Measured savings (honest report):**

Running `python -m evals.cache_benchmark --cases 3` against the live
Anthropic API with `claude-haiku-4-5`:

```
Pass 1 — cold cache:  input_tokens = 4597, cache_read = 0
Pass 2 — warm cache:  input_tokens = 4597, cache_read = 0
Saved tokens:  0
USD saved:     $0.00
```

Reason: **the stable block is 1,085 tokens, below Haiku 4.5's 4,096-token
cache minimum.** Per Anthropic's documentation
(`https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md`
— "Minimum Token Requirements"), blocks shorter than the model minimum
have their `cache_control` silently ignored:

| Model | Minimum cache block |
|---|---|
| Claude Opus 4.x | 1024 tokens |
| Claude Sonnet 4.6 | 2048 tokens |
| Claude Haiku 4.5 | 4096 tokens |

**Implication:** the cache_control structure is present and correct — it
just doesn't activate on Haiku with today's prompt. Two paths to real
savings:
1. Migrate to Sonnet 4.6 and expand the template to >2048 tokens.
2. Keep Haiku and expand the template to >4096 tokens (add more
   examples, industry-specific guidance, etc.).

The spec anticipates this case: "If a prompt-caching setup would cost
more than it saves (under 1K tokens cached), skip it and say so." The
*setup* costs nothing here (extra JSON field is ignored by the API when
inactive), so the structure is kept in place to activate automatically
the moment the prompt or the model change crosses the threshold.

**Operator action:** run `python -m evals.cache_benchmark` any time you
change the prompt or model. A non-zero `cache_read_tokens` on pass 2 is
the signal that caching is live.

**Test:**
```bash
pytest tests/test_prompt_caching.py -v   # 11 passed
pytest tests/                            # 171 passed total
```

**Risk:** None. Adding `cache_control` to an API that ignores it is a
no-op. Backward-compat `_render_system_prompt` string helper preserved
for existing tests + code.

---

## P9 — Persistent tunnel (auto-reclaim + Named Tunnel docs) _(complete)_

**Why:** Every `cloudflared tunnel --url http://localhost:8765` restart
mints a new `https://<random>.trycloudflare.com` URL. Without an
automated process, the operator has to open the Twilio console and
paste three webhook URLs for every number after every restart — a 10x
footgun. The auto-reclaim script does this in seconds.

**Files added:**
- `scripts/reclaim_tunnel.py` — spawns cloudflared as a subprocess,
  parses its stderr stream for the `trycloudflare.com` URL, writes it
  to `data/tunnel_url.txt`, then PATCHes every managed Twilio number
  (derived from `clients/*.yaml::inbound_number`) so its
  `voice_url` / `status_callback` / `sms_url` point to the new tunnel
  path.
- `tests/test_reclaim_tunnel.py` — 11 tests: URL regex happy + sad
  paths, persist_url file creation, managed-number filtering, missing
  credentials skip, list() error handling, per-number update error
  handling, empty targets skip, tenant_numbers excludes reserved
  configs, stdout stream parsing + callback.

**Behavior:**
- Option A (Cloudflare Named Tunnel + your own domain) is documented in
  ROLLOUT.md — the 3-command setup. Preferred for production.
- Option B (auto-reclaim) is what this commit implements. Always works,
  zero signup, zero domain required. Keep using it until you're ready
  for Option A.

**Defaults:**
- cloudflared binary: `./cloudflared.exe` on Windows, `cloudflared` on
  *nix. Override with `--exe`.
- Local port: 8765. Override with `--port`.
- `--dry-run` captures the URL but doesn't touch Twilio (useful during
  initial wiring to confirm the output parsing works).
- `--once` exits after the first URL capture — convenient for CI smoke
  tests.

**Test:**
```bash
pytest tests/test_reclaim_tunnel.py -v   # 11 passed
pytest tests/                            # 182 passed total
```

**Risk:** Medium. Updates Twilio webhooks when invoked. Mitigations:
(1) only touches numbers that match a tenant `inbound_number` (numbers
not in any YAML are left alone); (2) errors per number don't abort the
batch; (3) `--dry-run` available for safe verification before any
writes happen.

---

## P10 — Analytics admin page _(complete)_

**Why:** `/admin` shows per-client margin and calls, but not the patterns
operators use to spot problems (when do calls come in, what are they
about, which clients are silently drifting). `/admin/analytics` answers
all four at a glance, no charting lib.

**Files modified:**
- `src/admin.py`:
  - New `/admin/analytics` route with four sections: intent
    distribution, calls-per-hour heatmap, month-over-month per-client
    trend, flagged-client list.
  - Pure-function helpers `_previous_month`, `_intent_counts`,
    `_calls_per_hour`, `_silence_rate`, `_flagged_clients` — each
    SQL-scoped by `(client_id?, month)` with the existing `_db_lock`.
- `tests/test_analytics.py` — 9 tests: empty render, intent + heatmap +
  MoM rendering, high-spam-rate flag, previous-month wrap, intent
  counts query, calls-per-hour query, silence-rate math.

**What the operator sees:**
- **Intent distribution:** one row per intent with count, share %, and
  an ASCII bar (`█` ratio).
- **Calls per hour (UTC):** 24 rows with `▇` density bars. Quickly
  shows when the after-hours line is actually busy.
- **MoM per client:** calls / minutes / margin with signed deltas vs
  previous month (`+12`, `-8`).
- **Flagged:** red rows for clients whose margin dropped below 50%,
  spam rate exceeds 20%, or silence-timeout rate exceeds 10%. Reasons
  are listed so the operator knows which lever to pull.

**Test:**
```bash
pytest tests/test_analytics.py -v   # 9 passed
pytest tests/                       # 191 passed total
```

**Risk:** Low. Read-only page. All SQL is scoped; no new writes.

---

## P11 — Per-call feedback SMS + self-improvement loop _(complete)_

**Why:** The eval suite catches obvious regressions, but real-world
problems show up as "the AI got the address wrong", "the AI missed
a booking" — not things you can easily seed. Soliciting a one-word
YES/NO from callers after every non-emergency call, and dumping the
conversation to a review file on NO, is the cheapest data-collection
mechanism possible.

**Files added:**
- `src/feedback.py`:
    - `maybe_send_followup(call_sid, client, caller_phone, outcome,
      duration_s, emergency, ...)` — guarded by outcome/duration/
      emergency/flag; sends one SMS with the prompt body.
    - `classify(body)` — lenient YES/NO matcher with trailing
      punctuation stripped; recognizes "yes/y/yeah/yup/yep/sure/
      absolutely" and "no/n/nope/nah/not really".
    - `record_response(caller_phone, body)` — looks up the most recent
      pending feedback row for this caller within 48h, records the
      classification, and on NO writes a transcript line to
      `logs/negative_feedback.jsonl`.
    - `feedback` SQLite table (call_sid, client_id, caller_phone,
      sent_ts, response, response_ts, transcript, month), init'd
      lazily on first write.
- `tests/test_feedback.py` — 23 tests: 12 classify variants (YES/NO,
  case, punctuation, multi-word "not really"), guard skips (non-normal
  outcome, emergency, too-short call, flag off), send path + pending
  row presence + transcript embed, inbound-response match YES + NO +
  negative-log write, unparseable, no-pending, outside-window.

**Files modified:**
- `main.py`:
    - `/voice/status` on `CallStatus=completed` invokes
      `_feedback.maybe_send_followup` with the caller's conversation
      history.
    - `/sms/incoming` starts by calling `_feedback.record_response`.
      On a match, the handler short-circuits with a brief ack message
      ("Thanks — passing that along!" for YES, "Got it — we'll follow
      up." for NO) and skips the LLM, so the feedback reply doesn't
      chain into a new conversation.
- `.env.example` — `ENFORCE_FEEDBACK_SMS=false` documented.

**Self-improvement loop:**
1. Caller hangs up.
2. `/voice/status` fires → `maybe_send_followup` pushes the YES/NO SMS.
3. Caller texts back.
4. `/sms/incoming` → `record_response` logs the result.
5. On NO → `logs/negative_feedback.jsonl` gains a line with
   call_sid + transcript + the caller's SMS body.
6. Operator grep for NO replies, promotes recurring patterns into
   new `evals/cases.jsonl` entries.
7. Next nightly eval run flags any regression and the loop closes.

**Test:**
```bash
pytest tests/test_feedback.py -v   # 23 passed
pytest tests/                      # 214 passed total
```

**Risk:** Low. Default-off flag. Send path fails closed on Twilio
outages. Matching has a 48h response window so stale incoming SMS
never accidentally match.

---

# v2.0 major upgrade — V1–V8

Full audit + redesign pass after the v1.0 ship. 8 focused
improvements spanning demo orientation, UI, ops hardening, two new
user-visible features (transcripts + HELP command), and repo
polish. Live `+18449403274`/Ace HVAC routing untouched throughout.

---

## V1 — Septic demo pivot _(complete)_

**Why:** The showcase needed to feel like a real business demo. HVAC
was fine for live traffic; septic is sharper for the 3-minute video
because "sewage backing up at 11 PM" is a single-sentence reason
every viewer gets.

**Files:**
- Added `clients/septic_pro.yaml` — full "Septic Pro / Bob" tenant.
  No `inbound_number` so it doesn't route live traffic.
- `memory.py` — 3 new seed callers (Ellen, Travis, Linda). HVAC
  seeds retained.
- New `docs/SHOWCASE_SCRIPT.md` — 3-min website video script,
  entirely septic.
- `docs/DEMO_SCRIPT.md` — header points operators at septic_pro as
  the baked-in warm-up tenant.
- `evals/cases.jsonl` — 5 septic cases added (overflow emergency,
  pump-out, quote, drain-field emergency, new-customer inspection).
- `_test_suite.py` — caller-seed assertion loosened (require HVAC
  seeds present, not strict equality).

## V2 — Design system + admin UI v2 _(complete)_

**Why:** Every HTML surface had bespoke CSS. Typography, spacing,
colors all inconsistent. v2 extracts a shared module so the three
surfaces (admin/portal/landing) speak the same visual language while
carrying distinct accents.

**Files:**
- New `src/design.py` — CSS custom-property tokens, automatic
  dark-mode via prefers-color-scheme, print-friendly overrides. Helpers
  `page()`, `card()`, `data_table()`, `stats()`, `stat_card()`,
  `pill()`, `sparkline()`, `heatbar()`.
- `src/admin.py` rewritten on the design system. New `/admin/call/{sid}`
  route for per-call transcript detail (wired in V4). Sidebar nav,
  stat cards, SVG sparklines, pill-styled outcomes, demo-tenant badges.
- `src/transcripts.py` — schema + CRUD scaffold for V4.
- `tests/test_routes.py` — manifest updated with new routes.

## V3 — Client portal UI v2 _(complete)_

**Files:**
- `src/client_portal.py` rewritten on the design system (accent="client",
  teal). Summary uses stat cards; call log uses friendly labels
  + pill variants; invoice re-skinned with print CSS hiding nav.
- New `/client/{id}/call/{sid}` route — client-side transcript detail.
  Explicit tenant-ownership check on the call SID prevents
  cross-tenant leaks even with a valid token.

## V4 — Call transcripts (new feature) _(complete)_

**Why:** We logged metadata but never the conversation itself. For
dispute resolution, training data, and client trust, that's a gap.

**Files:**
- `src/transcripts.py` — `record_turn`, `get_transcript`,
  `get_call_meta`. New `transcripts` SQLite table, schema init lazy
  on first write.
- `main.py::_run_pipeline` — now records user + assistant turns for
  every LLM exchange. No-ops on empty sid (web chat, some SMS).
- `tests/test_transcripts.py` — 13 tests: roundtrip, whitespace
  guards, ordering, admin detail rendering, portal tenant isolation,
  pipeline integration.

## V5 — index.html as showcase landing _(complete)_

**Why:** The root page was the old HVAC chat UI. Visitors to the
public site should land on a clear pitch, not a chat sandbox.

**Files:**
- `index.html` completely rewritten — sticky nav, gradient hero
  with social-proof strip, 3-feature grid, embedded live web-chat
  demo against `septic_pro` + septic seeds, quick-prompt chips
  (overflow emergency, book pump-out, price check, wrong number),
  4-step "how it works", terminal-styled clone-and-run teaser,
  gradient CTA. Dark-mode ready. ~17KB, no JS framework.
- `main.py` — `/chat` gains optional `client_id` so the landing
  can demo septic_pro regardless of the sole-tenant fallback.

## V6 — HELP SMS + welcome flow (new feature) _(complete)_

**Why:** Owners texting questions to their receptionist number would
have gotten a confused AI reply. Now they get a direct cheat sheet.

**Files:**
- New `src/owner_commands.py` — `is_help_command`, `handle_help_sms`,
  `build_welcome_body`, `send_welcome_sms`. Recognizes HELP / INFO /
  STATUS / LINK as first word. Owners (matched by owner_cell /
  escalation_phone, normalized) get a portal URL + escalation line;
  unknown numbers get a polite "call us" redirect.
- `main.py::/sms/incoming` — short-circuits HELP before the feedback
  check and before the LLM.
- `src/onboarding.py` CLI — new `welcome <client_id> [--to NUM]
  [--dry-run]` subcommand.

## V7 — Ops endpoints + request-id correlation _(complete)_

**Why:** K8s-style probes + log-line correlation are table-stakes
ops hygiene. Adding them after v1.0 was a small lift.

**Files:**
- New `src/ops.py` — `RequestIDMiddleware` (short hex ID per request,
  honors inbound X-Request-ID, sets X-Request-ID response header,
  exposes via contextvar); `install_logging()` (root format string
  includes `[request_id]`); `/health` (liveness); `/ready`
  (readiness — sqlite + tenant + prompt-template checks, 503 on any
  fail so load balancers stop).
- `main.py` wires all three.

## V8 — Audit + README + LICENSE _(complete)_

**Files:**
- New `README.md` — proper GitHub landing with status badges, quick
  start, feature table, repo tour, links to key docs.
- New `LICENSE` — MIT.
- Static unused-import audit → removed 5 truly-unused names
  (`Optional` in call_timer/sms_limiter, `Iterable` in
  twilio_signature, `ZoneInfoNotFoundError` in onboarding,
  `datetime/timezone` in feedback).
- `SHIP_REPORT.md` gains a v2.0 section summarizing all 8 upgrades,
  updated test counts, and a refreshed three-command go-live.

---

**v2 totals:** 265 passing pytest cases (up from 216).
Eight new commits plus matching docs commits.

---

# v3.0 major upgrade — V3.1–V3.18

Agency-ready scale-up pass. Informed by a deep competitive-research brief
against 2026 voice-agent platforms (Vapi, Retell, Bland, Synthflow,
Rosie, ServiceTitan Voice Agents, Trillet, Stammer, Convocore,
Marlie AI). Priorities picked from what agencies name in marketing +
close-deal scripts: white-label multi-tenancy, native bookings,
real-time sentiment, knowledge base, webhooks, ops-grade observability.

## V3.1 — Graceful LLM degradation _(complete)_

`llm.chat_with_usage` + `recover` now catch `anthropic.RateLimitError`,
`APITimeoutError`, `AuthenticationError`, and generic `APIError` +
`TypeError(no api_key)` inside the try/except. On failure, a randomized
canned "hang on" phrase is returned (categorized by reason) instead of
propagating an exception that would 503 the Twilio webhook and kill the
call. Module-level `degradation_stats()` counter surfaces reason
buckets; `/metrics` (V3.15) reads it. 13 tests.

## V3.2 — Context compression _(complete)_

`_build_messages` compresses turns older than `COMPRESS_THRESHOLD` (10)
into a single `[context recap]` prefix. Each older turn is truncated
to ~80 chars. Only activates on long SMS threads; voice calls with
their 240s cap almost never trip it. No extra LLM call for summary —
deterministic join keeps the feature cheap. 7 tests.

## V3.3 — SSML prosody tuning _(complete)_

`src/voice_style.py::apply_ssml(text, style)` XML-escapes then inserts
`<prosody rate/pitch>` + `<break>` at sentence and clause boundaries.
Three styles: `warm` (95% rate, soft pitch), `formal` (100% neutral),
`brisk` (108% faster, tight breaks). Opt-in via `voice_style` field in
a tenant YAML — omitting it keeps the plain-text path for backward
compat. `main.py._respond` now renders SSML when the tenant opts in.
16 tests.

## V3.4 — Per-call AI summary _(complete)_

`src/call_summary.py::generate_summary(call_sid)` reads the transcript
post-call and asks Claude Haiku for one sentence under 100 chars
summarizing what happened. Stored on a new `calls.summary` column
(lazy-migrated via `ALTER TABLE ADD COLUMN` wrapped in try/except).
Skipped for <30s calls and spam/silence/no-transcript outcomes. Surfaced
in the admin call log (new column), admin call detail, and client
portal. 10 tests.

## V3.5 — Knowledge base (RAG-lite) _(complete)_

`src/knowledge.py` parses `clients/<id>.knowledge.md` by `# Section`
headers and scores sections by keyword-overlap against the caller's
current message. Top matches inject into the volatile portion of the
system prompt as `## Relevant knowledge`. Stopword-filtered tokenizer,
dollar-sign-aware, lru-cached per process. Ships with a worked
`septic_pro.knowledge.md` covering pricing ($475 pump-out, $525
1500-gal, after-hours surcharges), drain-field repair, install ranges,
service area, emergency response, and maintenance cadence. Demo
prospects asking "how much?" now get real numbers. 19 tests.

## V3.6 — Booking capture _(complete)_

`src/bookings.py`: new `bookings` table (id, client_id, call_sid,
caller_phone, caller_name, address, requested_when, service, notes,
status, created_ts, month). `maybe_extract_from_call(call_sid)` fires
post-call when outcome=normal + intent=Scheduling + transcript exists;
calls Claude via `beta.messages.parse` with a `BookingExtraction`
Pydantic schema. `generate_ics(booking)` emits RFC 5545 calendar
invites. `/admin/bookings` lists them with status pills and call
cross-links. 15 tests.

## V3.7 — Real-time sentiment + auto-escalation _(complete)_

`llm.ChatResponse` gains a `sentiment` field
(neutral/positive/frustrated/angry). Prompt instructs Claude to
classify the CALLER's tone each turn. `src/sentiment_tracker.py`
counts consecutive hot turns per call; at threshold (default 2), the
turn's priority is auto-promoted to `high` so the call routes through
the existing emergency transfer flow — including the owner SMS brief.
Flag `ENFORCE_SENTIMENT_ESCALATION` (default on) + global kill.
14 tests.

## V3.8 — Agent personality _(complete)_

`src/personality.py` holds four snippet presets (warm/formal/brisk/
regional). Client YAMLs opt in via `personality: warm`. Snippet
appends to the stable (cacheable) portion of the system prompt —
doesn't invalidate prompt cache. 11 tests.

## V3.9 — Agency tenancy _(complete)_

`agencies/<id>.yaml` files declare `owned_clients`. `src/agency.py`
loads them (lru_cache) and exposes list/get/ownership helpers.
`/admin/agency/{agency_id}` shows an aggregate view scoped to the
agency's clients — same stat cards + per-client table as `/admin`,
filtered. Worked example `agencies/example_agency.yaml` (Acme AI
Agency owns ace_hvac + septic_pro). 12 tests.

## V3.10 — White-label portal branding _(complete)_

`design.page()` grows `brand_logo_url`, `brand_display_name`, and
`custom_accent_hex` parameters. Hex passes a strict `^#[a-f0-9]{6}$`
regex before landing in a scoped `<style>body[data-accent="custom"] {
--accent: ... }` block — CSS-injection-proof. Companion soft tint
computed via `_hex_soft`. Client YAMLs adopt three new fields
(`brand_accent_color`, `brand_logo_url`, `brand_display_name`). 13 tests.

## V3.11 — Hard usage cap _(complete)_

`src/usage_cap.py::is_capped(client)` checks `plan.hard_cap_calls`
against `usage.monthly_summary`. When hit, `/voice/incoming` plays a
polite "at capacity" message, hangs up, and records outcome='capped' —
no LLM tokens burned for a runaway-budget tenant. Flag
`ENFORCE_USAGE_HARD_CAP` default on + kill switch. 11 tests.

## V3.12 — Self-serve signup _(complete)_

`src/signup.py` adds `GET /signup` (form) + `POST /signup`
(tenant-creation endpoint). Validates company name, services, and
E.164-ish email regex. Reuses `onboarding._build_demo` + `_write_yaml`
for the 24h-expiry tenant, then mints a portal URL. Rate-limited
5/hour per IP via local bucket (separate from /admin's). Disable via
`ENFORCE_PUBLIC_SIGNUP=false`. 11 tests.

## V3.13 — Webhook event bus _(complete)_

`src/webhooks.py`: clients subscribe in YAML `webhooks: [{url, events,
secret}]`. Known events: `call.started`, `call.ended`,
`emergency.triggered`, `booking.created`, `feedback.negative`. HMAC-
SHA256 signs body with per-subscription secret → `X-AI-Receptionist-
Signature: sha256=<hex>`. 5-second timeout per delivery; `fire_safe`
wraps with outer try/except so a bad recipient never disrupts the
Twilio handler. Wired into main.py at call-end, booking-created,
emergency branch, and feedback-negative points. 15 tests.

## V3.14 — Live call monitoring _(complete)_

`/admin/live` renders `call_timer.snapshot()` as a table (call SID,
client, elapsed, emergency flag, turn count, latest caller line,
watch link). Meta `http-equiv=refresh content="3"` auto-reloads every
3 seconds. No JS. 6 tests.

## V3.15 — Prometheus /metrics _(complete)_

`src/ops.py` grows `/metrics` endpoint in Prometheus text-exposition
format. Emits: `receptionist_uptime_seconds`, `_active_calls`,
`_active_emergency_calls`, `_llm_degradations_total{reason}` (from
V3.1 stats), `_calls_total{client,outcome}`, `_minutes_total{client}`,
`_emergencies_total{client}`, `_margin_pct{client}`,
`_sentiment_escalations_active`. No auth required — scrapers don't
carry Basic. 10 tests.

## V3.16 — Eval response cache _(complete)_

`evals/cache.py::CachingChatFn` wraps a `chat_fn` with a key-lookup
memo layer. Key = sha256(case_id, user_message, client_id). Persists
to `data/eval_cache.jsonl` with a 30-day TTL on entries. Runner
integration via `use_cache` kwarg. `--no-cache` CLI flag forces
refresh. `EVAL_CACHE_DISABLE=true` env disables globally (tests
set this in conftest to avoid cross-test contamination). 15 tests.

## V3.17 — Docker + docker-compose _(complete)_

`Dockerfile` (python:3.12-slim, tini PID 1, requirements-first for
layer caching, non-root UID 1000, HEALTHCHECK on /health). `docker-
compose.yml` with app service + host-mounted volumes for data/ logs/
clients/ agencies/ evals/. Stub for a cloudflared sidecar in
comments. `.dockerignore` excludes .env, .git, tests/, docs/ so
image stays small. 12 tests (parse + invariant checks).

## V3.18 — Audit + CHANGES + SHIP_REPORT _(complete)_

Full pytest suite: **475 passed, 1 deselected** (up from v2's 265).
209 new tests across 17 feature modules.

Competitive positioning (from the v3 research brief):
 - Agencies need: unlimited sub-accounts (✓ V3.9), white-label
   branding (✓ V3.10), native bookings (✓ V3.6), webhooks into
   Zapier/Jobber/HubSpot (✓ V3.13), compliance-talk surfaces
   (HIPAA/SOC2 left as operator-responsibility).
 - 2026 tech bar: sub-500ms latency (voice pipeline unchanged —
   deferred for a dedicated latency sprint), SSML prosody (✓ V3.3),
   graceful degradation (✓ V3.1).
 - HVAC/plumbing/septic verticals: emergency keyword routing (✓ v1),
   after-hours-only deployment (✓ kill switch + usage cap V3.11),
   bilingual (✓ 9 langs from v1), per-unique-caller billing (deferred).

**v3 totals:** 475 passing pytest cases. 17 new feature commits + 1
final audit commit. Zero regressions on the ship-production baseline.

---

# v4.0 major upgrade — V4.1–V4.7

Voice quality + trust pass for blue-collar service businesses. Goal:
make the receptionist sound and feel as real as Cortana/Siri/Claude
voice/GPT voice — so a Bob's-Septic customer doesn't realize they're
talking to a bot in the first 5 seconds. Plus: never lie about prices,
real calendar integration, recall across calls.

## V4.1 — Pluggable TTS abstraction + ElevenLabs adapter _(complete)_

`src/tts.py` introduces TtsProvider with PollyProvider (default,
unchanged behavior) and ElevenLabsProvider (opt-in, generates audio
bytes via the streaming API, caches to data/audio/<sha256[:24]>.mp3,
serves via FastAPI /audio/<filename>.mp3 endpoint with strict path-
traversal validation). Per-tenant `tts_provider` + `tts_voice_id` +
`tts_voice_settings` YAML fields. Always falls back to Polly on any
error so a provider outage never drops a call. main._respond branches
on payload.kind ∈ {polly, play}. 22 tests.

## V4.2 — Natural speech preprocessing _(complete)_

`src/humanize_speech.py::humanize_for_speech(text)` runs before TTS to
turn "$475 at 4273 Mill Creek Road by 9:30 AM, call (555) 219-3987" into
"four hundred seventy-five dollars at forty-two seventy-three Mill Creek
Road by nine thirty A M, call five five five, two one nine, three nine
eight seven". stdlib only. Per-tenant `humanize_speech: false` opts out
(default ON). Internal exception handler returns raw text on any error.
56 tests covering currency, phones, times, addresses, and combined
sentences.

## V4.3 — Anti-robot scrubber _(complete)_

prompts/receptionist_core.md updated to ban "Certainly", "Absolutely",
"I apologize for the inconvenience", "So you're asking about X", "I
understand your concern", "How may I assist you today?", "Let me help
you with that". Added explicit varied-ack guidance ("got it / okay /
sure thing / mhm / yeah / no problem"). New `src/anti_robot.py::scrub`
post-processor strips robotic phrases anywhere in the reply, substitutes
"Certainly!" → "Sure," etc., and capitalizes the first letter after
stripping. Per-tenant `anti_robot_scrub: false` opts out (default ON).
21 tests.

## V4.4 — Strict grounding (anti-hallucination) _(complete)_

`src/grounding.py::verify_reply` extracts $-prices from the reply and
cross-checks against the tenant's pricing_summary + KB. Sentences
containing prices outside ±20% of any allowed value are replaced with
"Let me check the exact number — I'll have someone call you right back."
Multiple violations collapse to one fallback line. Per-tenant
`strict_grounding: false` opts out (default ON for v4+). Logs violation
count for audit. 25 tests.

## V4.5 — Twilio call recording + admin playback _(complete)_

`src/recordings.py` lazy-migrates recording_sid + recording_url +
recording_duration_s columns onto the calls table. New /voice/recording
webhook receives Twilio's recording-status callback when complete and
stores the metadata. /admin/call/{sid} renders an HTML5 <audio> player
sourced from /admin/recording/{sid}.mp3, a server-side proxy that adds
Twilio Basic auth so the operator's browser never sees the auth token.
Disclosure phrase ("This call may be recorded for quality") prepended
to the greeting when recording is on. Per-tenant `record_calls: true`
opts in (default OFF — privacy + storage). 19 tests.

## V4.6 — Per-tenant ICS calendar feed _(complete)_

`src/bookings.py::generate_feed_ics(bookings, tenant_name)` emits an
RFC 5545 multi-event VCALENDAR. New `src/calendar_feed.py` exposes
GET /calendar/{client_id}.ics?t=<token> with HMAC-signed token (reuses
CLIENT_PORTAL_SECRET). Bob pastes the URL into Google Calendar /
Apple Calendar / Outlook via "Add by URL" and bookings auto-appear,
refreshing hourly via X-PUBLISHED-TTL hint. CLI: `python -m
src.calendar_feed url <client_id>`. 16 tests.

## V4.7 — Cross-call recall _(complete)_

`src/recall.py::prior_calls` queries the calls table for non-spam
calls from the same number to the same tenant within max_days
(default 7), excluding the in-flight call. `build_recall_block`
renders a "## Recent calls from this same number" system-prompt block
with each prior call's when ("yesterday around 4 PM"), outcome, and
AI summary (V3.4). Soft guidance prompt: "If the caller is following
up on one of these, lead with that — 'hey, calling back about
yesterday?'". llm.chat_with_usage builds this automatically when the
caller has a phone. Cross-platform _humanize_when handles Windows
strftime quirks. 23 tests.

---

**v4 totals:** 657 passing pytest cases. 7 new feature modules:
src/tts, src/humanize_speech, src/anti_robot, src/grounding,
src/recordings, src/calendar_feed, src/recall. 7 new feature commits.
Zero regressions across v1.0/v2.0/v3.0/v4.0 surface.

Voice naturalness focus areas:
 - Voice quality: opt-in ElevenLabs (V4.1) + SSML prosody (V3.3)
 - Speech-rendering: prices/phones/times/addresses spoken human-like (V4.2)
 - No corporate-speak: "Certainly" stripped, varied acks instead (V4.3)

Trust focus areas:
 - Never invents prices (V4.4 strict grounding)
 - Recordings for audit + dispute resolution (V4.5)
 - Real calendar that Bob's phone will sync (V4.6)
 - "Hey, calling back about yesterday?" continuity (V4.7)

---

# v5.0 — Quality + refinement pass (V5.1–V5.5)

After four releases of feature work, v5 was a deliberate pause to find
the bugs that hide behind rapid feature delivery: leaks the test suite
never noticed, security gaps the audits missed, schema drift, broken
quiet tests. Five focused tasks; no new user-visible features. Code
deltas are small; the value is the regression guards added underneath.

## V5.1 — Shared-state leak audit _(complete)_

`src/sentiment_tracker.py` recorded per-call state on every turn but its
`record_end` was never wired into anything — every call leaked an entry.
Same shape on smaller scale in `src/security.py` (admin rate-limit
buckets per IP) and `src/signup.py` (signup rate-limit buckets per IP).
`src/scheduler.py` accumulated dedup keys forever to avoid double-firing
the daily digest.

Fixes:
 - `src/call_timer.py::record_end` now transitively clears
   sentiment_tracker state — the call-end webhook fans out to every
   per-call store from one place.
 - Hard caps with LRU-by-last-seen eviction:
   - `call_timer._calls`            → 5,000
   - `sentiment_tracker._tracked`   → 5,000
   - `security._buckets`            → 10,000
   - `signup._rate_limits`          → 5,000 + periodic empty-bucket prune
 - `scheduler.py` prunes dedup keys older than 14 days each tick.

Why caps not "fix the leak completely": a forgotten leak path can
re-leak silently. Caps make the worst case bounded even if a future
caller forgets to clean up. New regression suite
`tests/test_state_leaks.py` (12 tests) simulates 7,000-call churn and
asserts steady-state size for every store.

## V5.2 — Security + auth audit _(complete)_

Two real holes from v4:
 - `/admin/recording/{call_sid}.mp3` (V4.5) was missing
   `Depends(_check_auth)`. Anyone with the URL could fetch a recording
   even when admin Basic auth was configured. **Closed.**
 - `/voice/recording` (V4.5) was not in `PROTECTED_PATHS` —
   `X-Twilio-Signature` was never verified. An attacker could forge
   `RecordingUrl` to point at a malicious server and we'd happily store
   it in the calls table. **Closed.**

Plus a hardening sweep:
 - Path-traversal check on `/admin/recording/{call_sid}` rejects `..`,
   `/`, `\`. The audio served-from-disk endpoint already had this.
 - Extracted `src/admin_auth.py` so every admin route shares one auth
   helper. Backwards-compat shim in `src/admin.py` keeps the old
   `_check_auth` / `_auth_required` names working.

Regression suite `tests/test_security_audit.py` (20 tests):
 - `/admin/recording/*` requires auth when creds set; rejects wrong
   auth; stays open without creds (consistent with rest of `/admin`).
 - `/voice/recording` 403s when signature missing + enforcement on,
   passes with valid signature.
 - Path-traversal payloads on `/admin/recording/*` and `/audio/*`.
 - Cross-route admin auth sweep + cross-route Twilio-sig coverage.
 - Cross-tenant token rejection for `/client/*` and `/calendar/*.ics`.

## V5.3 — Pipeline order audit + the deselected test _(complete)_

`tests/test_llm_degradation.py::test_voice_gather_survives_llm_failure`
had been deselected since v4 because it hung indefinitely under
TestClient. Root cause: `TwilioSignatureMiddleware` re-yielded the body
to ASGI receive() but kept returning the same chunk forever. Newer
Starlette form-parsers loop on this waiting for more_body. **Fixed
in `src/twilio_signature.py`** — track delivery; second receive() call
returns `http.disconnect`, the proper end-of-stream signal.

Then the test itself was rewritten to bypass TestClient form parsing
entirely (it was stress-testing transport, not the feature). New
version monkey-patches `llm.chat_with_usage` and calls
`main._run_pipeline` directly to verify a degraded ChatResponse
propagates through `anti_robot → grounding → humanize → tts`.

Locked-in pipeline order in `tests/test_pipeline_order.py` (9 tests):
asserts the ordering with both positive (right order works) and
negative (wrong order corrupts grounding tokens) cases. If a future
refactor moves humanize before grounding, the regression test fires.

## V5.4 — DB migration consolidation + cross-tenant isolation _(complete)_

`src/call_summary.py` (V3.4) and `src/recordings.py` (V4.5) both did
their ALTER TABLE lazily on first write. That works but means a fresh
deploy is missing those columns until the right code path runs. Anyone
inspecting the schema right after `docker-compose up` would see drift.

`src/migrations.py` (new) consolidates every additive migration into one
idempotent `run_all()` that main.py's lifespan invokes at startup. Lazy
ALTERs in the original modules are KEPT (defense in depth) but become
no-ops after the consolidated pass. Non-raising — boots degraded rather
than failing to come up. PRAGMA cache + race-safe re-check handle the
case where two writers ALTER the same column concurrently.

Adding a future migration: append a `(table, column, ddl)` tuple to
`MIGRATIONS`. The next startup picks it up.

Tests:
 - `tests/test_migrations.py` (8 tests): empty DB, pre-V3.4 DB, fully
   migrated DB, double-run, non-existent table, mid-run race.
 - `tests/test_tenant_isolation.py` (12 tests): every customer-facing
   surface (admin, portal, calendar, recordings, transcripts, bookings)
   refuses to leak across tenants. Token signed for tenant A → 403 on
   tenant B's URL. `?client_id=B` query string on tenant A's portal
   URL is ignored.

## V5.5 — Dead code sweep + docs reality-check _(complete)_

Static unused-import audit caught two real dead imports:
 - `from src import call_summary` inside
   `src/admin.py::recent_calls` — left from a refactor where the route
   used to render summaries inline. Now the rows already carry the
   summary column.
 - `from typing import Optional` in `src/migrations.py` — never used.

Both removed. README badge bumped 657 → 719 tests, version v4.0 → v5.0.
SHIP_REPORT.md gains a v5 section. This block in CHANGES.md.

---

**v5 totals:** 719 passing pytest cases (60 new since v4.0). 1 new
module (`src/admin_auth.py`), 1 new system (`src/migrations.py`).
4 net code commits + 1 docs commit. Zero regressions.

What v5 changed in practice:
 - Three real leaks bounded; if a fourth slips in, the cap activates
   silently rather than running the box out of memory.
 - Two real auth holes closed with explicit regression guards.
 - One always-deselected test now runs (and passes).
 - DB schema is reproducible from `migrations.run_all()` alone.
 - Cross-tenant rejection is a regression suite, not just a code review.

What v5 deliberately did NOT do:
 - No new user-visible features. Pure quality pass.
 - No prompt-caching tweaks (P8 is still $0 on Haiku — unchanged).
 - No new TTS provider, voice cloning, or conversational rewrites.











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
