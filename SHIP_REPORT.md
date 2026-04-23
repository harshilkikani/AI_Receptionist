# SHIP_REPORT — AI Receptionist

_Branches: `margin-protection-refactor` → `ship-production` (v1.0 @ tag `v1.0-ship-production`) → **v2.0 major upgrade** → **v3.0 major upgrade (current tip)**_

_Test suite: **475 pytest cases, all passing** (~70 seconds). Legacy
`_test_suite.py` integration harness: 19 cases against a live server.
209 new tests since v2.0._

---

# v1.0 — Margin protection + production ship (P0–P11)

See `CHANGES.md` for the per-P details. Status summary:

| # | Section | Status |
|---|---|---|
| P0 | Admin security (auth gate, rate limit, headers) | done |
| P1 | Client-facing portal with signed URLs | done |
| P2 | Automated monthly billing | done |
| P3 | Owner emergency SMS push | done |
| P4 | End-of-day owner digest | done |
| P5 | Onboarding wizard + demo tenants | done |
| P6 | Twilio webhook signature verification | done |
| P7 | Eval harness with cases.jsonl | done |
| P8 | Prompt caching | done (structure; $0 savings on Haiku today — see below) |
| P9 | Persistent tunnel (auto-reclaim + Named Tunnel docs) | done |
| P10 | Analytics admin page | done |
| P11 | Post-call feedback SMS | done |

**Measured P8 savings (honest):** $0 on Claude Haiku 4.5 because the
stable prompt block is 1,085 tokens and Haiku's cache minimum is 4,096.
Structure is correct and activates automatically if the prompt grows
past the threshold or you migrate to Sonnet 4.6 (min 2,048) or
Opus (min 1,024). Full analysis in `CHANGES.md` → P8.

---

# v2.0 — Major upgrade (V1–V8)

A post-v1.0 audit pass that rebuilt the UI, pivoted the demo to septic
services, added two user-visible features, and professionalized the
repo.

| # | Section | Status | Highlights |
|---|---|---|---|
| V1 | Septic demo pivot | done | `septic_pro` tenant, 3 septic seed callers, rewritten `SHOWCASE_SCRIPT.md`, 5 septic eval cases. |
| V2 | Shared design system + admin v2 | done | `src/design.py`, sidebar nav, SVG sparklines + heatbars, pill-styled outcomes, dark-mode ready. |
| V3 | Client portal v2 | done | Same design system (teal accent), stat cards, per-call detail link, print-friendly invoice. |
| V4 | Call transcripts (new feature) | done | Every turn stored. Admin + portal per-call detail pages with explicit tenant-isolation. |
| V5 | Landing page rebuild | done | `index.html` is now a showcase landing with embedded septic-flavored live chat demo. |
| V6 | HELP SMS + welcome flow (new feature) | done | Owners texting HELP get portal URL + escalation; unknown callers get a polite redirect. `onboarding welcome` CLI subcommand. |
| V7 | /health + /ready + request-id | done | Ops probes + request correlation middleware + `[request_id]` in log lines. |
| V8 | Audit + README + LICENSE | done | Static unused-import sweep. Professional README with status badges, repo tour, quick-start. MIT LICENSE. This file updated. |

## What's new between v1.0 and v2.0

**Two new user-visible features:**
- **Call transcripts** — conversation replay for operator + client.
- **HELP SMS command** — owners text HELP and get a cheat sheet.

**Two fresh surfaces:**
- **Showcase landing page** — `/` is now a proper marketing page.
- **Admin + portal UI** — redesigned on a shared design system.

**Ops additions:**
- `/health`, `/ready`, `X-Request-ID` header + contextvar, structured logging.

---

# Known gaps — do before first paying client

Unchanged from v1.0 plus v2-specific notes:

1. **`.env` secrets** — `ADMIN_USER`, `ADMIN_PASS`,
   `CLIENT_PORTAL_SECRET` (32+ random chars), `PUBLIC_BASE_URL`.
2. **Twilio webhook URLs** — `python scripts/reclaim_tunnel.py`
   repoints them to the current tunnel automatically.
3. **SMTP or webhook** for `config/alerts.json` — invoices + digests.
4. **Shadow-mode walk** — keep `ENFORCE_SPAM_FILTER`,
   `ENFORCE_CALL_DURATION_CAP`, `ENFORCE_SMS_CAP` at `false` for
   48h. Review `logs/rejected_calls.jsonl` and `logs/` before flipping.
