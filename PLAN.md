# PLAN — Margin Protection + Multi-Tenant Refactor

Ordered sections. Each labeled: complexity (S/M/L), risk (low/med/high), files touched.

---

## Phase 1 — Baseline Safety

| Step | Complexity | Risk | Files |
|---|---|---|---|
| 1.1 `git init` + branch `margin-protection-refactor` + baseline commit | S | low | `.git/`, `.gitignore` |
| 1.2 Snapshot existing configs to `_backups/` (gitignored) | S | low | `_backups/*` |
| 1.3 Write `.env.example` with all current + new vars documented | S | low | `.env.example` |

---

## Phase 2 — Implementation

### Section G — Multi-Tenant Client Configs *(done first — everything else needs it)*

| Complexity | Risk | Files |
|---|---|---|
| L | **high** — touches prompt loading + routing | `clients/_template.yaml`, `clients/_default.yaml`, `clients/ace_hvac.yaml`, `clients/example_client.yaml`, `src/tenant.py` (new), `main.py`, `llm.py` |

**Why first:** every downstream section (duration cap, SMS limits, alerts) is per-client configurable. Establishing the tenant-loading abstraction first avoids rework.

**Design:**
- YAML per client under `clients/<id>.yaml`
- Module `src/tenant.py` exposes `load_client_by_number(phone)` and `load_default()`
- Existing hardcoded "Ace HVAC" becomes `clients/ace_hvac.yaml` mapped to `+18449403274`
- Fallback: `clients/_default.yaml` used if no match

### Section B — System Prompt Refactor

| Complexity | Risk | Files |
|---|---|---|
| M | med — changes every LLM call | `prompts/receptionist_core.md`, `prompts/_deprecated/v1_inline.md`, `llm.py`, `src/tenant.py` |

**Design:**
- Extract prompt from `llm.py` to `prompts/receptionist_core.md` with `{{var}}` template slots
- Slots: `{{company_name}}`, `{{owner_name}}`, `{{services}}`, `{{pricing_summary}}`, `{{service_area}}`, `{{hours}}`, `{{escalation_phone}}`, `{{emergency_keywords}}`
- `llm.py` now reads template file once on startup, renders with client context per call
- Old inline prompt preserved in `prompts/_deprecated/v1_inline.md`

### Section E — Usage Tracking + Rate Card

| Complexity | Risk | Files |
|---|---|---|
| L | med — adds SQLite + DB writes on every turn | `src/usage.py` (new), `config/rate_card.json`, `data/usage.db` (runtime), `main.py` |

**Design:**
- SQLite schema: `calls`, `turns`, `sms` tables
- Per-turn: track input_tokens, output_tokens, tts_chars, duration_ms
- Per-call: client_id, from_number, start_ts, end_ts, outcome, total_cost, sms_count
- `calc_cost(client_id)` aggregates using `config/rate_card.json`
- `monthly_summary(client_id, month)` returns full metrics
- Cost tracking works in shadow mode — always on regardless of flags (just data collection)

### Section A — Call Duration Hard Cap

| Complexity | Risk | Files |
|---|---|---|
| M | **high** — can truncate live calls | `main.py`, `src/call_timer.py` (new), `src/tenant.py` |

**Design:**
- Each call tracked by `CallSid` in an in-memory dict: `{sid: {start_ts, client_id, emergency_detected, grace_extended}}`
- On every turn (`/voice/gather`), check elapsed:
  - 180s → inject wrap-up signal into system prompt via `wrap_up=True` flag
  - 225s → final wrap-up message
  - 240s (or 360s if emergency) → force-end with `<Hangup>`
- Grace period: if actively collecting critical info (checked by recent conversation keywords), allow 15s extension
- Feature-flag `ENFORCE_CALL_DURATION_CAP` — when false, log only

### Section C — Spam + Junk Filtering

| Complexity | Risk | Files |
|---|---|---|
| M | med — wrong rejections = lost calls | `src/spam_filter.py` (new), `config/spam_blocklist.json`, `config/spam_phrases.json`, `main.py`, `logs/rejected_calls.jsonl` (runtime) |

**Design:**
- Silence gate: `speechTimeout="auto"` catch at call start; if 5s no speech → end + log
- Number blocklist: JSON, checked at `/voice/incoming`
- Phrase detection: scan first 15s of transcript for configurable phrases
- Override list: if caller has mentioned address/service/emergency words, bypass filter
- All rejections → `logs/rejected_calls.jsonl`
- Feature-flag `ENFORCE_SPAM_FILTER`

### Section D — SMS Rate Limiting

| Complexity | Risk | Files |
|---|---|---|
| S | low | `main.py`, `src/tenant.py`, `src/usage.py` |

**Design:**
- Per-call SMS counter
- Before sending, check `usage.sms_count_for_call(sid) < client.sms_max_per_call`
- If cap reached, skip send + log
- Length check: 160 chars preferred, 320 absolute max (truncate with warning)
- Feature-flag `ENFORCE_SMS_CAP`

