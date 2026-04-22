# Operations Runbook — top 10 failure modes

_Last updated: 2026-04-21_

Each entry: **symptom** (what you'll notice), **diagnose** (commands to
confirm), **fix** (shortest corrective path). Don't diagnose by guessing
— run the command first, then act.

---

## 1. Tunnel dead (Twilio webhooks returning 502/504)

**Symptom:** Twilio Debugger logs 502/504 on `/voice/incoming`. New
calls go straight to caller-side voicemail or Twilio's default error
handling.

**Diagnose:**
```bash
curl -sS -I https://<your-tunnel>.trycloudflare.com/missed-calls
# or for named tunnel:
curl -sS -I https://receptionist.yourdomain.com/missed-calls
cat data/tunnel_url.txt   # was the URL written recently?
```

**Fix:**
```bash
python scripts/reclaim_tunnel.py       # auto-captures new URL + repoints Twilio
# OR for named tunnel:
cloudflared tunnel --url http://localhost:8765 run receptionist
```

If the app itself is down, see #4 first.

---

## 2. Twilio 429 (rate limited on outbound SMS)

**Symptom:** `/voice/status` returns `{"ok": True, "action": "sms_sent"}`
but caller never receives the recovery SMS. Server log shows Twilio
SDK exceptions with status 429.

**Diagnose:**
```bash
# Check Twilio console → Monitor → Errors for RateExceeded codes
# Check your current SMS send rate for the last hour
```

**Fix:** Twilio's default is 1 SMS/sec per number. Nothing to do at the
app level — the SMS will fire. If this recurs, apply for Twilio
"high-throughput" or provision additional sending numbers. In the
meantime, flip `ENFORCE_SMS_CAP=true` if it isn't already so runaway
threads don't compound the problem.

---

## 3. Anthropic 429 (LLM rate limited)

**Symptom:** `/voice/gather` returns TwiML with the fallback reply
("Gimme one sec, let me grab someone."). Server log shows
`anthropic.RateLimitError`.

**Diagnose:**
```bash
grep -E "RateLimit|anthropic_api" logs/*.log 2>/dev/null
# Or look at stdout if running attached
```

**Fix:** The handler returns `_FALLBACK` gracefully — the caller
gets one generic reply, but the next turn usually recovers. If
sustained, upgrade Anthropic tier. For the trial account, the free
Haiku 4.5 limit is ~5 RPM — enough for demo traffic but not production.

---

## 4. Admin UI down (overview page 500)

**Symptom:** `/admin` returns 500. Other routes may work.

**Diagnose:**
```bash
curl -sS -i http://localhost:8765/admin/flags
curl -sS -i http://localhost:8765/admin
# Check if data/usage.db is present + readable
ls -la data/usage.db
sqlite3 data/usage.db "SELECT COUNT(*) FROM calls;"
```

**Fix:** If the DB is corrupted, see #5. If it's a YAML parse issue
(new tenant with invalid YAML), `python -c "from src import tenant;
tenant.reload(); [print(c['id']) for c in tenant.list_all()]"` —
whichever one raises is the culprit. Rename it out of `clients/`
until fixed.

---

## 5. SQLite lock / "database is locked"

**Symptom:** `sqlite3.OperationalError: database is locked` in logs.
Admin pages 500.

**Diagnose:**
```bash
lsof data/usage.db 2>/dev/null   # (Linux/mac) — who has it open?
# or on Windows: check for stray python processes
```

**Fix:** Another process (often a stale uvicorn worker) is holding the
WAL. Kill the stray process. If lock persists and no process holds it,
`sqlite3 data/usage.db ".recover"` — but BACK UP the DB file first:
```bash
cp data/usage.db data/usage.db.bak-$(date +%s)
```

---

## 6. Runaway call (stuck past 240s with no force-end)

**Symptom:** Admin call log shows a call with duration > 300s and
outcome still null. `/admin/calls` shows it as in-progress.

