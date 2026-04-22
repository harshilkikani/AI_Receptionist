# SHIP_REPORT — ship-production branch

_Commit range: `b75d59f..HEAD`_
_Test suite: 216 pytest cases, all passing (60s + LLM integration tests
skipped without a real ANTHROPIC_API_KEY)._

## Per-section status

| # | Section | Status | Notes |
|---|---|---|---|
| P0 | Admin security (auth gate, rate limit, headers) | **done** | 8 tests. Middlewares installed. |
| P1 | Client-facing portal with signed URLs | **done** | 15 tests. CLI issues tokens. |
| P2 | Automated monthly billing | **done** | 16 tests. `enabled=false` default. |
| P3 | Owner emergency SMS push | **done** | 12 tests. Fires before transfer. |
| P4 | End-of-day owner digest | **done** | 15 tests. 22:00 local. |
| P5 | Onboarding wizard + demo tenants | **done** | 17 tests. Startup auto-purge. |
| P6 | Twilio webhook signature verification | **done** | 9 tests. Shadow-mode available. |
| P7 | Eval harness with cases.jsonl | **done** | 16 tests + 20 seed cases. |
| P8 | Prompt caching | **done (structure)** | 11 tests. Savings = $0 today (see below). |
| P9 | Persistent tunnel (auto-reclaim + Named Tunnel docs) | **done** | 11 tests. Option B wired. |
| P10 | Analytics admin page | **done** | 9 tests. No-JS tables + ASCII bars. |
| P11 | Post-call feedback SMS | **done** | 23 tests. `enabled=false` default. |

Plus:
- Route manifest check at `tests/test_routes.py` — fails if a router
  is added or removed without updating the manifest.
- All 19 legacy `_test_suite.py` integration tests still pass against
  a live server (now automatically sign their webhook POSTs if
  `TWILIO_AUTH_TOKEN` is available).

## Token savings (measured, P8)

Running `python -m evals.cache_benchmark --cases 3` against live
Anthropic with `claude-haiku-4-5`:

```
Pass 1 (cold): input_tokens = 4597   cache_read = 0
Pass 2 (warm): input_tokens = 4597   cache_read = 0
Saved:         0 tokens       $0.00
```

**Why zero:** the stable system block is 1,085 tokens. Claude Haiku 4.5
requires 4,096 tokens minimum for cache activation; blocks below the
minimum have their `cache_control` silently ignored. Sonnet 4.6 needs
2,048; Opus 1,024.

The structure is in place and correct — it activates automatically if
the prompt grows past the Haiku minimum or the operator migrates to
Sonnet/Opus. Full analysis + rate table in `CHANGES.md` → P8.

## Known gaps — do before first paying client

1. **`.env` secrets** — set `ADMIN_USER`, `ADMIN_PASS`,
   `CLIENT_PORTAL_SECRET` (32+ random chars), and
   `PUBLIC_BASE_URL` (your tunnel URL).
2. **Twilio webhook URLs** on `+18449403274` — currently point at
   whatever cloudflared URL was last active. Run
   `python scripts/reclaim_tunnel.py` once the server is up to
   auto-repoint.
3. **SMTP credentials** for invoices + digests — set
   `ALERT_SMTP_PASSWORD` in `.env` and fill `config/alerts.json::smtp`
   with host/port/user/from/to. Or switch transport to `webhook` and
   point at a Zapier catch hook.
4. **Shadow-mode walk** — keep `ENFORCE_SPAM_FILTER`,
   `ENFORCE_CALL_DURATION_CAP`, and `ENFORCE_SMS_CAP` at `false` for
   the first 48 hours. Watch `logs/rejected_calls.jsonl` and the
   call_timer log lines. Only flip each to `true` after observing clean
   behavior.
5. **Twilio signature verification** — start with
   `TWILIO_VERIFY_SIGNATURES=false`, confirm webhooks are shaped
   correctly (no `shadow-pass` warnings in logs), THEN flip to `true`.
6. **Monthly invoice go-live** — set
   `config/alerts.json::monthly_invoice.enabled=true` only after
   manually sending `python -m src.invoices send-all --month <prev>`
   once and reviewing the output.
7. **Eval regression scheduler** — `ENFORCE_EVAL_REGRESSION=false` by
   default (costs tokens). Flip to `true` once `cases.jsonl` is
   stable.
8. **`owner_cell` + `owner_email` + `timezone`** on every active
   client's YAML — these default empty and the new per-tenant features
   need them. Use `python -m src.onboarding new` for new tenants so
   you don't forget.

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
the admin is at `http://localhost:8765/admin` (with Basic auth from the
`ADMIN_USER`/`ADMIN_PASS` you set), and the per-tenant portal links
can be minted with `python -m src.client_portal issue <client_id>`.