### Section F — Alerting

| Complexity | Risk | Files |
|---|---|---|
| M | low | `src/alerts.py` (new), `config/alerts.json`, `main.py` |

**Design:**
- SMTP OR webhook (both supported, operator picks one in `config/alerts.json`)
- Thresholds: 60% log, 80% notify, 100% notify+overage flag, 150% urgent
- Default: daily digest (batched), not per-event
- Runs via startup cron job (APScheduler optional — if heavy, replace with `schedule` lib or simple asyncio task)
- Actually: use FastAPI's `startup` event + `asyncio.create_task` with 24h sleep loop — zero new deps
- Feature-flag `ENFORCE_USAGE_ALERTS` (default **true** — alerts are safe)

### Section H — Voice Tier Optimization

| Complexity | Risk | Files |
|---|---|---|
| S | low | `main.py`, `src/tenant.py` |

**Design:**
- Two voice names per language: `voice_main` and `voice_transactional`
- Transactional phrases = short confirmations, goodbyes, "Got it"
- For Polly, use same voice at lower tier... actually Polly Neural voices don't have tiers in Twilio's pricing — all Neural voices are same cost. In this codebase, the win is minimal. **Document the limitation** and add the config scaffold anyway for future TTS provider switches (e.g., ElevenLabs Flash).
- Config: `voice_tier_main` / `voice_tier_transactional` in client YAML
- No immediate cost savings on Polly — scaffold only

### Section I — Conversation Flow Audit

| Complexity | Risk | Files |
|---|---|---|
| M | low (prompt-level change only) | `prompts/receptionist_core.md` |

**Design:**
- Enhance system prompt with batched-question instruction
- Add "EARLY EXIT PATHS" list for non-service queries (wrong number, hours only, directions only)
- Add "COLLECT ALL AT ONCE" guidance: "grab name + address + callback in one question"

### Section J — Admin Dashboard

| Complexity | Risk | Files |
|---|---|---|
| L | low (read-only) | `src/admin.py` (new), `admin_templates/` (new), `main.py` |

**Design:**
- FastAPI routes under `/admin/*`
- Basic auth via `ADMIN_USER` / `ADMIN_PASS` env vars (skip if not set = local-only mode)
- Pages: current-month usage table, per-client margin, recent calls, export CSV
- Lightweight — Jinja templates or plain HTML strings

### Section K — Tests

| Complexity | Risk | Files |
|---|---|---|
| M | low | `tests/` (new), `pytest.ini`, `requirements.txt` |

**Design:**
- pytest in `tests/`
- One file per section: `test_call_timer.py`, `test_spam_filter.py`, `test_usage.py`, `test_alerts.py`, `test_kill_switch.py`, `test_tenant.py`
- Keep `_test_suite.py` as integration-level smoke suite (unchanged)

### Section L — Shadow Mode + Rollout

| Complexity | Risk | Files |
|---|---|---|
| S | low | `ROLLOUT.md`, `.env.example` |

**Design:**
- Each section already feature-flagged (`ENFORCE_*`)
- Global kill: `MARGIN_PROTECTION_ENABLED` — checked first, bypasses all enforcement
- Doc with 7-day rollout plan

---

## Phase 3 — Onboarding

| Step | Complexity | Risk | Files |
|---|---|---|---|
| 3.1 `docs/NEW_CLIENT_CHECKLIST.md` | S | low | `docs/NEW_CLIENT_CHECKLIST.md` |
| 3.2 `docs/DEMO_SCRIPT.md` | S | low | `docs/DEMO_SCRIPT.md` |

---

## Phase 4 — Finalize

| Step | Complexity | Risk | Files |
|---|---|---|---|
| 4.1 Verify all deliverables present | S | low | — |
| 4.2 Final commit + summary | S | low | — |

---

## Ordering Rationale

- **Section G first** (multi-tenant) — foundation for everything else
- **B second** (prompt refactor) — depends on G's tenant context
- **E third** (usage tracking) — drives the data for alerts
- **A fourth** (duration cap) — depends on per-client config from G
- **C fifth** (spam filter) — depends on G for per-client blocklist
- **D sixth** (SMS cap) — depends on E for counting + G for cap value
- **F seventh** (alerts) — consumes E's usage data
- **H eighth** (voice tier) — minor, scaffold only
- **I ninth** (flow audit) — prompt-only change, late OK
- **J tenth** (admin) — consumes everything
- **K eleventh** (tests) — locks everything in
- **L twelfth** (rollout doc) — last, documents finished state

## Dependencies Added (Minimum)

Expected additions to `requirements.txt`:
- `pyyaml` — client config files (YAML per spec)
- `pytest` — test framework (spec §K)

**Not adding:** Postgres, Redis, Celery, APScheduler, Flask-WTF, SQLAlchemy (SQLite via stdlib), Jinja2 (plain strings for admin UI).
