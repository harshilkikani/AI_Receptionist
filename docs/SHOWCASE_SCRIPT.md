# Website Showcase Script — AI Receptionist (Septic Services)

_Last updated: 2026-04-21 — v2.0_
_Runtime target: 2:55–3:10_

Two hosts (**A** / **B**) alternating, plus pre-recorded caller audio.
Demo tenant: `septic_pro` (Bob / Septic Pro). Live production tenant
(`ace_hvac`) is unrelated — the showcase is specifically a septic story
because a broken septic at midnight is the clearest example of why a
business with no night shift needs an always-on receptionist.

---

## [0:00 – 0:15] — HOOK

**[VISUAL:** black screen. Distant phone ringing. Type-in caption:
*"It's 11:47 PM. A customer's septic tank just backed up into their kitchen."*]

**A** *(voiceover, calm, unhurried):*
Bob runs a septic service. Seven trucks, twelve techs, and a phone line that goes to voicemail after six. Around 30% of the calls that come in between 6 PM and 7 AM don't become customers.

**[VISUAL:** cut to A on camera briefly.]

**A:**
We built Bob a receptionist that answers every one of those calls. Here's thirty seconds of what the worst of them sounds like.

---

## [0:15 – 1:00] — DEMO 1: OVERFLOW EMERGENCY (the sell)

**[VISUAL:** split screen.
LEFT: a homeowner's phone, caller view, outbound call timer running.
RIGHT: a bare "AI Receptionist" panel with a live audio waveform.
Bottom-third tag field ready.]

**[AUDIO clip — plays through end of scene]**

**CALLER (Linda, panicked):**
Hey — we've got sewage coming up through the downstairs toilet and the kids' bathroom is starting to smell really bad, I don't know what to do —

**RECEPTIONIST (Polly Joanna, steady):**
Okay — getting a tech out there right away. What's the address?

**CALLER:**
3411 Mill Creek Road.

**RECEPTIONIST:**
Got it. Hang tight — connecting you to Bob now.

