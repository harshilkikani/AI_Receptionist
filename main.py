"""Ace HVAC & Plumbing — AI Receptionist backend.

One FastAPI app, three transports (web chat, voice, SMS), one shared brain
and one shared memory store. Run with:

    uvicorn main:app --reload

Environment: ANTHROPIC_API_KEY is required. Twilio vars are optional and
only needed if you wire up a real phone number (Step 3).
"""

import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # MUST run before importing llm (SDK reads key at instantiation)

import anthropic
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import llm
import memory
from contextlib import asynccontextmanager
from src import tenant, usage, call_timer, spam_filter, sms_limiter, alerts, owner_notify
from src import scheduler as _scheduler
from src import feedback as _feedback
from src import transcripts as _transcripts
from src import owner_commands as _owner_commands
from src import voice_style as _voice_style
from src import call_summary as _call_summary
from src import bookings as _bookings
from src import sentiment_tracker as _sentiment
from src import usage_cap as _usage_cap
from src import signup as _signup_module
from src import webhooks as _webhooks
from src import tts as _tts
from src import humanize_speech as _humanize
from src import anti_robot as _anti_robot
from src.security import AdminRateLimitMiddleware, SecurityHeadersMiddleware
from src.twilio_signature import TwilioSignatureMiddleware
from src.ops import RequestIDMiddleware, router as _ops_router, install_logging as _install_logging

import logging
# V7 — request-id-aware logging. install_logging() sets up a handler
# that includes the per-request correlation ID. Logs now look like:
#   2026-04-21 12:34:56 INFO [a3f2b9c1] receptionist handling call CAxxx
_install_logging(level=os.environ.get("LOG_LEVEL", "INFO").upper())
log = logging.getLogger("receptionist")

ROOT = Path(__file__).parent


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # P5 — prune expired demo tenants at startup so stale demo YAMLs
    # don't accidentally route live traffic after their 24h window.
    try:
        from src import onboarding as _onboarding
        removed = _onboarding.purge_expired_demos()
        if removed:
            log.info("startup: purged %d expired demo tenants: %s",
                     len(removed), ", ".join(removed))
    except Exception as e:
        log.error("startup: demo purge failed: %s", e)

    # Startup — kick off alert digest loop if enforcement is on
    alerts.start_background_loop()
    # P4 — per-client owner end-of-day digest (10 PM local)
    _scheduler.start()
    yield
    # Shutdown
    alerts.stop_background_loop()
    _scheduler.stop()


app = FastAPI(title="AI Receptionist", lifespan=_lifespan)

# P0 — security middlewares. Order matters: headers applied to every
# response (including rate-limited 429s), rate limiter runs first for
# admin-prefixed paths.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AdminRateLimitMiddleware)
# P6 — Twilio signature verification for /voice/* + /sms/incoming.
# Runs AFTER headers middleware (added later = runs earlier); adds a 403
# cheap rejection for forged webhooks before any app logic runs.
app.add_middleware(TwilioSignatureMiddleware)
# V7 — request-id correlation middleware. Added LAST so it runs FIRST,
# meaning every downstream middleware + handler shares the same ID.
app.add_middleware(RequestIDMiddleware)

# V7 — ops probes: /health (liveness) + /ready (readiness)
app.include_router(_ops_router)

# Mount admin routes (lightweight dashboard)
from src import admin as _admin_module  # noqa: E402
app.include_router(_admin_module.router)

# Mount client-facing portal (signed-URL per tenant). P1.
from src import client_portal as _client_portal_module  # noqa: E402
app.include_router(_client_portal_module.router)

# V3.12 — public /signup form for self-serve demo tenants
app.include_router(_signup_module.router)

# V4.1 — /audio/<hash>.mp3 cache server for ElevenLabs (and any future
# pre-rendered TTS provider)
app.include_router(_tts.audio_router)


@app.exception_handler(anthropic.AuthenticationError)
async def _auth_err(request: Request, exc: anthropic.AuthenticationError):
    return JSONResponse(
        status_code=503,
        content={"error": "anthropic_auth",
                 "detail": "ANTHROPIC_API_KEY is missing or invalid. "
                           "Set it in .env and restart the server."},
    )


