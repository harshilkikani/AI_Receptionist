# Architecture

_Last updated: 2026-04-21 (commit 9edc9c8)_

## System diagram

```
                     ┌─────────────────────────────────┐
                     │       Caller (PSTN phone)       │
                     └────────────┬────────────────────┘
                                  │
                      voice / SMS │
                                  ▼
                     ┌─────────────────────────────────┐
                     │            Twilio               │
                     │  (PSTN gateway + TwiML runtime) │
                     └────────────┬────────────────────┘
                                  │ HTTPS POST webhooks
                                  │ X-Twilio-Signature
                                  ▼
      ┌───────────────────────────────────────────────────────────┐
      │     Cloudflare Tunnel  (reclaim_tunnel.py / named tunnel) │
      └────────────┬──────────────────────────────────────────────┘
                   │
                   ▼
      ┌───────────────────────────────────────────────────────────┐
      │           FastAPI app (uvicorn on :8765)                  │
      │  ┌──────────────────────────────────────────────────────┐ │
      │  │  Middleware chain  (executed outer → inner)          │ │
      │  │    SecurityHeaders  →  AdminRateLimit                │ │
      │  │                     →  TwilioSignature               │ │
      │  └──────────────────────────────────────────────────────┘ │
      │     /voice/*  /sms/incoming  /admin/*  /client/*          │
      │         │          │            │          │              │
      │         ▼          ▼            ▼          ▼              │
      │   spam_filter  feedback     admin.py   client_portal      │
      │   call_timer   (P11)                    (P1, signed)      │
      │   owner_notify                                            │
      │         │                                                 │
      │         ▼                                                 │
      │      llm.py → Anthropic (cacheable system blocks)         │
      │         │                                                 │
      │         ▼                                                 │
      │   usage.py  →  data/usage.db  (SQLite WAL)                │
      └────────────┬──────────────────────────────────────────────┘
                   │
      ┌────────────┴───────────────────────────────────────────┐
      │  Background tasks (asyncio, started by lifespan)       │
      │    alerts.py   → daily digest + monthly invoices (P2)  │
      │    scheduler.py→ 22:00-local owner digest (P4)         │
      │                  07:00 UTC eval regression (P7)        │
      └────────────────────────────────────────────────────────┘
```

## Request lifecycle — inbound voice call

1. **Twilio → /voice/gather** with form fields `From`, `To`, `CallSid`,
   `SpeechResult`, `Language`. The `TwilioSignatureMiddleware` reads the
   raw body, validates the Twilio signature, and re-yields the body so
   FastAPI's form parsing proceeds normally. Invalid signatures 403
   when `TWILIO_VERIFY_SIGNATURES=true` or log a warning otherwise.

2. **Tenant resolution.** `tenant.load_client_by_number(To)` looks up the
   YAML for the inbound number. `spam_filter.check_phrases` scans the
   first 15 seconds of transcript for spam patterns (bypassed by any
   override keyword — addresses, service words, emergency words).
   `call_timer.check(CallSid, client, SpeechResult)` decides whether
   to pass normally, inject a wrap-up cue, or force-end.

3. **LLM turn.** `_run_pipeline` calls `llm.chat_with_usage(caller,
   SpeechResult, history, client, wrap_up_mode)`. The system prompt is
   two content blocks: a cacheable stable block containing the tenant's
   persona + rules (`cache_control: ephemeral`) and a volatile block
   with the caller's memory + any wrap-up suffix. Anthropic returns a
   `ChatResponse(reply, intent, priority)` via Pydantic parsing.

4. **Side effects.** `usage.log_turn` writes to SQLite (tokens, TTS
   chars, intent). `memory.append_turn` persists the exchange. If
   `priority=high`, `call_timer.mark_emergency` extends the duration
   cap and `owner_notify.notify_emergency` SMSes `owner_cell` with the
   caller's number, the one-line summary, and any address on file —
   *before* TwiML `<Dial>` bridges the call to `escalation_phone`.

5. **Response.** TwiML is returned as a `<Say>` nested inside a
   `<Gather>` (barge-in enabled) for normal turns, or a `<Say> +
   <Hangup>` for force-end / spam rejection / emergency transfer. On
   `/voice/status`, a successful completion may trigger a P11 YES/NO
   follow-up SMS if the flag is on and the call was long enough and
   non-emergency.
