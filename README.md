# AI Receptionist

> A voice + SMS AI receptionist for service businesses. Answers missed calls in under a second, routes emergencies to the owner's cell with an SMS brief **before** the bridge, remembers callers across calls, bills per-minute automatically, and improves itself overnight from its own mistakes.

- **719 passing tests**
- **Real production stack** — FastAPI, Twilio, Anthropic Claude Haiku 4.5, SQLite
- **Multi-tenant** — one YAML per business, one Twilio number per tenant
- **Zero-framework frontend** — pure HTML + CSS for every UI surface
- **MIT licensed** — clone it, read it, change it

![status](https://img.shields.io/badge/tests-719%20passing-brightgreen)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-blue)
![version](https://img.shields.io/badge/version-v5.0-violet)

---

## Try the live demo

```bash
git clone https://github.com/harshilkikani/AI_Receptionist.git
cd AI_Receptionist
pip install -r requirements.txt
cp .env.example .env
# edit .env — at minimum set ANTHROPIC_API_KEY and ADMIN_USER/ADMIN_PASS
uvicorn main:app --port 8765
```

Open `http://localhost:8765` — the embedded chat demos the septic tenant
live. `/admin` is the operator dashboard. `/client/septic_pro?t=<token>`
is the client-facing portal (mint a token with
`python -m src.client_portal issue septic_pro` after setting
`CLIENT_PORTAL_SECRET`).

Full three-command go-live: see [`SHIP_REPORT.md`](SHIP_REPORT.md).

## What it does

| | |
|---|---|
| 📞 **Voice + SMS intake** | Twilio webhooks → FastAPI → Claude → Polly Neural. Barge-in, 9 languages, caller's choice. Optional per-tenant SSML prosody tuning (V3.3). |
| 🧠 **Multi-call memory** | Keyed by phone number. Returning callers greeted by name with address + history pre-loaded into the prompt. |
| 🚨 **Emergency routing** | Keyword-tuned + sentiment-aware (V3.7). Transfers to `escalation_phone` with a pre-bridge SMS brief to `owner_cell`. |
| 📚 **Knowledge base** (V3.5) | Per-tenant `<id>.knowledge.md` injected into the prompt via keyword match. Prices, hours, service area come out accurately. |
| 📅 **Booking capture** (V3.6) | Post-call extraction pulls name/address/when/service into a `bookings` table. Admin dashboard + ICS generation. |
| 💰 **Margin protection** | Per-client plans, call-duration caps, spam filter, SMS cap, **hard usage cap** (V3.11). All feature-flagged. |
| 🏢 **Agency multi-tenancy** (V3.9) | `agencies/<id>.yaml` declares owned clients. `/admin/agency/{id}` shows aggregate metrics for the agency only. |
| 🎨 **White-label branding** (V3.10) | Per-tenant logo, accent color, display name on the client portal. |
| 📊 **Admin dashboard** | Margin table, call log with AI summaries, per-call transcripts, analytics, evals, bookings, live in-flight calls, feature flags, CSV export. |
| 🔐 **Client portal** | `/client/{id}?t=<signed-token>` — one bookmarkable URL per tenant. HMAC-signed, rotate via secret. |
| 📆 **Automated billing** | Monthly invoices via SMTP or webhook on the 1st. HTML + CSV. |
| 🛠️ **Onboarding** | CLI wizard + **public /signup form** (V3.12) — a prospect gets a working demo tenant in 60 seconds. |
| 🧪 **Self-improving evals** | 25 seed cases, nightly regression detection, negative-feedback-to-eval pipeline, **response cache** (V3.16) for cheap re-runs. |
| 🔌 **Webhook event bus** (V3.13) | Clients subscribe to `call.ended`, `booking.created`, `emergency.triggered`, `feedback.negative`. HMAC-signed POSTs. |
| 🩺 **Ops-ready** | `/health`, `/ready`, **`/metrics`** (V3.15) in Prometheus format, `X-Request-ID` correlation, structured logging. |
| 🛡️ **Graceful degradation** (V3.1) | LLM rate limits / timeouts / auth failures get canned-response fallbacks so the call never 503's out. |
| 🐳 **Docker-ready** (V3.17) | `docker-compose up` — image with tini, health checks, non-root user. |
| 🎤 **Pluggable TTS** (V4.1) | Polly default, ElevenLabs Conversational opt-in via cached MP3 + `<Play>`. Falls back to Polly on any error. |
| 💬 **Natural speech** (V4.2) | Prices, phones, addresses, times spoken human-like ("$475" → "four hundred seventy-five dollars"). |
| 🤖❌ **Anti-robot scrubber** (V4.3) | Strips "Certainly / I understand your concern / Let me help you with that" before TTS. |
| 🛡️ **Strict grounding** (V4.4) | Replaces invented prices with "Let me check the exact number — I'll have someone call you right back." |
| 🎙️ **Call recording + playback** (V4.5) | Twilio recordings surfaced in admin via server-proxied audio player. Disclosure prepended. |
| 📅 **ICS calendar feed** (V4.6) | `/calendar/{id}.ics?t=<token>` subscribes in Google/Apple/Outlook — Bob's bookings auto-sync. |
| ⏪ **Cross-call recall** (V4.7) | "Hey, calling back about yesterday?" — prior calls from same number injected into prompt. |

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full diagram
and request lifecycle. Short version:

```
Caller → Twilio → Cloudflare Tunnel → FastAPI
                                        │
                    middleware stack: RequestID → SecurityHeaders → AdminRateLimit → TwilioSig
                                        │
                          tenant lookup (YAML) → spam filter → call timer → llm → usage log
                                        │
              emergency? ──── owner_notify (SMS preview) ──→ Twilio Dial
                                        │
                        /voice/status ──── post-call feedback SMS (opt-in)
                                        │
                 background: alerts digest, owner digest, eval regression
```

## Repo tour

```
main.py                   FastAPI app + Twilio webhook routes
llm.py                    Claude wrapper, prompt caching
memory.py                 caller JSON store
prompts/                  system prompt template
clients/                  one YAML per tenant (ace_hvac live, septic_pro demo)
config/                   rate card, alerts config, spam lists
src/
  tenant.py               YAML loader + routing
  design.py               shared CSS + render helpers (admin/portal/landing)
  admin.py                /admin/* dashboard
  client_portal.py        /client/{id}/* — signed URL portal
  usage.py                SQLite-backed call/turn/SMS tracking
  invoices.py             monthly billing generator
  call_timer.py           240s / 360s hard cap
  spam_filter.py          number + phrase rejection with override keywords
  sms_limiter.py          per-call SMS cap + length truncation
  alerts.py               daily digest + monthly invoice scheduler
  owner_notify.py         pre-transfer emergency SMS to owner
  owner_digest.py         22:00-local daily summary
  owner_commands.py       HELP SMS + welcome flow
  feedback.py             post-call YES/NO capture + negative-transcript dump
  transcripts.py          per-call conversation store
  scheduler.py            per-tenant timezone-aware async loop
  security.py             rate-limit + security-headers middlewares
  twilio_signature.py     X-Twilio-Signature verification middleware
  ops.py                  /health, /ready, request-id middleware
  onboarding.py           interactive tenant-creation CLI
evals/
  cases.jsonl             25 seeded cases (HVAC + septic)
  runner.py               replay + score
  regression_detector.py  nightly diff + alert
  cache_benchmark.py      prompt-caching savings measurement
scripts/
  reclaim_tunnel.py       auto-capture cloudflared URL + repoint Twilio
tests/                    719 pytest cases
_test_suite.py            19-case legacy integration suite (live server)
docs/                     ARCHITECTURE, OPS_RUNBOOK, CLIENT_PORTAL, INVOICES,
                          EVALS, DEMO_SCRIPT, SHOWCASE_SCRIPT, NEW_CLIENT_CHECKLIST
```

## Running the tests

```bash
pytest tests/             # 719 cases, ~65 seconds
python _test_suite.py     # legacy 19-case integration suite (needs a live server)
```

## Key docs

- [`SHIP_REPORT.md`](SHIP_REPORT.md) — status per P + v2 section,
  three-command go-live, measured P8 prompt-cache savings
- [`CHANGES.md`](CHANGES.md) — per-section change log across both
  branches (commit-by-commit)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system diagram +
  request lifecycle trace
- [`docs/OPS_RUNBOOK.md`](docs/OPS_RUNBOOK.md) — 10 failure modes with
  exact diagnose + fix commands
- [`docs/NEW_CLIENT_CHECKLIST.md`](docs/NEW_CLIENT_CHECKLIST.md) —
  onboarding flow (wizard-first, manual fallback)
- [`docs/SHOWCASE_SCRIPT.md`](docs/SHOWCASE_SCRIPT.md) — 3-minute
  website video script (septic-flavored)
- [`docs/EVALS.md`](docs/EVALS.md) — eval harness anatomy + regression
  workflow

## Design principles

- **No Jinja, no templating engine, no JS framework.** Inline HTML
  strings + a shared CSS module. Every surface readable top-to-bottom.
- **Feature-flag every enforcement.** Flags default to `false` for risky
  things (call-duration cap, spam filter) and `true` for safe ones
  (emergency SMS, signature verification). Global kill switch too.
- **Shadow-mode first.** Every enforcement module can log what it WOULD
  do without actually doing it. Operator tunes before tightening.
- **Stateless per-tenant URLs.** HMAC-signed, no token table to manage.
  Rotate by changing the secret.
- **One source of truth.** `data/usage.db` for metrics + billing,
  `clients/*.yaml` for tenant config, `memory.json` for caller history.

## License

MIT — see [`LICENSE`](LICENSE).
