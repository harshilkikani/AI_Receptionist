# New Client Onboarding Checklist

Use this when setting up a new HVAC/plumbing/septic/etc. business on the receptionist platform.

---

## Step 1 — Collect from the client (15 min call)

Fill these in as you talk to them. Values go straight into their YAML config.

| Field | Example | Notes |
|---|---|---|
| `id` | `bobs_septic` | snake_case, short. Becomes file name `clients/<id>.yaml`. |
| `name` | "Bob's Septic Service" | Display name (what the AI says) |
| `owner_name` | "Bob Miller" | AI uses: "Bob will call you back" |
| `owner_email` | "bob@bobseptic.com" | **P2** — where monthly invoices go. Empty = SMTP send skipped (webhook still works). |
| `owner_cell` | "+17175551234" | **P3** — where emergency push SMS goes. Falls back to `escalation_phone` if empty. |
| `timezone` | "America/New_York" | **P4** — IANA TZ for the 10 PM-local owner daily digest. Default America/New_York. |
| `services` | "Septic pumping, repairs, emergency service" | One line, under 100 chars |
| `pricing_summary` | "Pumping from $475. Emergency 24/7." | Ballpark — AI quotes this |
| `service_area` | "Lancaster County" | Geographic area |
| `hours` | "Mon-Fri 8am-5pm, emergency 24/7" | For hours inquiries |
| `escalation_phone` | "+17175551234" | Where the AI transfers emergencies (E.164) |
| `emergency_keywords` | `[flooding, backing up, overflow, sewage]` | Words that trigger priority=high |
| Plan tier | `starter` / `pro` / `enterprise` | Pricing tier |
| Monthly price | `$297` | What they pay you |
| Included calls/minutes | `250 / 500` | Before overage kicks in |

Ask them:
- "How many calls do you currently miss per month?" (validates plan sizing)
- "What's the worst thing the AI could say?" (reveals must-avoid phrases — add to prompt if needed)
- "What should the AI do if someone sounds drunk or abusive?" (currently: transfers to on-call; confirm they want this)
- "Want the 10 PM nightly summary?" (if yes, make sure `owner_cell` OR
  `owner_email` is set + `timezone` is correct). Opt-out by setting
  `ENFORCE_OWNER_DIGEST=false` globally or removing `owner_cell`
  /`owner_email` on their YAML.

---

## Step 2 — Create their client config

1. Copy the template:
   ```bash
   cp clients/_template.yaml clients/<client_id>.yaml
   ```
2. Fill in every field from Step 1.
3. **Critical:** `id` must match file name. `inbound_number` must be E.164 (`+1xxxxxxxxxx`).
4. Validate the YAML:
   ```bash
   python -c "from src import tenant; tenant.reload(); c = tenant.load_client_by_id('<client_id>'); print(c['name'], c['plan'])"
   ```
   Should print the name and plan. If `KeyError` or `None`, the YAML has a typo.

---

## Step 3 — Provision the inbound number

1. In Twilio console → **Phone Numbers** → **Buy a number** (or use an existing one the client is forwarding to).
2. Note the E.164 format (e.g., `+17175551234`). Paste into their YAML `inbound_number`.
3. Configure webhooks on the number (in Twilio console or via API):
   - **Voice URL:** `https://<your-ngrok-or-domain>/voice/incoming` (POST)
   - **Status callback:** `https://<your-ngrok-or-domain>/voice/status` (POST)
   - **Messaging URL:** `https://<your-ngrok-or-domain>/sms/incoming` (POST)

   Or programmatically:
   ```python
   from twilio.rest import Client
   c = Client(sid, token)
   nums = c.incoming_phone_numbers.list(phone_number="+17175551234")
   nums[0].update(
       voice_url=f"{base}/voice/incoming", voice_method="POST",
       status_callback=f"{base}/voice/status", status_callback_method="POST",
       sms_url=f"{base}/sms/incoming", sms_method="POST",
   )
   ```

---

## Step 4 — Test with a live demo call

1. Restart the server so the new YAML is loaded:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8765 --reload
   ```
2. Call their new Twilio number from a verified number (for trial accounts) or any number (paid).
3. Verify:
   - Greeting matches their company name
   - AI responds on-topic (try a service question: "my septic tank is backing up")
   - Emergency keywords trigger `priority=high` → transfer to their `escalation_phone`
   - Hours/price inquiry exits in under 30 seconds
4. Check `/admin` — a new row should appear for this client with a call logged.

---

## Step 5 — Enable in shadow mode first

**Do NOT enable enforcement flags for a new client until Day 7+ of observation.**

- All enforcement flags (`ENFORCE_*`) are global, so your existing live clients on `ENFORCE_*=true` will apply to the new client too — that's fine; the multi-tenant configs and rate cards handle differentiation.
- Watch the new client's calls in `/admin/calls` and `logs/rejected_calls.jsonl` for the first week.
- Adjust their per-client config (especially `emergency_keywords`, `max_call_duration_seconds`) based on observed behavior.

---

## Step 6 — Suggested 7-day free trial setup

For a low-commitment pilot:

1. Have the client forward their **after-hours line** (not their main line) to the Twilio number. This catches missed calls without risking their main phone flow.
2. Tell them: "For 7 days, any call your team doesn't pick up after-hours will come to the AI instead. We'll send you a daily digest of what the AI handled."
3. Enable `ENFORCE_USAGE_ALERTS=true` and point `config/alerts.json::webhook.url` at a Zapier webhook that emails the client + you.
4. At Day 7, review the `/admin/export.csv` output with them. Convert to paid if they liked what they saw.

---

## Step 7 — Go live (after trial)

1. Update their YAML: switch from trial plan to paid plan tier.
2. Client updates their main phone forwarding to always go through Twilio first (or keep after-hours only).
3. Add their number to the long-term monitoring list (your own tracking sheet).
4. Schedule a 14-day check-in to review margins.

---

## Common pitfalls

- **Typo in `inbound_number`**: calls fall back to `_default` client → generic greeting. Fix by matching Twilio's exact E.164 format.
- **Missing `escalation_phone`**: emergencies land in voicemail with "tech being paged — they'll call back in ten". Add their on-call.
- **Too-loose `emergency_keywords`**: includes common words like "leak" when they're a roofing company (not plumbing) → false emergency transfers. Scope it to real emergencies for their industry.
- **Overlapping numbers between clients**: two YAMLs with the same `inbound_number` — first loaded wins. Always verify with `python -c "from src import tenant; print(tenant.load_client_by_number('+1xxx')['id'])"`.
