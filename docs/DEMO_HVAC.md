# Demo flow — HVAC emergency

A ~3-minute live demo. The prospect sees both panes side-by-side at
`http://localhost:8000/` (or whatever the live URL is). Pick **Marcus
Reilly** in the phone shell to start.

The script is paced for **read-aloud** during a screen-share. The
emotional arc: a real receptionist would handle this in under a
minute. The AI does it in 22 seconds, with the owner already in the
loop before the call bridges.

---

## Setup

- Open the demo page. Both panes load — chat on the left (phone), portal on the right (Septic Pro's "Today" view).
- The portal already shows 6 seeded scenarios — quietly establish "this is what a normal day looks like" without dwelling.
- Pick **Marcus Reilly** in the phone. The system caption shows: *"Sewage backup emergency — try saying your toilets are overflowing."*

> **Talk track:** "Quick demo — let's pretend I'm a homeowner who just woke up to a sewage backup. I dial the business line at 11pm. Here's what happens on the call, and here's what the owner sees."

## Beat 1 — Customer calls (00:00)

**Customer (you, in the phone input):**
> "Hey, my toilets are backing up and there's stuff coming up in the basement."

**AI replies (~1s):**
> "That sounds serious — I'm flagging this as an emergency. Address?"

**Portal beat:** Marcus's existing card flashes briefly (the V10.2
continuity highlight) at the top of "Recent activity". Status pill
updates to **Emergency** (red).

> **Talk track:** "Notice the AI doesn't try to upsell, doesn't ask for a name first, doesn't say 'I understand your concern'. It heard 'sewage' and 'backing up' — that's an emergency keyword — and skipped straight to the address."

## Beat 2 — Address capture (00:14)

**Customer:**
> "412 Maple Lane, Lancaster."

**AI:**
> "Got it. Bob is on call — he'll ring you in under a minute."

**Portal beat:** Card expands inline (click it during the demo). The
last 3 turns are visible — the prospect can see exactly what the AI
heard and said. The "View full thread →" link is in the bottom of the
preview.

**Owner SMS (off-screen):** the owner Bob receives:
> "Emergency · Marcus Reilly · 412 Maple Lane, Lancaster · sewage backup in basement · 5550101001. Call about to bridge."

> **Talk track:** "Bob's phone just rang. He has the briefing before he picks up. No 'who is this' awkwardness when the call connects."

## Beat 3 — Operator visibility (00:22)

Open the expandable Marcus card on the portal. Show the inline preview.

> **Talk track:** "If Bob's not at his desk, he opens this portal on his phone. There's the call. Status: Emergency. Address captured. Conversation summary on the right. He can also pull up the full transcript if he wants to."

Open the full thread (click "View full thread →"). The conversation
detail shows both the emergency call and any prior history. Marcus's
illustrated portrait is consistent across both panes.

## Beat 4 — Calm filter (00:45)

Switch back to the chat. Pick **"Unknown caller"**.

> **Talk track:** "Now let's say someone calls who's got the wrong number."

**Customer:**
> "Hey is this dom?"

**AI:**
> "No worries — wrong number."

**Portal beat:** Unknown caller's card updates. Status: **Filtered**.
This call doesn't count against the owner's attention.

> **Talk track:** "Bob doesn't get pinged for wrong numbers. The AI just politely closes it out. The portal logs it so there's no question about what was said."

## Beat 5 — Trust close (01:00)

Stay on the portal. Scroll through the activity feed. Point out:
- The seeded scenarios all show real names, phones, photos
- The "Live" indicator at the top
- The "Recent activity" feed updates without refresh

> **Talk track:** "Everything you see here is live. Every call, every text, every follow-up — captured automatically. You set up business hours and a forwarding number once. After that, it just answers the phone the way you would, all the time. Emergencies get to you in under a minute. Wrong numbers don't."

> **Close:** "Want to point your line at this for a week and see what happens?"

---

## Pacing notes

- Don't read the talk track verbatim — paraphrase. The script is the
  emotional arc, not a teleprompter.
- Resist the urge to explain the architecture. The prospect doesn't
  care that there's an LLM. They care that calls get answered.
- If something glitches (filler plays twice, refresh stalls), shrug
  and say "real software, real internet, occasional hiccups". Don't
  apologize — recover.
- The whole arc should land in **3 minutes or less**. If it's running
  long, cut Beat 4 (the wrong-number calm filter) — the emergency
  flow alone is enough to sell.

## What NOT to do

- Don't open the admin/internal pages
- Don't reference token counts, latency numbers, or model names
- Don't say "AI" more than once in the whole flow — say "the
  receptionist" or "the system"
- Don't fake-edit settings to show flexibility. The product feels
  premium because it's opinionated, not because it has 40 knobs.
