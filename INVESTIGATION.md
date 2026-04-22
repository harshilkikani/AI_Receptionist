# INVESTIGATION — AI Receptionist (current architecture)

_Last updated: 2026-04-21_

Rewritten after the `ship-production` branch landed. For the pre-refactor
version see `_backups/PLAN.v1-margin.md` + the `margin-protection-refactor`
branch history.

## Stack

- **Language:** Python 3.14, stdlib-heavy.
- **Web:** FastAPI + Starlette middleware, Uvicorn at `:8765`.
- **LLM:** Anthropic Claude Haiku 4.5 via `anthropic` SDK v0.75
  (`client.beta.messages.parse` with Pydantic `ChatResponse`). System
  prompt sent as cacheable blocks (P8).
- **Telephony:** Twilio — voice (`/voice/*`) + SMS (`/sms/incoming`).
- **TTS/STT:** Polly Neural via Twilio `<Say>`; Twilio enhanced STT
  inside `<Gather>` with per-language codes.
- **Storage:** SQLite at `data/usage.db` (stdlib `sqlite3`, WAL mode),
  JSON memory store at `memory.json`, YAML client configs under
  `clients/`, JSON for rate card + alerts + spam lists under `config/`.
- **Scheduler:** in-process asyncio tasks — `src/alerts.py` (daily
  digest + monthly invoices) and `src/scheduler.py` (10 PM local owner
  digest per tenant + nightly eval regression).
- **Deps added across both branches:** `pyyaml`, `pytest`. No Redis,
  Postgres, Celery, APScheduler, Jinja2.

## Data flow (inbound call, happy path)

```
 Caller → Twilio PSTN
   ↓
 POST /voice/incoming (form-encoded)
   ↓ TwilioSignatureMiddleware validates X-Twilio-Signature
   ↓ SecurityHeadersMiddleware adds nosniff / no-referrer
 tenant.load_client_by_number(To)  — YAML lookup
 spam_filter.check_number(From)     — blocklist + area code
 memory.get_or_create_by_phone(From)
 call_timer.record_start(CallSid, client_id)
 usage.start_call(...)
   ↓
 Lang selection (menu or saved) → TwiML <Gather>
   ↓ (next turn)
 POST /voice/gather
   ↓ spam_filter.check_phrases(SpeechResult) (first 15s)
   ↓ call_timer.check(...)  → normal / soft_wrapup / hard_wrapup / force_end
   ↓ llm.chat_with_usage(caller, SpeechResult, history, client, wrap_up_mode)
       ↑ system = [{stable, cache_control: ephemeral}, {memory+suffix}]
   ↓ usage.log_turn(...)
   ↓ memory.append_turn(...)
 If priority=high:
   owner_notify.notify_emergency(...)     → SMS owner_cell
   vr.dial(escalation_phone)
   usage.end_call(outcome='emergency_transfer', emergency=True)
 Else:
   TwiML <Say> inside <Gather> → next caller turn
   ↓
 POST /voice/status (Twilio terminal callback)
   usage.end_call(CallSid, outcome)
   call_timer.record_end(CallSid)
   If CallStatus in {no-answer, busy, failed}: recovery SMS
   If CallStatus=completed + duration >= 30s + non-emergency:
     feedback.maybe_send_followup(...)    # P11, flag-gated
```

SMS follows the same shape through `/sms/incoming`. If the inbound body
classifies as YES/NO within 48h of a feedback ask,
`feedback.record_response` matches it and short-circuits the LLM.

## Multi-tenant

- Every client is a YAML under `clients/<id>.yaml`. Loader is
  `src/tenant.py`. Routing by `To` form field; `_`-prefixed IDs are
  reserved (never route). Missing `To` with a single real tenant
  configured → dev-convenience fallback to that tenant.
- Fields added on this branch: `owner_email`, `owner_cell`, `timezone`,
  optional plan fields `overage_rate_per_minute`, `included_sms_segments`,
  `overage_rate_per_sms`, `emergency_surcharge`. All default empty so
  existing ace_hvac routing is unchanged.
