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
