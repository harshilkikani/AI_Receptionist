# SHIP_REPORT — AI Receptionist

_Branches: `margin-protection-refactor` → `ship-production` (v1.0 @ tag `v1.0-ship-production`) → **v2.0 major upgrade**_

_Test suite: 265 pytest cases, all passing (~75 seconds). Legacy
`_test_suite.py` integration harness: 19 cases against a live server._

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