5. **`TWILIO_VERIFY_SIGNATURES`** — start with `false`, flip to `true`
   after confirming webhooks shaped correctly (no `shadow-pass`
   warnings in logs).
6. **Monthly invoices** — set
   `config/alerts.json::monthly_invoice.enabled=true` only after
   manually running `python -m src.invoices send-all --month <prev>`
   once + reviewing output.
7. **Nightly eval regression** — `ENFORCE_EVAL_REGRESSION=false` by
   default (spends tokens). Flip after `cases.jsonl` is stable.
8. **Every client YAML** — `owner_cell`, `owner_email`, `timezone`
   fields. Defaults are empty. Wizard prompts for all three.
9. **V2-new:** `ENFORCE_FEEDBACK_SMS=false` by default; turn on per
   client when you want the YES/NO self-improvement loop.
10. **V2-new:** send the welcome SMS on day 1 —
    `python -m src.onboarding welcome <client_id>`.

## Three commands to go live

```bash
# 1. Install deps + run tests against a fresh clone
pip install -r requirements.txt && pytest tests/ && python -m src.onboarding purge-expired

# 2. Bring up the app (assumes .env is filled)
uvicorn main:app --host 0.0.0.0 --port 8765

# 3. In a second terminal, start the tunnel + auto-repoint Twilio
python scripts/reclaim_tunnel.py
```

After those three, `+18449403274` answers live as Joanna / Ace HVAC,
the admin is at `http://localhost:8765/admin`, the showcase landing is
at `http://localhost:8765/`, and per-tenant portal links are minted
with `python -m src.client_portal issue <client_id>`.

---

# Operating model reminder

- **Global kill switch:** `MARGIN_PROTECTION_ENABLED=false` → all
  enforcement bypassed instantly, no restart needed if running with
  a reloader.
- **Ops page:** `/admin/flags` shows current flag state.
- **Problem triage:** `docs/OPS_RUNBOOK.md` — 10 failure modes with
  diagnose + fix commands.
- **Architecture:** `docs/ARCHITECTURE.md` — system diagram +
  request lifecycle trace.

---

# v3.0 — Agency-ready scale-up (V3.1–V3.18)

Informed by competitive research against 2026 voice-agent platforms
(Vapi, Retell, Bland, Synthflow, Rosie, ServiceTitan Voice Agents,
Trillet, Stammer, Convocore). Focus: features agencies need to close
SMB deals quickly + ops hardening.

| # | Section | Highlights |
|---|---|---|
| V3.1 | Graceful LLM degradation | Canned "hang on" phrases when Claude rate-limits / times out / auth-fails. Keeps the Twilio webhook alive instead of 503-ing the call. |
| V3.2 | Context compression | Long SMS threads get old turns folded into a `[context recap]` prefix. Prompt stays bounded on 15+ turn conversations. |
| V3.3 | SSML prosody tuning | Polly breaks + rate/pitch per tenant's `voice_style` field. 3 presets (warm/formal/brisk). Opt-in. |
| V3.4 | Per-call AI summary | 1-line summary auto-generated post-call, stored in calls table, surfaced in admin call log + detail. |
| V3.5 | **Knowledge base (RAG-lite)** | Per-tenant `clients/<id>.knowledge.md`. Keyword-match against caller message; inject relevant sections into prompt. Ships with a septic_pro pricing/services KB. |
| V3.6 | **Booking capture** | `bookings` table + LLM extraction (name/address/when/service) from post-call transcript. `/admin/bookings` dashboard. ICS generation. |
| V3.7 | Real-time sentiment | ChatResponse gains `sentiment` field. Auto-escalate to emergency transfer after N consecutive frustrated/angry turns. |
| V3.8 | Agent personality | `personality: warm|formal|brisk|regional` per tenant. Snippet appended to cacheable prompt block. |
| V3.9 | **Agency tenancy** | `agencies/<id>.yaml` owns client IDs. `/admin/agency/{id}` aggregate view. Enables agency resellers. |
| V3.10 | **White-label portal branding** | Per-tenant accent color, logo URL, display name. CSS-injection-proof strict hex validation. |
| V3.11 | Hard usage cap | `plan.hard_cap_calls` auto-disables runaway tenants with a polite caller message. No LLM tokens spent past cap. |
| V3.12 | **Self-serve signup** | Public `/signup` form → 24h demo tenant + portal URL. Rate-limited 5/hour/IP. |
| V3.13 | **Webhook event bus** | Clients subscribe to `call.ended`, `booking.created`, `emergency.triggered`, `feedback.negative` via YAML. HMAC-SHA256-signed POSTs. |
| V3.14 | Live call monitoring | `/admin/live` with meta-refresh shows in-flight calls + latest caller line. |
| V3.15 | **Prometheus /metrics** | Scrape-friendly text format. Uptime, active calls, LLM degradations, per-client call counts, margin. |
| V3.16 | Eval response cache | SHA256-keyed cache for repeated eval runs. `EVAL_CACHE_DISABLE` env gate. |
| V3.17 | **Docker + docker-compose** | Production-shaped image (slim, non-root, tini, healthcheck). Compose stub for cloudflared sidecar. |
| V3.18 | Audit + CHANGES + SHIP_REPORT | This file. Full suite: 475 passing, 1 deselected. Zero regressions. |

