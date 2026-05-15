# Website Showcase Script — AI Receptionist (Real Estate · Frontend Demo)

_Last updated: 2026-05-15 — v2.0 (V11.0, frontend-only)_
_Runtime target: 3:10–3:30_
_Format: single browser screen recording + two-voice voiceover._

The video is a **screen recording of the V11.0 combined-demo page at
`/`** with the industry switcher set to Lawrence Realty. No Twilio
audio, no separate operator-portal screen-grab, no iPhone mockups —
the demo page already shows all three surfaces side-by-side (the
chat phone, the owner phone, and the embedded operator portal). Two
voiceover hosts narrate over the interaction.

Why frontend-only: we ship V11.0 and this lets us cut a hero video
this week without scheduling Twilio recording sessions or matching
voice talent. A fuller production version with live Polly Joanna
audio belongs in `SHOWCASE_REAL_ESTATE_FULL.md` later.

Companion scripts:
- `SHOWCASE_SCRIPT.md` — original septic-flavored video (V8)
- `DEMO_REAL_ESTATE.md` — solo live walkthrough for prospects
- `LIVE_DEMO_TWO_PRESENTER.md` — in-person two-presenter playbook

---

## The screen, throughout

The browser window is at `http://<host>/` with the V11.0 demo loaded.
The viewport is wide enough that all three regions are simultaneously
visible:

```
┌──────────────────────────────────────────────────────────────────┐
│  AI Receptionist · +1 (844) 940-3274 · ⋯                          │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│        ┌─── OPERATOR PORTAL PANE ────────────────────────┐        │
│        │  Today's leads · stat cards · Recent activity   │        │
│        │  Worth a follow-up · expandable call cards      │        │
│        └─────────────────────────────────────────────────┘        │
│                                                                   │
│  ┌──────────────────────┐    ┌──────────────────────┐             │
│  │  CUSTOMER PHONE       │    │  OWNER PHONE          │             │
│  │  (chat shell)         │    │  (Lauren's phone)     │             │
│  │  caller chips         │    │  Seeded SMS bubbles   │             │
│  │  conversation         │    │  + new alerts arrive  │             │
│  │  suggestion chips     │    │                       │             │
│  └──────────────────────┘    └──────────────────────┘             │
└──────────────────────────────────────────────────────────────────┘
```

Cursor moves are the only "production" — no cuts away from the
browser window. Two voiceover tracks (A storyteller, B operational
voice) narrate. Captions on for muted autoplay.

---

## [0:00 – 0:14] — HOOK

**[VISUAL:** demo page open. Industry switcher already set to
**Lawrence Realty** so the brand reads "Lawrence Realty," chat
caller list shows Caleb/Priya/Daniel/Sienna/Jordan/Emily, owner
phone reads "Lauren's phone" with two pre-baked SMS bubbles. No
cursor activity yet — a calm, populated screen.]

**A** *(voiceover, unhurried):*
This is what a real-estate agent's phone looks like at 9 PM on a
Thursday. A buyer opens Zillow on his couch. He calls the listing.
On the left, what he sees. On the right, what the agent sees. In the
middle, what everyone else misses.

---

## [0:14 – 0:24] — ORIENTATION

**[VISUAL:** cursor traces a slow arc — points to the customer phone
shell on the left, then the owner phone on the right, then up to
the operator portal pane. Three quiet hovers, no clicks.]

**B** *(voiceover, matter-of-fact):*
Customer's phone. Agent's phone. Operations view. Same product, no
recording, no acting — this is the live system.

---

## [0:24 – 1:25] — DEMO 1: AFTER-HOURS BUYER CAPTURE

**[VISUAL:** cursor clicks **Caleb Morrison** in the chat caller
chips. The chat panel populates — empty conversation, ready for the
buyer to type.]

**A:**
Caleb is the buyer. Let's send the message he'd send.

**[VISUAL:** cursor moves to the chat input. The text appears letter
by letter (or is typed in a recorded sweep — see production notes
on cadence):]

> *"Hey, is 1100 Birch still on the market?"*

**[VISUAL:** cursor clicks send. The message renders as the
customer's outgoing chat bubble. A typing-indicator (three dots)
appears below for ~1.5 seconds. Then the AI reply materializes:]

> *"It is. Want to swing by this weekend or get the details first?"*

**[VISUAL:** beat. Cursor types the next reply:]

> *"A tour would be great — Saturday afternoon?"*

**[VISUAL:** typing dots. AI replies:]

> *"Got 1pm or 3pm open."*

**A:**
She offered weekend slots without being asked. That's not generic
chatbot polish — that's the real-estate prompt knowing how showings
work. Saturday afternoons are when buyers tour.

**[VISUAL:** cursor types:]

> *"1pm."*

**[VISUAL:** AI:]

> *"Saturday 1pm. What's the best number for a confirmation text Saturday morning?"*

**[VISUAL:** cursor types:]

