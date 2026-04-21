# Custom Demo Script — 30-minute prospect walkthrough

Goal: get the prospect to hear their business answered by the AI using their actual company name + services within 20 minutes of the call starting. They pay the $5 Twilio credit themselves.

---

## Pre-call prep (10 minutes before)

1. Have a **throwaway Twilio number** ready. Keep one always provisioned for demos under `TWILIO_DEMO_NUMBER` in `.env`.
2. Pre-create a demo client config:
   ```bash
   cp clients/_template.yaml clients/demo_prospect.yaml
   ```
   Fill `id: demo_prospect`, `inbound_number: <demo number>`. Leave the rest to fill live.
3. Open two browser tabs:
   - Twilio Console → your demo number's config page
   - `http://localhost:8765/admin` (your local admin dashboard)

---

## The call (30 minutes)

### Minutes 0–5: "Tell me about your business"

Ask them:
1. Company name (exactly how they say it)
2. Owner first name
3. What services they offer (1 sentence)
4. Ballpark pricing (1 line)
5. Service area
6. Hours
7. What's an emergency for them (specific keywords)
8. Where should emergencies transfer to (their cell)

Write into `clients/demo_prospect.yaml` as they talk.

### Minutes 5–10: "Let me set this up — one moment"

Save the YAML. In terminal:
```bash
# 1. Hot-reload tenant configs (or restart)
# Easiest: just restart uvicorn
# 2. Verify it loads
python -c "from src import tenant; c = tenant.load_client_by_id('demo_prospect'); print(c['name'], c['services'])"
```

You can narrate this: "I'm just loading your info into the system — your name, services, and emergency contact. Takes about 30 seconds."

### Minutes 10–15: First live call

Put them on speaker. "Okay — call this number: (555) XXX-XXXX."

They call. The AI answers:
> *"Hey, this is Joanna from [their company name] — what's going on?"*

Prompt them:
- "Ask about your hours." → AI gives hours in 1 sentence, asks if anything else
- "Ask about pricing." → AI gives ballpark, offers in-home estimate
- "Say 'I've got a pipe bursting!'" (or their equivalent) → AI goes into emergency mode, transfers to their cell

**This is the moment that sells it.** Their phone rings. "That's the AI paging you for a real emergency."

### Minutes 15–20: Show the back-office

Open `/admin` in your browser. Point at:
- The call they just made, logged with duration + intent + outcome
- Per-client margin (show them what their plan would cost vs. your rate)
- Export CSV → "You'd get this for overage billing every month"

Open `/admin/calls` → show the transcript trail.

### Minutes 20–25: Pricing conversation

Now that they've *experienced* it, they're ready to hear the price.

Template:
> "Starter plan is $297/month: up to 250 calls and 500 minutes. That's roughly what a mid-size HVAC shop does after-hours in a month. Going over that is 75¢ per extra call — and frankly if you're doing 400 calls a month of AI-handled missed calls, the value to you is far more than 75¢ each. Want to run it for 7 days free to see the numbers?"

### Minutes 25–30: Close

If yes:
- Offer the 7-day free trial (see `NEW_CLIENT_CHECKLIST.md` §6)
- Set up their **after-hours** forwarding (low commitment)
- Schedule Day-7 review call to look at the admin dashboard together

If not yet:
- Offer to leave the demo number active for the week so they can test it with their team
- Send them `/admin/export.csv` of the demo call data as a takeaway

---

## Common objections and responses

| Objection | Response |
|---|---|
| "I can't have an AI answer my phone" | "Fair — want to start with *only* after-hours and missed calls? You never let the AI near your main line unless you decide it's good." |
| "What if it gets something wrong?" | "It transfers to your cell any time someone says an emergency keyword (we configured those with you in this call). It's an overflow handler, not a replacement." |
| "Can I listen to recordings?" | Twilio records calls if you enable it. Admin export CSV includes timestamps that cross-reference Twilio's recordings. "Absolutely — all calls recorded in Twilio." |
| "What's the price competitive against?" | A part-time answering service is $400–$800/mo and doesn't work nights/weekends. You're $297 for 24/7. |
| "Can you customize the voice?" | Right now it's Polly Joanna (female, US English). Future: ElevenLabs voice cloning — your voice could literally answer. |

---

## After the demo — cleanup

1. **Don't delete their YAML yet** — leave it so the demo number keeps working during the trial
2. If they pass, archive the YAML: `git mv clients/demo_prospect.yaml clients/_archived/`
3. Release the throwaway number in Twilio console (or keep it as your standing demo number)

---

## Tips that win demos

- **Always have them do the emergency test on their own cell.** When it rings, they're sold.
- **Don't demo via your laptop speaker.** Put them on speakerphone to an actual phone line.
- **Pre-fill `pricing_summary` and `services` with their actual wording** — if they say "pumping from $475" and the AI says "pricing varies by service", they'll notice.
- **If they're chatty, skip the "ask what are your hours" step** and go straight to the emergency demo. Strongest moment first.
- **Record their demo call** and send a clip as a follow-up email. "Here's what your customers would hear."