- Client configs are process-cached via `lru_cache`; `tenant.reload()`
  busts the cache (used by tests + onboarding wizard).

## Enforcement flags

| Flag | Default | Scope |
|---|---|---|
| `MARGIN_PROTECTION_ENABLED` | true | Global kill switch |
| `ENFORCE_CALL_DURATION_CAP` | false | 240s / 360s hard cap |
| `ENFORCE_SPAM_FILTER` | false | Number + phrase rejection |
| `ENFORCE_SMS_CAP` | false | Per-call SMS limit |
| `ENFORCE_USAGE_ALERTS` | true | Daily digest |
| `ENFORCE_OWNER_EMERGENCY_SMS` | true | Pre-transfer owner push (P3) |
| `ENFORCE_OWNER_DIGEST` | true | 22:00 local owner summary (P4) |
| `TWILIO_VERIFY_SIGNATURES` | true | Webhook signature 403s (P6) |
| `ENFORCE_EVAL_REGRESSION` | false | Nightly eval run (P7) |
| `PROMPT_CACHE_ENABLED` | true | cache_control on system prompt (P8) |
| `ENFORCE_FEEDBACK_SMS` | false | YES/NO post-call SMS (P11) |

Kill switch + per-feature flag compose: both must be on for enforcement
to actually activate.

## Client-facing surface

Signed per-tenant portal at `/client/{id}?t=<token>` (P1):
- Summary card (no cost/margin fields)
- Call log (timestamps, outcomes, intent, emergency flag)
- Printable monthly invoice (P2 — falls back to a simple view if
  `src/invoices.py` is unavailable)

Tokens are HMAC-SHA256 over `{client_id}|{issued_ts}` keyed by
`CLIENT_PORTAL_SECRET`; never expire, rotate by changing the secret.
CLI: `python -m src.client_portal issue <client_id>`.

## Ops tooling

- `python -m src.onboarding new` — interactive tenant creation (17
  validated prompts), writes YAML + prints webhook URLs + portal URL.
- `python -m src.onboarding new-demo` — 24h disposable tenant; startup
  auto-purges expired demos into `clients/_expired/`.
- `python -m src.invoices preview|csv|send|send-all` — manual invoice
  ops.
- `python -m src.client_portal issue <id>` — mint a signed portal URL.
- `python -m src.owner_digest preview|send <client_id> [YYYY-MM-DD]` —
  preview or force-send a daily digest.
- `python -m evals.runner` — replay 20 seeded eval cases against
  `llm.chat`.
- `python -m evals.regression_detector` — run + diff vs previous; alert
  on >5pp pass-rate drop.
- `python -m evals.cache_benchmark` — two-pass cache-hit measurement.
- `python scripts/reclaim_tunnel.py` — auto-reclaim cloudflared URL +
  repoint managed Twilio numbers.

Admin dashboard routes: `/admin` (overview), `/admin/calls`,
`/admin/analytics` (P10), `/admin/evals` (P7), `/admin/export.csv`,
`/admin/flags`, `/admin/alerts/trigger`. Basic-auth gated via
`ADMIN_USER` + `ADMIN_PASS`; rate-limited to 60 req/min per IP.

## Deployment

Target: single VM, uvicorn behind a Cloudflare tunnel.

1. `.env` from `.env.example` with the operator's credentials + secrets
   (Anthropic, Twilio, ADMIN_USER/PASS, CLIENT_PORTAL_SECRET).
2. `uvicorn main:app --host 0.0.0.0 --port 8765`.
3. `python scripts/reclaim_tunnel.py` OR Cloudflare Named Tunnel
   (preferred — see `ROLLOUT.md`).

Startup lifespan: purges expired demo tenants, starts alerts digest
loop, starts the per-tenant scheduler. Shutdown cancels both loops.

`data/usage.db` is the single operational truth for cost/margin/billing;
back it up alongside `clients/*.yaml` and `memory.json` during deploys.

For failure-mode remediation see `docs/OPS_RUNBOOK.md`.
