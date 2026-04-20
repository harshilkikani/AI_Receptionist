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
