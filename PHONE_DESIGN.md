# Phone System Design — Twilio Integration

Demo-friendly design. Reuses the same core logic (`classify` + `replyFor` + memory) as the web chat UI. Phone is just a different transport.

---

## 1. Architecture

```
  ┌────────────────┐   webhook    ┌────────────────────────┐
  │  Twilio Voice  │────────────▶│  FastAPI /voice/*       │
  │  (phone #)     │◀────TwiML────│  ─ load memory by phone │
  └────────────────┘              │  ─ classify intent      │──▶ Claude
         │                        │  ─ generate reply       │
         │ missed call            │  ─ write memory         │
         ▼                        └───────────┬─────────────┘
  ┌────────────────┐                          │
  │  Twilio SMS    │◀────send────────────────┘
  │  (same #)      │          (recovery message)
  └────────────────┘

                       shared SQLite: callers, calls
                       (same store the web UI uses)
```

**Key idea:** `caller_id = phone number`. Whether the caller arrives via web chat, voice, or SMS, they hit the same memory row.

---

## 2. Components

| Component | Purpose | Demo choice |
|---|---|---|
| Twilio phone number | Receive calls + SMS | 1 trial number |
| Twilio Voice webhook | Turns caller speech into HTTP | `<Gather input="speech">` |
| Twilio Messaging API | Send recovery SMS | REST from FastAPI |
| FastAPI endpoints | Glue + reuse core brain | 4 routes |
| STT | Speech → text | Twilio's built-in (free, no extra setup) |
| TTS | Text → speech | Twilio `<Say voice="Polly.Joanna">` |
| LLM | Intent + reply | Claude (same as chat UI) |
| DB | Memory | SQLite (same file) |

No Whisper, no ElevenLabs, no separate STT/TTS infra. Twilio handles both sides of audio.

---

## 3. Endpoints to Add

```
POST /voice/incoming       — first webhook when a call rings
POST /voice/gather         — receives the caller's transcribed speech
POST /voice/status         — Twilio StatusCallback (detects missed calls)
POST /sms/incoming         — handles SMS replies to recovery messages
```

All four call into the same core:
```python
intent   = classify(text)
memory   = load_caller(phone)
reply    = generate_reply(memory, text, intent)
save_turn(phone, text, reply, intent)
```

---

## 4. Incoming Call Flow

### Step A — Call rings
Twilio hits `POST /voice/incoming` with `{From, To, CallSid}`.

Server responds with TwiML:
```xml
<Response>
  <Say voice="Polly.Joanna">
    Hi, thanks for calling Ace HVAC and Plumbing.
    How can I help you today?
  </Say>
  <Gather input="speech" action="/voice/gather" speechTimeout="auto" />
</Response>
```

### Step B — Caller speaks
Twilio transcribes → `POST /voice/gather` with `SpeechResult`.

Server logic:
```
phone     = form["From"]
text      = form["SpeechResult"]
caller    = load_caller(phone)             # memory lookup
intent    = classify(text)                 # Haiku
reply     = generate_reply(caller, text, intent)  # Sonnet
save_turn(phone, "user", text, intent)
save_turn(phone, "ai", reply)
```

Response TwiML:
```xml
<Response>
  <Say voice="Polly.Joanna">{reply}</Say>
  <Gather input="speech" action="/voice/gather" speechTimeout="auto" />
</Response>
```

### Step C — Emergency branch
If `intent.priority == "high"`:
```xml
<Response>
  <Say>This sounds urgent. I'm connecting you to our on-call technician now.</Say>
  <Dial>+1415ONCALL</Dial>
</Response>
```
Caller is warm-transferred to a human. Memory is already updated with the emergency before the handoff.

---

## 5. Missed Call SMS Recovery

### Detecting a missed call
Configure Twilio `StatusCallback` on the number. When a call ends with `CallStatus=no-answer` or `busy` or `failed`, Twilio POSTs `/voice/status`.

```python
@app.post("/voice/status")
async def status(req):
    if req.form["CallStatus"] in ("no-answer", "busy", "failed"):
        phone  = req.form["From"]
        caller = load_caller(phone)
        msg    = recovery_message(caller)   # same function the chat UI uses
        twilio.messages.create(
            to=phone, from_=OUR_NUMBER, body=msg
        )
        save_turn(phone, "ai", msg, note="sms_recovery")
```

### Recovery message content
- **Returning caller:** *"Hi Sarah, Ace HVAC here — sorry we missed you! I see we did your furnace tune-up in March. Text back and I'll get you scheduled right away."*
- **New lead:** *"Hi, this is Ace HVAC — sorry we missed you! Reply here and our AI assistant can get you a quote or book a visit."*

### Two-way SMS
Reply lands at `POST /sms/incoming`. Same core pipeline. Response returned as TwiML:
```xml
<Response><Message>{reply}</Message></Response>
```
Conversation continues over SMS seamlessly. Memory persists.

---

## 6. Memory Reuse Across Channels

One table keyed by phone number:

```
callers(phone PK, name, address, equipment, notes, last_intent, updated_at)
calls(id, phone FK, channel, role, text, intent, priority, created_at)
```

New column: `channel` ∈ `{"web", "voice", "sms"}`.

That's the only phone-specific schema change. Everything else is shared with the web demo.

**Demo payoff:** caller chats on the website Monday → gets missed-call SMS Tuesday → calls in Wednesday. AI greets them by name and picks up where they left off. Same SQLite row, three different transports.

---

## 7. Minimal Setup Steps

1. `pip install twilio`
2. Sign up for Twilio trial → buy a number (~$1/mo)
3. In the number's config page, set:
   - **Voice webhook:** `https://<ngrok>.ngrok.io/voice/incoming`
   - **Status callback:** `https://<ngrok>.ngrok.io/voice/status`
   - **Messaging webhook:** `https://<ngrok>.ngrok.io/sms/incoming`
4. Run `ngrok http 8000` to expose local FastAPI
5. Add to `.env`:
   ```
   TWILIO_ACCOUNT_SID=...
   TWILIO_AUTH_TOKEN=...
   TWILIO_NUMBER=+1415XXXXXXX
   ```
6. Call the number from your cell → test the loop

Total setup: under 15 minutes.

---

## 8. What's Intentionally Left Out (Not Production)

- No signature validation on Twilio webhooks
- No retry logic on LLM calls
- No rate limiting
- No call recording / transcripts archive
- No multi-tenant (one HVAC business only)
- No barge-in / interruption handling
- No fallback human queue beyond the emergency `<Dial>`
- No PII redaction

All fine for a demo. Add before real customers.

---

## 9. Demo Script (Phone Version)

1. Call the Twilio number, let it ring out → hang up
2. Phone buzzes: SMS recovery message arrives within seconds, greets you by name if you're in the DB
3. Reply to the SMS: *"Can you come out Thursday?"* → AI books it
4. Call back: AI answers, references the Thursday booking from SMS → **cross-channel memory proven**
5. Say *"actually my water heater is leaking right now"* → AI escalates, `<Dial>` transfers to on-call

Same three features as the web demo, now over real phone lines, same brain.