**Diagnose:**
```bash
grep -E "call_timer call_sid=CA[a-z0-9]+" logs/*.log 2>/dev/null | tail -20
sqlite3 data/usage.db "SELECT call_sid, duration_s, outcome FROM calls WHERE outcome IS NULL;"
```

**Fix:**
1. Confirm the flag: `/admin/flags` — is `ENFORCE_CALL_DURATION_CAP=true`?
   If not, this is shadow-mode behavior (expected — cap logs but
   doesn't force-end).
2. In Twilio console, hang up the call manually:
   Twilio → Monitor → Calls → find CallSid → Hangup.
3. Investigate why `call_timer.check` returned `hard_wrapup` instead
   of `force_end` — likely the env var is unset on the running
   process.

---

## 7. Stale client YAML (wrong greeting after edit)

**Symptom:** You edited `clients/ace_hvac.yaml` but the AI still says
the old company name.

**Diagnose:**
```bash
python -c "from src import tenant; tenant.reload(); print(tenant.load_client_by_number('+18449403274'))"
```

**Fix:** YAML changes require process restart (no hot reload by design).
Stop + start uvicorn. Verify in `/admin` that the tenant name matches.

---

## 8. SMTP down (invoices / digests not arriving)

**Symptom:** Clients report they didn't receive the monthly invoice.
`/admin/alerts/trigger` returns `{"sent": False}`.

**Diagnose:**
```bash
# Force a send + watch server logs
curl -sS http://localhost:8765/admin/alerts/trigger
# Check: is ALERT_SMTP_PASSWORD set? Is config/alerts.json::smtp fully filled?
python -c "from src import alerts; print(alerts._cfg().get('smtp'))"
```

**Fix:**
- SMTP host down: wait it out; manual re-send via
  `python -m src.invoices send <client_id> <YYYY-MM>` after the
  provider recovers.
- Auth failure: rotate `ALERT_SMTP_PASSWORD` and restart.
- Switch transport to webhook (set `transport: "webhook"` in
  `config/alerts.json`) and point at a Zapier/email hook.

---

## 9. Spam filter false positive on a live customer

**Symptom:** Customer complains "I called and got hung up on
immediately." Phone number in `logs/rejected_calls.jsonl`.

**Diagnose:**
```bash
tail -n 50 logs/rejected_calls.jsonl | grep "<customer_phone_tail>"
```

**Fix:**
1. If `layer: "number"` with `reason: "number_blocklisted"` — remove
   from `config/spam_blocklist.json::numbers`, restart.
2. If `layer: "number"` with `reason: "high_risk_area_code"` — remove
   from `config/spam_blocklist.json::area_codes_high_risk`, restart.
3. If `layer: "phrase"` — consider adding an override keyword for the
   phrase the customer used (`config/spam_phrases.json::override_keywords`)
   and/or removing the too-greedy spam phrase.

Worst-case hot fix: `export ENFORCE_SPAM_FILTER=false` on the server
process and restart — shadow mode, everything logs but nothing blocks.

---

## 10. Duration cap cutting real customers off mid-sentence

**Symptom:** Customer says "the AI hung up on me while I was giving my
address." Call log shows `outcome='duration_capped'` with duration
exactly 240s or 360s (emergency).

**Diagnose:**
```bash
sqlite3 data/usage.db \
  "SELECT call_sid, duration_s, emergency FROM calls WHERE outcome='duration_capped' ORDER BY start_ts DESC LIMIT 20;"
```

**Fix:**
- Hot rollback: `export ENFORCE_CALL_DURATION_CAP=false` on the server
  process, restart.
- Root fix: increase `plan.max_call_duration_seconds` in that client's
  YAML (e.g., 240 → 360). Restart.
- Longer-term: audit the grace-period heuristic
  (`src/call_timer.py::_critical_info_pending`) — if address digits
  aren't being detected, broaden the regex.
