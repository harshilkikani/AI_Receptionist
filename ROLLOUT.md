# ROLLOUT — Margin Protection Refactor (7-day plan)

_Branch: `margin-protection-refactor`_
_Target: `main` after Day 7 if clean_

This refactor adds multi-tenant routing, call duration caps, spam filtering, SMS limits, usage tracking, alerts, and an admin dashboard. All enforcement features default to **shadow mode** — they log what they WOULD do but don't actually intervene. Operator enables each feature individually after observing logs.

---

## Prerequisites (before Day 1)

1. **Merge the branch behind a release tag:**
   ```bash
   git checkout margin-protection-refactor
   git pull
   git tag release-margin-v1
   ```
   (Or merge to `main` via PR — whichever workflow you use.)

2. **Install new dependencies:**
   ```bash
   pip install -r requirements.txt   # adds pyyaml + pytest
   ```

3. **Run the test suite:**
   ```bash
   pytest tests/
   # Expect: 51 passed
   ```

4. **Copy `.env.example` → `.env` and fill in:**
   - `ANTHROPIC_API_KEY` (unchanged)
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_NUMBER` (unchanged)
   - Leave all `ENFORCE_*` flags at their defaults (false for call cap/spam/SMS; true for alerts)
   - `MARGIN_PROTECTION_ENABLED=true` (global switch — individual flags still gate actual enforcement)
   - **P0 admin security (REQUIRED before exposing /admin via tunnel):**
     - `ADMIN_USER=<any>` and `ADMIN_PASS=<32+ random chars>`. If either is
       unset, /admin is open — fine for localhost, unsafe for cloudflared.
     - Optional: `ADMIN_RATE_LIMIT_PER_MIN=60` (default; middleware 429s past it).
     - Every response now carries `X-Content-Type-Options: nosniff` and
       `Referrer-Policy: no-referrer` automatically. No action needed.

5. **Verify `clients/ace_hvac.yaml` exists and maps to the live number** (`+18449403274` by default).

6. **Restart the server.** Verify a test call to `+18449403274` still reaches the AI:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8765
   ```
   Call the number. AI should answer as "Joanna from Ace HVAC" (same as before).

7. **Open the admin dashboard:**
   ```
   http://localhost:8765/admin
   ```
   Expect: empty-ish table with ace_hvac listed and 0 calls / $0 cost for the current month. This is your baseline — verify it increments as calls come in.

---

## Day 1–2: Shadow mode observation

**All enforcement flags remain `false`.** Everything runs but only logs what it would do.

**Watch:**
- `logs/rejected_calls.jsonl` — review for false positives in spam detection. Any legit caller accidentally matched?
- Uvicorn stdout for `call_timer` log lines — note how many calls would have been force-ended. Are they mostly past 240s naturally, or is the cap too aggressive?
- `data/usage.db` grows with every call. Inspect via the admin dashboard at `/admin`.
- `/admin/flags` — confirm all enforcement flags show as `false` or `(unset)`.

**Red flags to address before enabling enforcement:**
- Any legit caller logged as `spam_phrase_detected` → tune `config/spam_phrases.json` (remove the phrase or add override keyword)
- Any call that hit the 240s cap mid-important-info → increase `plan.max_call_duration_seconds` in `clients/ace_hvac.yaml`

**Don't do yet:** don't enable any `ENFORCE_*` flag.

---

## Day 3: Enable spam filter

**Change:**
```diff
# .env
-ENFORCE_SPAM_FILTER=false
+ENFORCE_SPAM_FILTER=true
```

Restart server.

**Watch for 24h:**
- Every new entry in `logs/rejected_calls.jsonl` now corresponds to an actual rejection. Operator should review daily.
- If Twilio usage drops significantly → spam filter is working.
- If legit customers complain they couldn't get through → check phone number in logs. May be a blocklist or area code false positive.

**Rollback if needed:**
```diff
-ENFORCE_SPAM_FILTER=true
+ENFORCE_SPAM_FILTER=false
```
Restart. Back to shadow.

---

## Day 4: Enable SMS cap

**Change:**
```diff
-ENFORCE_SMS_CAP=false
+ENFORCE_SMS_CAP=true
```

Restart.

**Watch:**
- `usage.db::sms` table — are any outbound SMS blocked? (Rare — AI typically sends ≤1 per conversation.)
- No customer complaints about "missed texts" from the AI.

Low-risk flag. Rarely triggers except in pathological back-and-forth text threads.

---

## Day 5: Enable call duration cap (⚠ highest risk)

**Change:**
```diff
-ENFORCE_CALL_DURATION_CAP=false
+ENFORCE_CALL_DURATION_CAP=true
```

Restart.

