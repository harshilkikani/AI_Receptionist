# AI Receptionist

> A voice + SMS AI receptionist for service businesses. Answers missed calls in under a second, routes emergencies to the owner's cell with an SMS brief **before** the bridge, remembers callers across calls, bills per-minute automatically, and improves itself overnight from its own mistakes.

- **265 passing tests**
- **Real production stack** — FastAPI, Twilio, Anthropic Claude Haiku 4.5, SQLite
- **Multi-tenant** — one YAML per business, one Twilio number per tenant
- **Zero-framework frontend** — pure HTML + CSS for every UI surface
- **MIT licensed** — clone it, read it, change it

![status](https://img.shields.io/badge/tests-265%20passing-brightgreen)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-blue)

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
| 📞 **Voice + SMS intake** | Twilio webhooks → FastAPI → Claude → Polly Neural. Barge-in enabled so the caller can interrupt. 9 languages, caller's choice. |
| 🧠 **Multi-call memory** | Keyed by phone number. Returning callers greeted by name with address + prior history loaded into the prompt. |
| 🚨 **Emergency routing** | Keyword-tuned intent classification. On `priority: high` the caller is transferred to `escalation_phone` AND a briefing SMS hits `owner_cell` before the bridge connects. |
| 💰 **Margin protection** | Per-client plans, call-duration caps, spam filter, SMS cap, all feature-flagged so every enforcement feature can be rolled out in shadow mode first. |
| 📊 **Admin dashboard** | `/admin` — margin by tenant, call log, analytics (intent distribution, hour-of-day heatmap, MoM trend), feature flags, CSV export. |
| 🔐 **Client portal** | `/client/{id}?t=<signed-token>` — one bookmarkable URL per tenant. Calls, invoice, no cost/margin. Rotate by changing the HMAC secret. |
| 📆 **Automated billing** | Monthly invoices generated + sent via SMTP or webhook on the 1st. HTML body + CSV attachment. |
| 🛠️ **Onboarding wizard** | `python -m src.onboarding new` walks 17 validated prompts, writes a YAML, prints the Twilio webhook URLs. `new-demo` mints a 24h disposable tenant. |
| 🧪 **Self-improving evals** | 25 seed cases in `evals/cases.jsonl`. Regression detector runs nightly and alerts on >5pp pass-rate drop. Failed YES/NO customer feedback becomes new test cases. |
| 📝 **Call transcripts** | Every turn stored. Admin + client portal both have per-call detail views. |
| 🩺 **Ops-ready** | `/health`, `/ready`, `X-Request-ID` correlation, structured logging. |

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
tests/                    265 pytest cases
_test_suite.py            19-case legacy integration suite (live server)
docs/                     ARCHITECTURE, OPS_RUNBOOK, CLIENT_PORTAL, INVOICES,
                          EVALS, DEMO_SCRIPT, SHOWCASE_SCRIPT, NEW_CLIENT_CHECKLIST
```

## Running the tests

```bash
pytest tests/             # 265 cases, ~75 seconds
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
