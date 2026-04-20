# INVESTIGATION — AI Receptionist Codebase

_Date: 2026-04-13_

## 1. Voice AI Platform / SDK

**Custom Twilio + LLM** stack — not Vapi, Retell, Synthflow, Ulio, or LiveKit.

Evidence:
- `main.py` imports `from twilio.twiml.voice_response import VoiceResponse, Gather` directly
- No Vapi/Retell/Synthflow/Ulio SDK imports anywhere
- Conversation turns are managed via Twilio `<Gather input="speech dtmf">` webhooks, with LLM calls fired synchronously inside each webhook handler
- No vendor framework abstracting call state — everything is hand-rolled TwiML

## 2. Telephony Provider

**Twilio** (verified).

- Single active number: `+1 (844) 940-3274` (from `.env`)
- Webhooks: `/voice/incoming`, `/voice/setlang`, `/voice/gather`, `/voice/status`, `/sms/incoming`
- Account SID: `AC_REDACTED_SID_PLACEHOLDER`
- Trial or paid account (paid — $5 credit added per prior conversation history)

## 3. LLM Provider and Model

**Anthropic Claude Haiku 4.5** (`claude-haiku-4-5`) via `anthropic` Python SDK v0.75.

- `llm.py` line 14: `MODEL = "claude-haiku-4-5"`
- Uses `client.beta.messages.parse(...)` with `output_format=ChatResponse` (Pydantic) for structured output
- `max_tokens = 80` (tuned for ultra-short receptionist replies)
- `anthropic.Anthropic()` instantiated at module import after `load_dotenv()`

## 4. Voice Synthesis (TTS)

**Amazon Polly Neural** via Twilio's built-in `<Say>` verb.

- `main.py` `VOICE_MAP`: English=`Polly.Joanna-Neural`, Spanish=`Polly.Lupe-Neural`, Hindi=`Polly.Kajal-Neural`, Gujarati=`Polly.Kajal-Neural` (fallback — no Gujarati Polly voice exists), plus Portuguese/Italian/Japanese/Korean/Chinese
- No separate ElevenLabs/PlayHT/OpenAI TTS integration
- All TTS is Twilio-hosted (no custom audio streaming)
- Single tier — no main-vs-transactional voice split exists yet

## 5. System Prompt Structure

**Inline string literal** in `llm.py` `SYSTEM_TEMPLATE` (lines 23–32). Single `{memory}` placeholder for caller context.

Current prompt is ~50 words, hard-coded for "Ace HVAC & Plumbing". Memory is rendered by `_format_memory()` from `memory.py` JSON store — name, phone, address, equipment, notes, last 3 history entries.

No templated multi-client support. No separation of "company persona" vs "conversation rules".

## 6. Call Lifecycle

### Inbound call flow (voice)
1. **Twilio hits** `POST /voice/incoming` with `From` header
2. `memory.get_or_create_by_phone(From)` — look up or auto-create caller record (keyed by normalized phone digits)
3. If caller has saved language → skip menu, greet directly
4. If new → DTMF language menu (1=en, 2=es, 3=hi, 4=gu)
5. `POST /voice/setlang` stores language, plays greeting, opens gather
6. **Turn loop**: Caller speaks → Twilio transcribes → `POST /voice/gather` with `SpeechResult`
7. `_run_pipeline()` calls Claude with memory + last 4 conversation turns
8. Response wrapped in single `<Gather>` with nested `<Say>` (barge-in enabled)
9. Repeats until caller hangs up
10. Emergencies: `priority="high"` branches to `<Dial>` to `ON_CALL_NUMBER` env var

### Missed call flow
- Twilio `StatusCallback` → `/voice/status` → if `CallStatus` in `{no-answer, busy, failed}` → `llm.recover()` generates SMS → sent via Twilio REST client

### SMS flow
- `POST /sms/incoming` → same pipeline as voice, reply wrapped in `<Message>` TwiML

### No existing call-duration timer. No spam filter. No usage tracking. No rate limiting.

## 7. Existing Usage Tracking

**None.** No token counting, no call duration logging, no cost tracking, no SQLite/DB, no log files beyond uvicorn's default stdout.

## 8. Multi-Tenant Structure

**None.** Hardcoded single-tenant for "Ace HVAC & Plumbing" throughout:
- `llm.py` SYSTEM_TEMPLATE: company name hardcoded
- `main.py` LANG_GREETINGS: hardcoded Joanna / Ace HVAC phrasing
- Single `TWILIO_NUMBER` in `.env`
- Single `memory.json` file

## 9. Happy Path (Current)

1. Caller rings `+18449403274`
2. Twilio hits `/voice/incoming` — new caller → language menu
3. Caller presses 1 → `/voice/setlang` → AI greets: *"Hey, this is Joanna from Ace HVAC— what's going on?"*
4. Caller speaks → Twilio transcribes → `/voice/gather` → Claude replies (1–2s latency)
5. AI speaks reply inside `<Gather>` — caller can interrupt
6. Turn loop repeats until caller hangs up or emergency triggers `<Dial>`

## 10. Existing Tests

`_test_suite.py` at repo root. Covers:
- Static endpoints (`GET /`, `GET /missed-calls`, `GET /memory/{id}`, `POST /memory/{id}`)
- Invalid body / 404 paths
- Twilio TwiML shape (`/voice/incoming`, `/voice/gather` empty speech, `/voice/status` no-op)
- Anthropic auth-error → 503 friendly response
- LLM integration (only runs if real `ANTHROPIC_API_KEY` present)

Framework: **plain urllib + manual `t()` harness**, not pytest. I will add pytest alongside it per PLAN.md, and port critical new tests to pytest.

## DO NOT DISRUPT

### Live production phone number
- **+1 (844) 940-3274** (Twilio) — active, used for live demos this week. All changes must keep this number answering as "Ace HVAC & Plumbing" in "Joanna" voice. Multi-tenant refactor MUST leave this number mapped to the existing Ace HVAC persona until explicitly remapped.

### Live integrations
- Cloudflare Tunnel URL: `https://leaders-related-difficulties-comprehensive.trycloudflare.com` (ephemeral — likely dead now; not something we'd break, but webhook URLs in Twilio console point here).
- Anthropic API key in `.env` is a real billable key (~$5 trial credits remaining).
- Twilio credentials in `.env` are real paid-account credentials.

### Web chat UI
- `index.html` is a functional demo UI used for showing the web-chat equivalent of the phone flow. Refactor must keep `/chat`, `/recover/{caller_id}`, `/missed-calls`, `/memory/*` endpoints working for the existing 3 seed callers (sarah, mike, dave).

## Platform Pivot Assessment

Not a thin wrapper over a closed platform. Full code access to voice loop, LLM call, TTS selection, memory store. Refactor proceeds **inside the codebase** (Python/FastAPI) — no external config-only pivot needed.
