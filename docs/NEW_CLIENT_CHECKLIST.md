# New Client Onboarding Checklist

_Last updated: 2026-04-21 (commit 6572aab)_

The fastest path is the wizard. Manual YAML editing still works — see
the fallback section below if you need to customize fields not yet in
the wizard.

---

## Fast path — run the wizard

```bash
python -m src.onboarding new
```

You'll answer ~19 prompts. Each one is validated (E.164 format,
snake_case id, IANA timezone, positive numbers). The wizard writes
`clients/<id>.yaml` and prints:

1. Missing `.env` vars to set
2. Twilio webhook URLs to paste into the number's config
3. A ready-to-send client portal URL (if `CLIENT_PORTAL_SECRET` is set)
4. A `python -c` routing sanity check
5. A `curl` to verify end-to-end that the tenant resolves

### Wizard questions (reference)

| Field | Example | Notes |
|---|---|---|
| `id` | `bobs_septic` | snake_case. Becomes `clients/<id>.yaml`. |
| `name` | "Bob's Septic Service" | Display name (what the AI says). |
| `owner_name` | "Bob Miller" | AI uses: "Bob will call you back". |
| `owner_email` | "bob@bobseptic.com" | **P2** — where monthly invoices go. Optional. |
| `owner_cell` | "+17175551234" | **P3** — where emergency push SMS goes. Optional. |
| `timezone` | "America/New_York" | **P4** — IANA TZ for 10 PM-local owner daily digest. |
| `inbound_number` | "+17175551234" | Twilio number in E.164. **Required.** |
| `escalation_phone` | "+17175551234" | Where emergencies transfer. **Required.** |
| `services` | "Septic pumping, repairs, emergency" | One line. |
| `pricing_summary` | "Pumping from $475. Emergency 24/7." | Ballpark. |
| `service_area` | "Lancaster County" | |
| `hours` | "Mon-Fri 8-5, emergency 24/7" | |
| `emergency_keywords` | `flooding,backing up,sewage` | Comma-separated; lowercased. |
| Plan tier | `starter` / `pro` / `enterprise` | |
| `monthly_price` | `297` | USD. |
| `included_calls` | `250` | Per month before overage. |
| `included_minutes` | `500` | Per month before overage. |
| `overage_rate_per_call` | `0.75` | USD per call over included. |
| `default_language` | `en` | `en`/`es`/`hi`/`gu`/`pt`/... |

Still worth asking the client during onboarding:
- "How many calls do you currently miss per month?" (validates plan sizing)
- "What's the worst thing the AI could say?" (reveals must-avoid phrases)
- "What should the AI do if someone sounds drunk or abusive?" (currently:
  transfers to on-call; confirm they want this)
- "Want the 10 PM nightly summary?" (if yes, ensure `owner_cell` OR
  `owner_email` is set + `timezone` is correct). Opt-out by setting
  `ENFORCE_OWNER_DIGEST=false` globally or leaving both fields empty.
- "Want the YES/NO follow-up SMS after non-emergency calls for the
  first few weeks?" (P11). Flip `ENFORCE_FEEDBACK_SMS=true` globally
  to enable. NO responses land in `logs/negative_feedback.jsonl` —
  review weekly and promote recurring patterns into
  `evals/cases.jsonl` so the regression detector catches them from
  then on.

---

## Demo mode — disposable tenant

For sales demos, mint a 24-hour throwaway:

```bash
python -m src.onboarding new-demo            # random id
python -m src.onboarding new-demo --id demo_acme   # override id
```

The YAML carries `demo: true` + `demo_expires_ts`. On the next server
startup (or `python -m src.onboarding purge-expired` explicitly),
expired demos move to `clients/_expired/` and stop routing.

During the demo window the tenant is indistinguishable from a real one.
After expiry it's harmlessly archived.

---

## Provision the Twilio number

1. In Twilio console → **Phone Numbers** → **Buy a number** (or use an
   existing number the client is forwarding to).
2. In the number's config, paste the webhook URLs the wizard printed:
   - **Voice URL:** `POST {base_url}/voice/incoming`
   - **Status callback:** `POST {base_url}/voice/status`
   - **Messaging URL:** `POST {base_url}/sms/incoming`

   Or programmatically via the Twilio Python SDK — the wizard prints an
   equivalent snippet.

---

## Test with a live demo call

1. Restart the server so the new YAML is loaded:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8765
   ```
2. Call the new Twilio number from a verified number (trial) or any
   number (paid).
3. Verify:
   - Greeting matches the company name
   - AI responds on-topic (try: "my septic is backing up")
   - Emergency keywords trigger `priority=high` → transfer to
     `escalation_phone`
   - Hours/price inquiry exits in under 30 seconds
4. `/admin` shows the new client with a call logged.

---

## Shadow mode first

**Do NOT enable enforcement flags for a new client for the first week.**

- All enforcement flags (`ENFORCE_*`) are global, so your existing live
  clients on `ENFORCE_*=true` will apply to this one too — that's fine;
  multi-tenant configs and rate cards handle the differences.
- Watch the new client's calls in `/admin/calls` and
  `logs/rejected_calls.jsonl` for a week.
- Adjust per-client config (especially `emergency_keywords` and
  `max_call_duration_seconds`) based on observed behavior.

---

## 7-day free trial (low-commitment pilot)

1. Client forwards their **after-hours line** (not main line) to the
   Twilio number.
2. Tell them: "For 7 days, any call your team doesn't pick up after-hours
   will come to the AI instead. We'll send you a daily summary of what
   the AI handled."
3. Confirm `ENFORCE_USAGE_ALERTS=true` (default) and point
   `config/alerts.json::webhook.url` at a Zapier webhook that emails the
   client + you.
4. At Day 7, review `/admin/export.csv` with them. Convert to paid if
   they liked what they saw.

---

## Going live after the trial

1. Flip their plan tier in the YAML (trial → paid).
2. Client updates their main phone forwarding to always go through
   Twilio first (or keep after-hours only).
3. Send them their `/client/<id>?t=<token>` portal URL so they can
   bookmark it.
4. Schedule a 14-day check-in to review margins.

---

## Fallback — manual YAML editing

If you need a field the wizard doesn't cover (custom `plan` extras,
integrations, etc.):

1. Copy the template:
   ```bash
   cp clients/_template.yaml clients/<client_id>.yaml
   ```
2. Fill in every field. `id` must match the filename.
3. Validate:
   ```bash
   python -c "from src import tenant; tenant.reload(); print(tenant.load_client_by_id('<id>')['name'])"
   ```
4. Restart the server.

---

## Common pitfalls

- **Typo in `inbound_number`** — calls fall back to `_default` → generic
  greeting. Fix by matching Twilio's exact E.164 format. The wizard's
  E.164 validator catches this up front.
- **Missing `escalation_phone`** — emergencies land in voicemail. Always
  set before going live.
- **Too-loose `emergency_keywords`** — e.g., "leak" for a roofing
  company triggers false emergencies. Scope to real emergencies only.
- **Overlapping numbers between clients** — first loaded wins. Always
  re-run the routing check after adding:
  `python -c "from src import tenant; print(tenant.load_client_by_number('+1xxx')['id'])"`.
- **Forgot `CLIENT_PORTAL_SECRET`** — the wizard skips printing the
  portal URL and tells you to set it + re-run `python -m src.client_portal issue`.