**Watch very carefully for 24h:**
- Calls that end at exactly 240s (or 360s for emergencies) are now hard-terminated. Review Twilio call logs + your customer feedback.
- Emergency calls should show as `outcome='emergency_transfer'`, NOT `duration_capped` — the transfer should happen before the cap.
- If a customer complains about being cut off mid-address-collection, the grace period didn't engage. Review the caller's last transcript and tighten `src/call_timer.py::_critical_info_pending` keywords.

**Rollback instantly if any single legit call is truncated:**
```bash
# Hot rollback via env var — no code change, no restart needed if using reloader
export ENFORCE_CALL_DURATION_CAP=false
```
Or flip the global kill:
```bash
export MARGIN_PROTECTION_ENABLED=false
```

---

## Day 6–7: Tune + review margins

**Alerts** are already on (`ENFORCE_USAGE_ALERTS=true` by default). You should have received at least one daily digest by now.

**Check:**
1. `/admin` — per-client margin table. Is ace_hvac green (>50% margin)?
2. `logs/rejected_calls.jsonl` — tune `config/spam_phrases.json` based on observed false positives.
3. Per-client plan sanity: `clients/ace_hvac.yaml::plan.included_minutes` realistic vs actual consumption?
4. Rate card (`config/rate_card.json`) — update if vendor prices changed.

**Decide:**
- Tune up/down `plan.max_call_duration_seconds` per client as you gather data
- Add known-spam numbers to `config/spam_blocklist.json::numbers`
- Adjust alert thresholds if the defaults are noisy

**After 7 days:**
- Merge `margin-protection-refactor` to `main`
- Tag release: `git tag v1.0-margin-protection`

---

## Emergency procedures

### "All calls failing"
Global kill, no restart needed:
```bash
export MARGIN_PROTECTION_ENABLED=false
```
Next request immediately bypasses all new enforcement.

### "Spam filter rejecting real customer"
1. Find their call in `logs/rejected_calls.jsonl`
2. If phrase match: add their legit word/phrase to `config/spam_phrases.json::override_keywords`, restart
3. If number match: remove their number from `config/spam_blocklist.json::numbers`, restart

### "Admin dashboard shows negative margin"
1. Check `/admin` — which client is losing money?
2. Compare `platform_cost_usd` vs `revenue_usd`
3. Common cause: client is using more minutes than `included_minutes`. Options:
   - Lower `plan.max_call_duration_seconds` for that client
   - Raise their plan tier
   - Invoice overage using `/admin/export.csv` + `plan.overage_rate_per_call`

### "Alerts not firing"
1. `/admin/flags` — is `ENFORCE_USAGE_ALERTS` on?
2. `config/alerts.json` — is transport URL/SMTP configured?
3. `/admin/alerts/trigger` — force a test digest; check server logs for success/failure

---

## Feature flag summary

| Flag | Default | What it controls |
|---|---|---|
| `MARGIN_PROTECTION_ENABLED` | `true` | Global kill switch — when false, all enforcement bypassed |
| `ENFORCE_CALL_DURATION_CAP` | `false` | Force-end calls past 240s / 360s emergency |
| `ENFORCE_SPAM_FILTER` | `false` | Reject calls by number blocklist / phrase match |
| `ENFORCE_SMS_CAP` | `false` | Block outbound SMS past `plan.sms_max_per_call` |
| `ENFORCE_USAGE_ALERTS` | `true` | Send daily digest email/webhook |
| `ENFORCE_OWNER_EMERGENCY_SMS` | `true` | P3 — push owner's cell on emergency |
| `ENFORCE_OWNER_DIGEST` | `true` | P4 — 10 PM-local daily owner summary |
| `TWILIO_VERIFY_SIGNATURES` | `true` | P6 — 403 forged Twilio webhooks |

Always: usage tracking (SQLite) is on regardless of flags. The kill switch does **not** stop data collection — only enforcement.

## Production security toggles (P6)

After the initial Twilio wiring phase, the **last** toggle you flip is
`TWILIO_VERIFY_SIGNATURES=true`. Timeline:

1. Wire the number + webhook URLs. Confirm calls land.
2. Set `TWILIO_VERIFY_SIGNATURES=false` for 24h and watch
   `logs/` for `twilio_signature shadow-pass` lines. Every
   legitimate Twilio POST should validate (you'll see no warnings).
3. Flip to `true`. Any forged / mis-routed webhook now gets a 403 at
   the middleware layer before touching app code.

If a legitimate webhook starts 403'ing after you flip, check
`X-Forwarded-Proto` + `X-Forwarded-Host` in your tunnel config, or set
`PUBLIC_BASE_URL` explicitly so signature validation uses the correct
URL scheme + host.