## Three-command go-live (updated for v3)

Preferred path — Docker:

```bash
# 1. Fill .env, then build + run
cp .env.example .env  # edit values
docker-compose up -d --build

# 2. (one-time) auto-repoint Twilio webhooks to the cloudflared URL
python scripts/reclaim_tunnel.py

# 3. Mint client portal URLs
python -m src.client_portal issue ace_hvac
python -m src.client_portal issue septic_pro
```

Legacy path (bare-metal) still works:
```bash
pip install -r requirements.txt && pytest tests/
uvicorn main:app --host 0.0.0.0 --port 8765
python scripts/reclaim_tunnel.py     # second terminal
```

## New env vars introduced in v3

| Var | Default | Purpose |
|---|---|---|
| `ENFORCE_SENTIMENT_ESCALATION` | `true` | V3.7 auto-escalate on frustrated/angry caller |
| `SENTIMENT_ESCALATE_AFTER` | `2` | V3.7 consecutive hot turns before escalation |
| `ENFORCE_USAGE_HARD_CAP` | `true` | V3.11 plan.hard_cap_calls enforcement |
| `ENFORCE_PUBLIC_SIGNUP` | `true` | V3.12 /signup form on/off |
| `SIGNUP_RATE_LIMIT_PER_HOUR` | `5` | V3.12 per-IP rate limit |
| `EVAL_CACHE_DISABLE` | *(unset)* | V3.16 force fresh LLM calls in evals |
| `SUMMARY_MODEL` | `claude-haiku-4-5` | V3.4 override summarization model |
| `BOOKING_MODEL` | `claude-haiku-4-5` | V3.6 override extraction model |

## New YAML fields introduced in v3

Optional per tenant:
- `voice_style: warm|formal|brisk` (V3.3)
- `personality: warm|formal|brisk|regional` (V3.8)
- `brand_accent_color: "#hexhex"` (V3.10)
- `brand_logo_url: "https://..."` (V3.10)
- `brand_display_name: "..."` (V3.10)
- `plan.hard_cap_calls: N` (V3.11)
- `webhooks: [{url, events, secret}]` (V3.13)

New `agencies/<id>.yaml` format:
- `id, name, contact_email, owned_clients: [...]` (V3.9)

New `clients/<id>.knowledge.md` (V3.5) — optional per-tenant knowledge
base, plain markdown with `# Section` headers.

## Gaps deferred to v4

- **Sub-500ms latency** — requires a speech-to-speech backend
  (gpt-realtime, Sonic-3, or ElevenLabs Conversational). Voice pipeline
  unchanged in v3; would need a new transport layer.
- **Native ServiceTitan / Jobber / Housecall Pro integrations** —
  webhook event bus (V3.13) covers most use cases via Zapier; direct
  CRM writers are a bigger lift.
- **MCP tool-calling** for live CRM reads during calls — research
  flagged this as the 2026 buzzword but real adoption is early.
- **Per-unique-caller billing** — alternate plan model alongside
  per-minute. Small YAML/usage change, didn't prioritize here.
- **Voicemail detection (AMD)** for outbound — the current outbound
  callback queue stub (not shipped in v3) would need AMD.
- **Warm transfer whisper** — owner_notify sends an SMS brief right
  before the dial; a true whisper (AI narrates context as owner
  connects) is a larger Twilio Conference refactor.