> *"This one's fine."*

**[VISUAL:** AI:]

> *"Lauren will be there. Talk soon."*

**[VISUAL:** now the camera (still no cut, just attention) shifts
to the **owner phone on the right**. Within 2 seconds, a new SMS
bubble drops into Lauren's phone above the seeded ones:]

> **"Showing requested · Caleb Morrison · Saturday 1pm at 1100 Birch."**

**[VISUAL:** the bubble has a brief "just arrived" entrance. About
2.5 seconds later, a small `✓ Read` indicator fades in below it.]

**B** *(voiceover, calm):*
That's Lauren's phone. She's at dinner. Buzzed once. Three lines —
who, what, when. She read it. The buyer doesn't see the agent's
side; the agent doesn't have to drive home and call back. Saturday
shows up booked.

---

## [1:25 – 2:15] — DEMO 2: LOCKBOX EMERGENCY (the credibility moment)

**[VISUAL:** cursor clicks **Jordan Bailey** in the caller chips.
The chat panel switches to Jordan's thread. The owner phone on the
right keeps Lauren's existing bubbles visible.]

**A:**
Saturday, 1:02 PM. Different buyer, at the showing right now.

**[VISUAL:** cursor types into the chat:]

> *"Hi, I'm at the Birch showing and the lockbox isn't opening."*

**[VISUAL:** typing dots — slightly faster this time. The AI reply
materializes:]

> *"That's frustrating — I'm paging Lauren right now. Are you at the front door?"*

**[VISUAL:** cursor types:]

> *"Yes — and there's a buyer with me."*

**[VISUAL:** AI:]

> *"Hold tight, she'll be in touch within 90 seconds."*

**[VISUAL:** instantly, a **red** SMS bubble drops into Lauren's
phone on the right — distinctly urgent styling, bold treatment:]

> **"Lockbox issue · Jordan Bailey · 1100 Birch Road · Buyer on-site now."**

**[VISUAL:** the urgent bubble's `✓ Read` indicator appears faster
than the previous one — about 1.5 seconds.]

**B:**
Red bubble. Top of the screen. Faster read receipt — Lauren picked
up her phone in seconds. The buyer at the door doesn't watch the
agent fumble — he sees the agent walk up two minutes later already
knowing exactly what's wrong.

---

## [2:15 – 2:50] — MEMORY + OPERATOR VIEW

**[VISUAL:** cursor moves up to the **operator portal pane** at the
top of the page. The pane is already populated — the Caleb showing
booking and Jordan lockbox alert from the previous two demos are
now both in "Recent inquiries."]

**A:**
Up here is what Lauren's office sees during the day.

**[VISUAL:** cursor hovers over the "Today's leads" headline and the
stat cards beneath (Inquiries / Active showings / Bookings, with
sparklines underneath each).]

**A:**
Today's leads, with the trend over the last month. Most agents track
this in a spreadsheet at the end of the week. Hers updates the
second something comes in.

**[VISUAL:** cursor clicks **Caleb Morrison's card** in the Recent
inquiries feed. The card expands inline — transcript preview
appears below it, showing the last three messages of the
conversation that just happened in the chat phone.]

**B:**
Every conversation, in the agent's voice, exactly as it happened.
Same data the customer side wrote — no black box.

**[VISUAL:** cursor collapses Caleb, briefly hovers the "Worth a
follow-up" section. Then a smooth scroll back up to the full
demo-page view.]

---

## [2:50 – 3:10] — INDUSTRY RANGE (the V11.0 reveal)

**[VISUAL:** cursor moves to the **⋯** in the top bar. Demo drawer
slides in from the right. Cursor opens the industry dropdown.]

**A:**
This was the real-estate version. Same product, different industries.

**[VISUAL:** dropdown shows all 12 verticals. Cursor selects
**Sunrise HVAC**. Drawer closes. The whole page transitions in one
beat — chat caller list rebuilds to HVAC personas (Marcus Reilly,
Wendy Larsen, etc.), brand label swaps to "Sunrise HVAC," owner
phone label becomes "Mike's phone," seeded SMS bubbles change to
HVAC scenarios, portal headline becomes "Today's calls."]

**B:**
One click. Whole product changes. Brokerage. HVAC shop. Plumbing.
Law firm. Med spa. Twelve verticals.

**[VISUAL:** cursor switches back to **Lawrence Realty**. Page
transitions back to real estate.]

---

## [3:10 – 3:25] — CLOSE

**[VISUAL:** browser window pulls back slightly — full demo page in
frame, populated with the day's activity from the demo we just ran.]

**A:**
This is the live demo. Same URL the prospect gets sent. Same data,
same calls, same agent view.

**B:**
We build receptionists that don't go to voicemail. Twelve industries,
one product, one URL. The link is in the description.

**[VISUAL:** fade. Overlay: contact URL + the live demo phone number.
Soft beat. End.]

---

# PRE-PRODUCTION NOTES

## Setup before recording

1. Open the demo page in a **clean browser window** at 1920×1080 (or
   2560×1440 for a 4K master). No bookmarks bar, no extensions
   showing, no devtools.
2. Confirm the page renders cleanly: Lawrence Realty selected,
   Lauren's phone showing the two pre-baked bubbles, caller chips
   showing all 7 real-estate personas (6 + New caller).
3. Pre-clear `sessionStorage` so no prior chat state lingers:
   in devtools console run `sessionStorage.clear(); location.reload();`
   then close devtools.
4. Reset the demo via the drawer's **Reset demo** button before
   recording to wipe any prior chat exchanges from the portal.
5. Hide your cursor's OS-level dot, then re-enable a visible but
   subtle cursor highlight (or post-process with a soft circle).

## Capture

- One continuous browser recording per take. Don't cut between
  scenes — the smoothness of the demo IS the demo.
- 1080p/60fps minimum. Mouse smoothing on if the recorder supports
  it.
- Speak the typed messages aloud while typing if possible (helps
  the voiceover lock in pacing). Re-record voice in studio after.

## Cadence for the typed messages

The chat input doesn't need to look like a robot. Either:

- **Hand-typed in the recording** — type at a natural cadence,
  ~3-4 chars/second with occasional pauses. Real but slightly
  faster than thinking speed.
- **Pasted with a custom input animation** — pre-type the messages,
  paste, and in post-production simulate keystrokes with a typing
  animation. More polished, less authentic.

Hand-typed is the recommended option. It feels live.

## Where to slow down

- After Caleb sends his first message: ~1.5s before the AI replies.
  Let viewers see the typing dots breathe.
- After the owner-phone SMS lands: full 2-second hold before
  continuing voiceover. The bubble appearing IS the moment.
- After the `✓ Read` indicator appears: brief beat. Don't talk
  over it.
- The Jordan emergency: cursor click on Jordan's caller chip
  should be deliberately quick — a small visual urgency cue.
- The industry switch: hold the dropdown open for ~1.2 seconds
  so all 12 options are visible before selecting HVAC.

## Voiceover tracks

Two separate vocal tracks:
- **A** — storytelling voice, mid-warm. Sets up each beat.
- **B** — operational voice, slightly drier. Names what the screen
  is showing without explaining the technology.

Treated room. 1-second silent tails after each line for clean cuts.

## Captions

Full captions throughout, since the hero video autoplays muted on
the website. Caption the SMS-bubble bodies (they're the visual hooks)
in addition to the spoken lines.

---

# TONE GUARDS

- **"AI" appears at most once.** Use "receptionist," "the system,"
  or "the platform."
- **No buzzwords.** "Seamless," "robust," "leverage,"
  "cutting-edge," "next-generation" → retake.
- **No market value, comp sales, or negotiation strategy mentioned.**
  The receptionist defers those to the agent. The script must
  mirror what the V11.0 real-estate prompt actually does.
- **No promises beyond scope.** Inbound + SMS + showing capture +
  owner alerting. Not CMAs, not automated lead nurture, not voice
  cloning.
- **The product is the visible thing.** Don't describe the
  architecture, the model, the stack, or the company. The product
  is what's on screen — let it speak.

---

# OPTIONAL — DROP-IN BEATS

If the cut runs short or one of the three demos lands flat in
recording, here are extensions:

- **Disclosure ask** (~20s, slot between Demo 1 and 2): pick
  Priya Shah, type *"Hi — I came to the open house Sunday. Could
  I get the disclosure packet?"* AI captures email, offers a second
  tour. Quieter, but proves the receptionist handles soft inquiries
  with the same care as bookings.

- **Seller intake** (~25s, slot between Demo 2 and the portal):
  pick Daniel Ellis, type *"I'm thinking about listing my house —
  what's your commission?"* AI defers commission to Lauren, captures
  address, books CMA-prep callback. Proves the "receptionist knows
  what's the agent's job" line.

- **Reset demo + replay** (~15s, post-credits): close drawer, click
  Reset demo, watch the page clear back to seeded state. Shows the
  whole flow is reproducible — anyone can hit the URL and run the
  same demo themselves.

---

# WHAT THIS SCRIPT IS NOT

- Not a phone-call recording. Zero Twilio audio. The "AI replies"
  are text in the chat, not Polly Joanna's voice.
- Not a separate operator-portal screen-grab. The portal pane is
  *part of* the demo page; you stay on `/` the entire video.
- Not a slide deck. No company background, no architecture
  diagrams, no founders speaking to camera.
- Not the final form of the showcase. A fuller version with live
  phone audio and Lauren's-iPhone screen recording (matching the
  septic `SHOWCASE_SCRIPT.md` structure) is the production stretch
  goal; it lives in `SHOWCASE_REAL_ESTATE_FULL.md` when we build it.

This version exists to ship a hero video this week from the demo
page alone.