**[VISUAL:** RIGHT pane flashes a tag: **intent: Emergency • priority: HIGH**.
Then an iPhone mockup slides in on top showing Bob's lock screen:]

> **"Septic Pro: +1 (717) 555-0122 — 'sewage coming up through the downstairs toilet.' Address on file: 3411 Mill Creek Road."**

**[VISUAL:** mockup rings. Bob answers. Brief bridge sound.]

**B** *(voiceover):*
That text hits Bob's phone before the call does. He picks up knowing who it is, where the truck is going, and what he's about to see. No blind transfers, no "hello this is who again?" — Bob is already mid-sentence when Linda gets on the line.

---

## [1:00 – 1:40] — DEMO 2: MEMORY + INTENT FLEXIBILITY

**[VISUAL:** same split. Lower caption: *"Returning customer — field replaced March 18."*]

**[AUDIO clip]**

**CALLER 2 (Ellen, casual, older):**
Hey, it's Ellen Kovacs — just wanted to schedule a pump-out before Thanksgiving.

**RECEPTIONIST:**
Hey Ellen — still at 88 Ridge Road? What week works?

**[VISUAL:** RIGHT pane tags: **intent: Scheduling • priority: low**.
A small memory card fades in:]

> **Ellen Kovacs. 1000-gal concrete tank. Last pumped Sep 14 (70% full). Prefers evening callbacks after 6.**

**A:**
The AI remembered her. Address on file, tank specs, even that she doesn't pick up before 6. Every call it answers makes the next one smarter.

**[VISUAL:** hard cut — new caller. Caption: *"Wrong number."*]

**[AUDIO clip]**

**CALLER 3:**
Oh — sorry, I was trying to reach the water authority.

**RECEPTIONIST:**
No problem — you reached Septic Pro. Good luck with the utility.

**[VISUAL:** tag: **intent: General • priority: low • 9 seconds**.]

**B:**
Wrong number — nine seconds, zero questions asked. The AI knows when to get out of the way.

---

## [1:40 – 2:15] — BACK OFFICE (fast, visual)

**[VISUAL:** full-screen screen recording of `/admin`.]

**A:**
Here's what Bob sees on his end.

**[VISUAL:** cursor hovers margin row, gentle zoom.]

**A:**
Per-client margin, live. Call log with duration, outcome, and intent for every call.

**[VISUAL:** click → `/admin/analytics`. Intent distribution bars animate in, then the UTC heatmap.]

**A:**
Calls by hour so he can see exactly when the AI's earning its keep. Intent distribution so he can price his plans right.

**[VISUAL:** cut to a single iPhone mockup. Browser showing the client portal URL.]

**B:**
And this is what Bob's *customer* sees — if Bob ever sends them a link. One URL. No app. No login.

**[VISUAL:** mockup scrolls — summary card, call log, monthly invoice.]

**B:**
Calls handled, minutes used, current bill. Every invoice emails itself the first of the month.

---

## [2:15 – 2:50] — THE IMPROVEMENT LOOP (the differentiator)

**[VISUAL:** an SMS thread mockup.]

> **Septic Pro:** "How'd that go? Text YES if it worked, NO if not."
> **Customer reply:** "no, you guys sent the truck to 88 instead of 188."

**A:**
Every non-emergency call gets a one-text follow-up. YES means the AI nailed it. NO means the transcript lands in our review pile.

**[VISUAL:** terminal-style overlay showing a line in `logs/negative_feedback.jsonl` streaming in.]

**B:**
That transcript becomes a new test case the next morning. Every night the agent runs against every test we've ever recorded. If it regresses — we know before the next customer does.

**A:**
That's the difference between an AI that *answers* Bob's phone and an AI that *gets better at* answering Bob's phone.

---

## [2:50 – 3:05] — CLOSE + CTA

**[VISUAL:** A and B on camera, neutral background.
Lower-third: *"Septic receptionist is one of the AI agents we build."*]

**A:**
This was septic. It's one of the agents we build.

**B:**
Support overflow, inbound sales, scheduling, 24/7 intake — whatever line keeps missing the calls you can't afford to miss, we build the agent for it.

**A:**
Drop us a line. Let's see what your phone looks like when nobody has to pick up.

**[VISUAL:** fade to black. Overlay: contact URL. Soft dial-tone beat. End.]

---

# PRE-PRODUCTION NOTES

## Pre-record

1. **Three caller clips** — Linda (panicked ~7s), Ellen (casual ~5s), wrong-number (matter-of-fact ~4s). Phone-filter EQ (bandpass 300–3400 Hz, light compression). Different voices.
2. **Three receptionist replies** — call the live `septic_pro` tenant via the live web chat or a demo Twilio number once the agent is deployed; record the actual Polly Joanna audio. **Do not fake with a different TTS** — use the real voice.
3. **Owner's-phone SMS mock** — take a real screen recording of your own phone receiving a P3 emergency SMS (send one to yourself from the live system). Lock-screen vibration is the money shot.
4. **Screen recordings** — `/admin` → `/admin/analytics` → `/client/septic_pro?t=<token>` → invoice view. 1080p, 60fps.

## Shoot

- Two-person on-camera only for hook + close. Voiceover the middle.
- Separate voiceover tracks, treated room.
- 1-second silent tails after each line.

## Post

- Hard cuts between sections, no transitions.
- Full captions — most hero embeds autoplay muted.
- Export two cuts: captioned-muted (hero) and full-audio (click-to-play).

---

# TONE

- If "seamless", "robust", "leverage", "cutting-edge" slips in → retake.
- Real speech is leaner than written speech. Drop every third "the".
- If a line sounds like marketing copy, it is. Cut it.

---

# STRETCH TO 5 MINUTES (optional)

- **Price shopper** (~20s): "just getting quotes for a replacement drain field — ballpark?" → AI quotes + offers in-home assessment.
- **After-hours pumping request** (~20s): "tank's at 80%, can you come next week?" → AI books without paging Bob.
- **Daily digest SMS moment** (~15s): show Bob's phone at 10 PM: *"[Septic Pro] 14 calls: 2 emergency, 5 bookings, 3 filtered. Avg 72s. Top: Scheduling."*

Add any of these between sections 2 and 3. Skip to keep it punchy.