@app.exception_handler(TypeError)
async def _missing_key_err(request: Request, exc: TypeError):
    """SDK raises TypeError when no api_key/auth_token can be resolved."""
    if "api_key" in str(exc) or "auth_token" in str(exc):
        return JSONResponse(
            status_code=503,
            content={"error": "anthropic_auth",
                     "detail": "ANTHROPIC_API_KEY is not set. "
                               "Add it to .env and restart the server."},
        )
    raise exc


@app.exception_handler(anthropic.APIError)
async def _api_err(request: Request, exc: anthropic.APIError):
    return JSONResponse(
        status_code=503,
        content={"error": "anthropic_api",
                 "detail": f"Anthropic API error: {exc}"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — web chat UI wired to real Claude
# ─────────────────────────────────────────────────────────────────────────────


class ChatIn(BaseModel):
    caller_id: str
    message: str
    # V5 — optional tenant override so the website landing page can
    # demo a specific tenant (e.g. septic_pro) regardless of what the
    # sole-real-tenant fallback would pick.
    client_id: str | None = None


def _run_pipeline(caller: dict, user_message: str, client: dict = None,
                  call_sid: str = "", wrap_up_mode: str = None) -> dict:
    """Shared brain: load memory → Claude → save turn → return response.
    `client` is the tenant config; if None, falls back to default.
    `call_sid` is the Twilio CallSid (empty for web chat / SMS)."""
    if client is None:
        client = tenant.load_default()
    history = caller.get("conversation", [])

    result, (in_tok, out_tok) = llm.chat_with_usage(
        caller, user_message, history,
        client=client, wrap_up_mode=wrap_up_mode,
    )

    # V4.3 — anti-robot post-processing. Strip "Certainly," / "I
    # understand your concern" / "Let me help you with that" / etc. The
    # prompt forbids them but Claude still slips occasionally.
    if _anti_robot.is_enabled(client):
        scrubbed, fired = _anti_robot.scrub(result.reply)
        if fired:
            log.info("anti_robot fired rules=%d call_sid=%s",
                     len(fired), call_sid)
            # Mutate the reply in place — ChatResponse is a Pydantic
            # model so we use copy + assign.
            try:
                result = result.model_copy(update={"reply": scrubbed})
            except AttributeError:
                # Older Pydantic
                result.reply = scrubbed

    # Track LLM + TTS usage (TTS char count = length of reply, since Polly
    # bills by synthesized character)
    usage.log_turn(
        call_sid=call_sid,
        client_id=client.get("id", "_default"),
        role="assistant",
        input_tokens=in_tok,
        output_tokens=out_tok,
        tts_chars=len(result.reply),
        intent=result.intent,
    )

    # V4 — capture the full transcript so the admin + client portal can
    # replay the conversation. Keyed by call_sid; silently no-ops for
    # web chat / SMS pseudo-sids without a real CallSid.
    _transcripts.record_turn(
        call_sid=call_sid,
        client_id=client.get("id", "_default"),
        role="user",
        text=user_message,
        intent=result.intent,
    )
    _transcripts.record_turn(
        call_sid=call_sid,
        client_id=client.get("id", "_default"),
        role="assistant",
        text=result.reply,
        intent=result.intent,
    )

    memory.append_turn(caller["id"], "user", user_message,
                       intent=result.intent, priority=result.priority)
    memory.append_turn(caller["id"], "assistant", result.reply)

    if result.intent == "Emergency":
        memory.add_history_note(
            caller["id"],
            f"Emergency contact — {user_message[:60]}",
        )

    return {
        "reply": result.reply,
        "intent": result.intent,
        "priority": result.priority,
        "sentiment": getattr(result, "sentiment", "neutral"),
        "caller": memory.get_caller(caller["id"]),
    }


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html")


@app.get("/missed-calls")
def missed_calls():
    """Seed callers shown in the left sidebar of the web UI."""
    return memory.list_callers()


@app.post("/chat")
def chat(body: ChatIn):
    caller = memory.get_caller(body.caller_id)
    if not caller:
        raise HTTPException(404, "caller not found")
    # V5 — if the request specified a tenant (e.g. the landing-page
    # showcase embeds `client_id: "septic_pro"`), use it. Otherwise fall
    # back to the sole-real-tenant heuristic.
    if body.client_id:
        client = tenant.load_client_by_id(body.client_id)
        if client is None or (client.get("id") or "").startswith("_"):
            raise HTTPException(404, f"client {body.client_id!r} not found")
    else:
        client = tenant.load_client_by_number("")
    return _run_pipeline(caller, body.message, client=client)


@app.post("/recover/{caller_id}")
def recover(caller_id: str):
    """Triggered when the user clicks a missed caller in the sidebar —
    generates a proactive recovery opening message via Claude."""
    caller = memory.get_caller(caller_id)
    if not caller:
        raise HTTPException(404, "caller not found")

    client = tenant.load_client_by_number("")
    result = llm.recover(caller, client=client)
    memory.append_turn(caller_id, "assistant", result.reply,
                       intent=result.intent, priority=result.priority)

    return {
        "reply": result.reply,
        "intent": result.intent,
        "priority": result.priority,
        "caller": memory.get_caller(caller_id),
    }


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — explicit memory endpoints (store / retrieve by ID or phone)
# ─────────────────────────────────────────────────────────────────────────────

# Memory is ALREADY injected into every Claude call via llm._format_memory().
# These endpoints expose the store for manual inspection, admin tooling,
# or cross-channel lookup (e.g. "does this phone number have a record?").


class MemoryUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    equipment: str | None = None
    notes: str | None = None


@app.get("/memory/{caller_id}")
def get_memory(caller_id: str):
    """Retrieve the full memory record for a caller (by ID or phone digits)."""
    caller = memory.get_caller(caller_id)
    if not caller:
        # Try phone lookup as fallback
        digits = memory.normalize_phone(caller_id)
        caller = memory.get_caller(digits)
    if not caller:
        raise HTTPException(404, "caller not found")
    return caller


@app.post("/memory/{caller_id}")
def update_memory(caller_id: str, body: MemoryUpdate):
    """Update stable facts on a caller's memory record."""
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    caller = memory.update_caller(caller_id, **fields)
    if not caller:
        raise HTTPException(404, "caller not found")
    return caller


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Twilio phone integration (voice + SMS)
# ─────────────────────────────────────────────────────────────────────────────

# All four endpoints below reuse `_run_pipeline` / `llm.recover` — the exact
# same code path as the web chat. Memory is keyed by phone number, so a
# caller who chats on the web and then calls from their phone hits the same
# record if the phone number matches an existing row.

try:
    from twilio.twiml.voice_response import VoiceResponse
    from twilio.twiml.messaging_response import MessagingResponse
    from twilio.rest import Client as TwilioClient
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False


def _twilio_client():
    """Lazy-init Twilio REST client (only needed for outbound SMS recovery)."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    tok = os.environ.get("TWILIO_AUTH_TOKEN")
    if not (TWILIO_AVAILABLE and sid and tok):
        return None
    return TwilioClient(sid, tok)


def _twiml(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


# ── Voice settings ──────────────────────────────────────────────────────
# Neural voices sound dramatically more human than standard Polly.
# Map language codes to the best available neural voice.
VOICE_MAP = {
    "en": "Polly.Joanna-Neural",
    "es": "Polly.Lupe-Neural",
    "hi": "Polly.Kajal-Neural",
    "gu": "Polly.Kajal-Neural",     # No Polly Gujarati voice; Hindi neural is closest
    "pt": "Polly.Camila-Neural",
    "it": "Polly.Bianca-Neural",
    "ja": "Polly.Kazuha-Neural",
    "ko": "Polly.Seoyeon-Neural",
    "zh": "Polly.Zhiyu-Neural",
}

# Tiered voice map — short transactional phrases can use a cheaper voice.
# On Polly via Twilio all Neural voices cost the same, so this is a scaffold
# for when operator switches to ElevenLabs (Flash vs. Turbo vs. Multilingual)
# or another provider with actual tiered pricing.
#
# Currently both tiers resolve to the same Polly Neural voice — no cost win
# on the current TTS stack. Operator updates this map when they swap TTS.
VOICE_TIER_MAP = {
    "premium": VOICE_MAP,          # main conversational turns
    "flash": VOICE_MAP,            # short transactional phrases
    "standard": {                  # downgrade to standard (non-neural) if needed
        "en": "Polly.Joanna",
        "es": "Polly.Lupe",
        "hi": "Polly.Aditi",
        "gu": "Polly.Aditi",
        "pt": "Polly.Camila",
        "it": "Polly.Bianca",
        "ja": "Polly.Mizuki",
        "ko": "Polly.Seoyeon",
        "zh": "Polly.Zhiyu",
    },
}
# Twilio <Gather> language codes for speech recognition
STT_LANG_MAP = {
    "en": "en-US",
    "es": "es-US",
    "hi": "hi-IN",
    "gu": "gu-IN",
    "pt": "pt-BR",
    "it": "it-IT",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "zh": "zh-CN",
}
DEFAULT_LANG = "en"


def _voice_for(lang: str, client: dict = None, mode: str = "main") -> str:
    """Return Polly voice for a language. `mode` is 'main' or 'transactional'
    — when the client config specifies a cheaper tier for transactional
    phrases, we use that map."""
    if client is None:
        tier = "premium"
    else:
        plan = client.get("plan") or {}
        key = "voice_tier_transactional" if mode == "transactional" else "voice_tier_main"
        tier = plan.get(key, "premium")
    table = VOICE_TIER_MAP.get(tier, VOICE_MAP)
    return table.get(lang, table.get(DEFAULT_LANG, VOICE_MAP[DEFAULT_LANG]))


def _stt_lang(lang: str) -> str:
    return STT_LANG_MAP.get(lang, STT_LANG_MAP[DEFAULT_LANG])


# ── Helpers ────────────────────────────────────────────────────────────

DTMF_LANG = {"1": "en", "2": "es", "3": "hi", "4": "gu"}

def _greeting_for(client: dict, lang: str) -> str:
    """Per-client greeting. Uses client['name']; Joanna is the persona name
    (hardcoded — same AI answers for every client)."""
    company = client["name"]
    if lang == "es":
        return f"Hola, habla Joanna de {company}— en que te puedo ayudar?"
    if lang == "hi":
        return f"Hey, main Joanna, {company} se— kya hua batao?"
    if lang == "gu":
        return f"Hey, hu Joanna, {company} thi— shu thayum kahejo?"
    return f"Hey, this is Joanna from {company}— what's going on?"


def _respond(vr, message: str, lang: str, client: dict = None):
    """The ONE pattern used everywhere: Say (or Play) inside Gather.
    Caller can interrupt. If they stay silent, Twilio re-fires
    /voice/gather with empty SpeechResult and we handle it there.

    V3.3 — Polly path can wrap in SSML for natural pacing.
    V4.1 — Pluggable TTS. If the tenant opts into ElevenLabs (or
    another non-Polly provider), the response uses <Play> with a cached
    audio URL. On any failure we transparently fall back to Polly so
    the call survives.
    """
    g = vr.gather(
        input="speech dtmf",
        action="/voice/gather",
        method="POST",
        speech_timeout="auto",
        speech_model="phone_call",
        enhanced=True,
        language=_stt_lang(lang),
    )
    # V4.2 — natural speech preprocessing for any TTS provider.
    if _humanize.is_enabled(client):
        message = _humanize.humanize_for_speech(message)
    payload = _tts.render(message, client=client, lang=lang)
    if payload.kind == "play" and payload.url:
        g.play(payload.url)
    else:
        # Polly path keeps the SSML enhancement for opted-in tenants
        style = _voice_style.style_for(client, mode="main")
        ssml_text = (_voice_style.apply_ssml(payload.text, style=style)
                     if style else payload.text)
        polly_voice = payload.polly_voice or _voice_for(lang, client, mode="main")
        g.say(ssml_text, voice=polly_voice)


# ── Voice endpoints ───────────────────────────────────────────────────

@app.post("/voice/incoming")
def voice_incoming(From: str = Form(...), To: str = Form(default=""),
                   CallSid: str = Form(default="")):
    if not TWILIO_AVAILABLE:
        raise HTTPException(500, "twilio package not installed")

    client = tenant.load_client_by_number(To)
    usage.start_call(CallSid, client["id"], From, To)
    call_timer.record_start(CallSid, client["id"])

    # V3.11 — hard usage cap. If the tenant has set plan.hard_cap_calls
    # and the current month's total has hit it, politely refuse rather
    # than burning more LLM tokens for a client who's beyond their plan.
    cap_status = _usage_cap.is_capped(client)
    if cap_status["capped"]:
        log.info("usage_cap_hit call_sid=%s client=%s current=%d cap=%d",
                 CallSid, client["id"], cap_status["current"], cap_status["cap"])
        vr_cap = VoiceResponse()
        vr_cap.say(_usage_cap.capped_message(client),
                   voice=_voice_for("en", client, mode="transactional"))
        vr_cap.hangup()
        usage.end_call(CallSid, outcome="capped")
        call_timer.record_end(CallSid)
        return _twiml(str(vr_cap))

    # Spam filter layer 1: caller-ID blocklist (before any LLM cost)
    number_check = spam_filter.check_number(From, client["id"], CallSid)
    if number_check["reject"]:
        log.info("spam_number rejected call_sid=%s from=%s reason=%s",
                 CallSid, From, number_check["reason"])
        vr = VoiceResponse()
        vr.say("Thanks, we're not taking calls from this number. Goodbye.",
               voice=_voice_for("en", client, mode="transactional"))
        vr.hangup()
        usage.end_call(CallSid, outcome="spam_number")
        call_timer.record_end(CallSid)
        return _twiml(str(vr))

    caller = memory.get_or_create_by_phone(From)
    saved_lang = caller.get("language")
    vr = VoiceResponse()

    # Returning caller with known language — skip menu, greet directly
    if saved_lang and saved_lang in VOICE_MAP:
        first_name = (caller.get("name") or "").split(" ")[0]
        company = client["name"]
        if caller.get("type") == "return" and first_name:
            greeting = f"Hey {first_name}! It's {company}— what's going on?"
        else:
            greeting = _greeting_for(client, saved_lang)
        _respond(vr, greeting, saved_lang, client=client)
        return _twiml(str(vr))

    # New caller — language selection (only time this ever happens)
    vr.say(f"Hey, thanks for calling {client['name']}.", voice=_voice_for("en"))
    vr.say("For English, press 1.", voice=_voice_for("en"))
    vr.say("Para espanol, presione 2.", voice=_voice_for("es"))
    vr.say("Hindi ke liye 3 dabaayein.", voice=_voice_for("hi"))
    vr.say("Gujarati maate 4 dabaavo.", voice=_voice_for("hi"))
    vr.gather(input="dtmf", action="/voice/setlang", method="POST",
              num_digits=1, timeout=5)
    vr.redirect("/voice/setlang?Digits=1", method="POST")
    return _twiml(str(vr))


@app.post("/voice/setlang")
def voice_setlang(From: str = Form(...), Digits: str = Form(default="1"),
                  To: str = Form(default="")):
    if not TWILIO_AVAILABLE:
        raise HTTPException(500, "twilio package not installed")

    client = tenant.load_client_by_number(To)
    lang = DTMF_LANG.get(Digits, DEFAULT_LANG)
    caller = memory.get_or_create_by_phone(From)
    memory.update_caller(caller["id"], language=lang)

    vr = VoiceResponse()
    _respond(vr, _greeting_for(client, lang), lang, client=client)
    return _twiml(str(vr))


@app.post("/voice/gather")
def voice_gather(From: str = Form(...),
                 SpeechResult: str = Form(default=""),
                 Digits: str = Form(default=""),
                 Language: str = Form(default=""),
                 To: str = Form(default=""),
                 CallSid: str = Form(default="")):
    """ONE endpoint, ONE gather per response. No chains, no redirects."""
    if not TWILIO_AVAILABLE:
        raise HTTPException(500, "twilio package not installed")

    # DTMF 0 → language switch (silent — no mid-call menu announcement)
    if Digits == "0":
        caller = memory.get_or_create_by_phone(From)
        memory.update_caller(caller["id"], language=None)
        vr = VoiceResponse()
        vr.redirect("/voice/incoming", method="POST")
        return _twiml(str(vr))

    client = tenant.load_client_by_number(To)
    caller = memory.get_or_create_by_phone(From)
    lang = (Language[:2] if Language else "") or caller.get("language") or DEFAULT_LANG
    if lang != caller.get("language"):
        memory.update_caller(caller["id"], language=lang)

    vr = VoiceResponse()

    # Empty speech → single re-prompt, no "still there" spam
    if not SpeechResult.strip():
        _respond(vr, "Sorry, didn't catch that— what was that?", lang,
                 client=client)
        return _twiml(str(vr))

    # ── Spam filter layer 2: phrase detection (first 15s) ──────────────
    timer_pre = call_timer.check(CallSid, client, caller_speech="")
    phrase_check = spam_filter.check_phrases(
        transcript=SpeechResult,
        seconds_since_start=timer_pre["elapsed"],
        client_id=client["id"],
        call_sid=CallSid,
        from_phone=From,
    )
    if phrase_check["reject"]:
        log.info("spam_phrase rejected call_sid=%s phrase=%s",
                 CallSid, phrase_check.get("phrase"))
        vr2 = VoiceResponse()
        vr2.say("Thanks, we're not interested. Goodbye.",
                voice=_voice_for(lang, client, mode="transactional"))
        vr2.hangup()
        usage.end_call(CallSid, outcome="spam_phrase")
        call_timer.record_end(CallSid)
        return _twiml(str(vr2))

    # ── Call duration check (Section A) ─────────────────────────────────
    timer = call_timer.check(CallSid, client, caller_speech=SpeechResult)
    log.info(
        "call_timer call_sid=%s elapsed=%.1fs cap=%ds action=%s enforce=%s",
        CallSid, timer["elapsed"], timer["cap"],
        timer["action"], timer["enforcement_active"],
    )

    # Past cap and enforcement active → skip LLM entirely, end politely
    if timer["action"] == "force_end":
        owner = client.get("owner_name", "the owner")
        goodbye = f"Okay— {owner} will call you back within the hour. Talk soon."
        vr.say(goodbye, voice=_voice_for(lang, client, mode="transactional"))
        vr.hangup()
        usage.end_call(CallSid, outcome="duration_capped")
        call_timer.record_end(CallSid)
        return _twiml(str(vr))

    # ── The entire flow: speech → Claude → one response → one gather ──
    result = _run_pipeline(caller, SpeechResult, client=client,
                           call_sid=CallSid, wrap_up_mode=timer["wrap_up_mode"])

    # V3.7 — sentiment tracking. If the caller has been frustrated/angry
    # for N consecutive turns, promote this to a priority=high call so
    # the rest of the handler routes them to escalation_phone.
    sentiment_result = _sentiment.record(CallSid, result.get("sentiment") or "neutral")
    if sentiment_result["should_escalate"] and result["priority"] != "high":
        log.warning(
            "sentiment_escalation overriding priority call_sid=%s consecutive=%d",
            CallSid, sentiment_result["consecutive"],
        )
        result["priority"] = "high"
        result["sentiment_escalation"] = True

    if result["priority"] == "high":
        # Mark the call as emergency — extends cap to 360s for any subsequent turns
        call_timer.mark_emergency(CallSid)
        # V3.13 — fire emergency.triggered webhook (best-effort)
        _webhooks.fire_safe("emergency.triggered", client, {
            "call_sid": CallSid,
            "from_number": From,
            "summary": SpeechResult[:200] if SpeechResult else "",
            "address": caller.get("address") if caller else None,
            "sentiment_escalation": result.get("sentiment_escalation", False),
        })
        # P3 — push an SMS to the owner's cell before transferring so they
        # see who called + the one-line summary before their phone rings.
        # Best-effort: failure never disrupts the caller transfer.
        try:
            owner_notify.notify_emergency(
                client,
                caller_phone=From,
                summary=SpeechResult,
                address=(caller.get("address") or None),
                call_sid=CallSid,
                twilio_client=_twilio_client(),
                twilio_from=os.environ.get("TWILIO_NUMBER"),
            )
        except Exception as e:  # never fail the call for an owner-alert bug
            log.error("owner_notify raised unexpectedly: %s", e)
        vr.say(result["reply"], voice=_voice_for(lang))
        on_call = client.get("escalation_phone") or os.environ.get("ON_CALL_NUMBER")
        if on_call:
            vr.say("Hang tight— connecting you now.",
                   voice=_voice_for(lang, client, mode="transactional"))
            vr.dial(on_call)
            usage.end_call(CallSid, outcome="emergency_transfer", emergency=True)
        else:
            vr.say("Got a tech being paged— they'll call you back in ten.",
                   voice=_voice_for(lang, client, mode="transactional"))
        return _twiml(str(vr))

    # Normal reply — one Say inside one Gather. That's it.
    _respond(vr, result["reply"], lang, client=client)
    return _twiml(str(vr))


@app.post("/voice/status")
def voice_status(From: str = Form(...), CallStatus: str = Form(...),
                 To: str = Form(default=""), CallSid: str = Form(default=""),
                 CallDuration: str = Form(default="0")):
    """Twilio StatusCallback. Records call end in the usage tracker, and for
    missed calls fires a recovery SMS."""
    # End the call record for any terminal status
    if CallStatus in ("completed", "no-answer", "busy", "failed", "canceled"):
        outcome_map = {
            "completed": "normal",
            "no-answer": "no_answer",
            "busy": "busy",
            "failed": "failed",
            "canceled": "canceled",
        }
        outcome = outcome_map.get(CallStatus, CallStatus)
        usage.end_call(CallSid, outcome=outcome)
        call_timer.record_end(CallSid)

        # V3.4 — generate a 1-line AI summary for the call. Best-effort;
        # a failure never affects the call record. The module's own
        # guards (duration, outcome, transcript-empty) decide whether
        # to actually summarize.
        try:
            _call_summary.generate_summary(CallSid)
        except Exception as e:
            log.error("call_summary.generate_summary error: %s", e)

        # V3.6 — booking extraction. Only runs for outcome='normal' calls
        # with a Scheduling intent turn + transcript. Best-effort.
        booking_row = None
        try:
            booking_row = _bookings.maybe_extract_from_call(CallSid)
        except Exception as e:
            log.error("bookings.maybe_extract_from_call error: %s", e)

        # V3.13 — fire webhook events. Best-effort via fire_safe so a
        # crash in delivery never disrupts the caller response.
        _client_for_webhooks = tenant.load_client_by_number(To)
        _webhooks.fire_safe("call.ended", _client_for_webhooks, {
            "call_sid": CallSid,
            "from_number": From,
            "outcome": outcome,
            "duration_s": int(CallDuration or "0"),
        })
        if booking_row:
            _webhooks.fire_safe("booking.created", _client_for_webhooks, {
                "booking_id": booking_row.get("id"),
                "call_sid": CallSid,
                "caller_name": booking_row.get("caller_name"),
                "address": booking_row.get("address"),
                "service": booking_row.get("service"),
                "requested_when": booking_row.get("requested_when"),
            })

        # P11 — post-call feedback SMS (opt-in via ENFORCE_FEEDBACK_SMS).
        # Only fire for natural call completions; spam/duration-capped/
        # emergency outcomes bypass via the module's own guards.
        if outcome == "normal":
            try:
                _client = tenant.load_client_by_number(To)
                duration_s = int(CallDuration or "0")
                caller = memory.get_or_create_by_phone(From)
                _feedback.maybe_send_followup(
                    CallSid, _client,
                    caller_phone=From, outcome=outcome,
                    duration_s=duration_s, emergency=False,
                    twilio_client=_twilio_client(),
                    twilio_from=os.environ.get("TWILIO_NUMBER"),
                    conversation=(caller or {}).get("conversation") or [],
                )
            except Exception as e:
                log.error("feedback.maybe_send_followup error: %s", e)

    # Missed call recovery — SMS flow
    if CallStatus not in ("no-answer", "busy", "failed"):
        return {"ok": True, "action": "none"}

    client = tenant.load_client_by_number(To)
    caller = memory.get_or_create_by_phone(From)
    result = llm.recover(caller, client=client)
    memory.append_turn(caller["id"], "assistant", result.reply,
                       intent=result.intent, priority=result.priority)

    # SMS rate-limit check before sending (Section D)
    sms_decision = sms_limiter.should_send(CallSid, client, result.reply)
    if not sms_decision["allow"]:
        log.info("recovery_sms_blocked call_sid=%s reason=%s",
                 CallSid, sms_decision["reason"])
        return {"ok": True, "action": "sms_blocked",
                "reason": sms_decision["reason"],
                "message": sms_decision["body"]}

    tw = _twilio_client()
    tw_number = os.environ.get("TWILIO_NUMBER")
    if tw and tw_number:
        tw.messages.create(to=From, from_=tw_number, body=sms_decision["body"])
        usage.log_sms(CallSid, client["id"], From, sms_decision["body"],
                      direction="outbound")
        return {"ok": True, "action": "sms_sent",
                "message": sms_decision["body"]}

    # Creds not configured — still returned the generated message so you can
    # verify the flow without real Twilio credentials
    return {"ok": True, "action": "generated_only",
            "message": sms_decision["body"]}


@app.post("/sms/incoming")
def sms_incoming(From: str = Form(...), Body: str = Form(...),
                 To: str = Form(default=""),
                 MessageSid: str = Form(default="")):
    """Two-way SMS. Replies flow through the same pipeline as voice,
    but with SMS-specific rate limiting."""
    if not TWILIO_AVAILABLE:
        raise HTTPException(500, "twilio package not installed")

    client = tenant.load_client_by_number(To)
    caller = memory.get_or_create_by_phone(From)

    # Log the inbound message toward this "conversation" (keyed by phone)
    sms_conv_key = f"SMS_{memory.normalize_phone(From)}"
    usage.log_sms(sms_conv_key, client["id"], From, Body, direction="inbound")

    # V6 — HELP-style command short-circuit. If the inbound body is
    # "HELP" / "INFO" / "STATUS" / "LINK", reply with a cheat sheet
    # (portal URL for known owners, polite redirect for strangers) and
    # skip the LLM entirely.
    try:
        help_result = _owner_commands.handle_help_sms(
            Body, from_phone=From, client=client,
            twilio_client=None,  # reply via TwiML is cheaper than REST
        )
    except Exception as e:
        log.error("owner_commands.handle_help_sms error: %s", e)
        help_result = {"handled": False}
    if help_result.get("handled"):
        log.info("help_sms variant=%s to=%s",
                 help_result.get("variant"), From)
        mr = MessagingResponse()
        mr.message(help_result["reply"])
        return _twiml(str(mr))

    # P11 — if this body is a YES/NO reply to a prior feedback SMS,
    # record it + (on NO) dump the transcript to negative_feedback.jsonl.
    # We short-circuit the AI reply on a matched YES/NO to avoid chaining
    # conversations unnecessarily.
    try:
        feedback_result = _feedback.record_response(From, Body)
    except Exception as e:
        log.error("feedback.record_response error: %s", e)
        feedback_result = {"matched": False}
    if feedback_result.get("matched"):
        log.info("feedback_matched call_sid=%s response=%s",
                 feedback_result.get("call_sid"),
                 feedback_result.get("response"))
        # V3.13 — fire feedback.negative webhook on NO responses.
        if feedback_result.get("response") == "no":
            _webhooks.fire_safe("feedback.negative", client, {
                "call_sid": feedback_result.get("call_sid"),
                "from_number": From,
                "body": Body[:200],
            })
        # Acknowledge briefly — keeps the caller from wondering if the
        # message went through.
        mr = MessagingResponse()
        if feedback_result["response"] == "yes":
            mr.message("Thanks — passing that along!")
        else:
            mr.message("Got it — we'll follow up.")
        return _twiml(str(mr))

    # Run the LLM
    result = _run_pipeline(caller, Body, client=client, call_sid=sms_conv_key)

    # Rate-limit check
    sms_decision = sms_limiter.should_send(sms_conv_key, client, result["reply"])

    mr = MessagingResponse()
    if sms_decision["allow"]:
        mr.message(sms_decision["body"])
        usage.log_sms(sms_conv_key, client["id"], From, sms_decision["body"],
                      direction="outbound")
    else:
        log.info("inbound_sms_reply_blocked reason=%s", sms_decision["reason"])
        # Empty TwiML = no outbound reply
    return _twiml(str(mr))
