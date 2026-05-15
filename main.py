"""Ace HVAC & Plumbing — AI Receptionist backend.

One FastAPI app, three transports (web chat, voice, SMS), one shared brain
and one shared memory store. Run with:

    uvicorn main:app --reload

Environment: ANTHROPIC_API_KEY is required. Twilio vars are optional and
only needed if you wire up a real phone number (Step 3).
"""

import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()  # MUST run before importing llm (SDK reads key at instantiation)

import anthropic
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import JSONResponse
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
from src import grounding as _grounding
# V10.0 — V7.2 disfluency injection retired from the pipeline.
# Module kept on disk for one version in case of rollback.
# from src import disfluency as _disfluency  # noqa: deprecated
from src import audio_cache as _audio_cache_module   # V8.9b filler lookup
from src import recordings as _recordings
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
    # V5.4 — run consolidated SQLite migrations FIRST. Idempotent;
    # every additive column from V3.4 + V4.5 is applied here so fresh
    # deploys don't depend on lazy in-module migrations firing later.
    try:
        from src import migrations as _migrations
        result = _migrations.run_all()
        if result.get("applied"):
            log.info("startup: migrations applied: %s",
                     ", ".join(result["applied"]))
        if result.get("errors"):
            log.error("startup: migration errors: %s",
                      ", ".join(result["errors"]))
    except Exception as e:
        log.error("startup: migrations crashed: %s", e)

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

    # V5.6 — bound the audio cache.
    # V8.10a — prewarm now runs in a BACKGROUND THREAD because the
    # prewarm model (multilingual_v2) is ~3-4x slower than Flash and
    # ~30 phrases per tenant would block uvicorn startup for ~60s.
    # The server starts serving requests immediately; cache fills
    # behind the scenes. Cache misses during the fill window degrade
    # to the live runtime model (Flash) — still fast, just less
    # prosody-rich until the cache catches up.
    try:
        from src import audio_cache as _audio_cache
        ev = _audio_cache.evict_if_needed()
        if ev.get("evicted_age") or ev.get("evicted_size"):
            log.info("startup: audio cache evicted age=%d size=%d freed=%d bytes",
                     ev["evicted_age"], ev["evicted_size"], ev["bytes_freed"])
    except Exception as e:
        log.error("startup: audio cache evict step failed: %s", e)

    def _prewarm_in_background():
        try:
            from src import audio_cache as _ac
            pw = _ac.prewarm_all()
            if pw.get("tenants_prewarmed"):
                log.info("background prewarm complete tenants=%d rendered=%d "
                         "skipped=%d errors=%d",
                         pw["tenants_prewarmed"], pw["rendered"],
                         pw["skipped"], pw["errors"])
        except Exception as e:
            log.error("background prewarm crashed: %s", e)

    import threading as _threading
    try:
        _threading.Thread(target=_prewarm_in_background,
                          daemon=True,
                          name="audio-cache-prewarm").start()
        log.info("startup: audio cache prewarm dispatched in background")
    except Exception as e:
        log.error("startup: failed to dispatch prewarm thread: %s", e)

    # V6.4 — every background loop start wrapped. A scheduler/alerts
    # crash at boot used to take the whole app down before any voice
    # webhook could answer; now we log + continue. Phone path is what
    # matters; background digests can be repaired separately.
    try:
        alerts.start_background_loop()
    except Exception as e:
        log.error("startup: alerts loop failed to start: %s", e)
    try:
        _scheduler.start()
    except Exception as e:
        log.error("startup: scheduler failed to start: %s", e)

    # V9.1 — seed the marketing-demo tenant (septic_pro) so the portal
    # has real-looking activity for live demos. Idempotent: skips if
    # rows already exist. Never touches the live tenant.
    try:
        from src import demo_seed as _demo_seed
        r = _demo_seed.seed_septic_pro()
        if r.get("seeded"):
            log.info("startup: demo seed planted v=%d sms=%d",
                     r.get("voice", 0), r.get("sms", 0))
    except Exception as e:
        log.error("startup: demo seed failed (non-fatal): %s", e)

    yield

    # Shutdown — same defense in depth on the way out
    try:
        alerts.stop_background_loop()
    except Exception as e:
        log.error("shutdown: alerts loop stop failed: %s", e)
    try:
        _scheduler.stop()
    except Exception as e:
        log.error("shutdown: scheduler stop failed: %s", e)


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

# V4.5 — /admin/recording/{call_sid}.mp3 streams the Twilio recording
app.include_router(_recordings.router)

# V4.6 — per-tenant ICS calendar feed
from src import calendar_feed as _calendar_feed  # noqa: E402
app.include_router(_calendar_feed.router)


# ── V6.2 — voice-path-aware error handling ─────────────────────────────
# Twilio displays "We are sorry, an application error has occurred" on
# any non-2xx response or any non-TwiML body. The exception handlers
# below used to return JSON 503 — Twilio's worst-case caller experience.
# Now we detect /voice/* paths and return a polite TwiML <Say> + <Hangup>
# instead. The caller hears something coherent and we still log the
# underlying error for ops.

_FRIENDLY_FAILURE_MESSAGE = (
    "Sorry, I'm having a brief issue on my end. "
    "Please give us a quick call back in a moment."
)


def _voice_failure_twiml(message: str = None) -> Response:
    """Build TwiML for an unrecoverable failure on a /voice/* webhook.
    Always 200 OK — Twilio retries on 5xx and shows 'application error'
    to the caller. We'd rather they hear a real sentence and hang up."""
    try:
        from twilio.twiml.voice_response import VoiceResponse as _VR
        vr = _VR()
        vr.say(message or _FRIENDLY_FAILURE_MESSAGE,
               voice="Polly.Joanna-Neural")
        vr.hangup()
        body = str(vr)
    except Exception:
        # Twilio SDK not importable — hand-craft minimal valid TwiML.
        body = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<Response><Say voice="Polly.Joanna-Neural">'
                f'{message or _FRIENDLY_FAILURE_MESSAGE}'
                '</Say><Hangup/></Response>')
    return Response(content=body, media_type="application/xml")


def _is_voice_path(request: Request) -> bool:
    return request.url.path.startswith("/voice/")


@app.exception_handler(anthropic.AuthenticationError)
async def _auth_err(request: Request, exc: anthropic.AuthenticationError):
    log.error("anthropic auth error on %s: %s", request.url.path, exc)
    if _is_voice_path(request):
        return _voice_failure_twiml()
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
        log.error("missing API key on %s: %s", request.url.path, exc)
        if _is_voice_path(request):
            return _voice_failure_twiml()
        return JSONResponse(
            status_code=503,
            content={"error": "anthropic_auth",
                     "detail": "ANTHROPIC_API_KEY is not set. "
                               "Add it to .env and restart the server."},
        )
    raise exc


@app.exception_handler(anthropic.APIError)
async def _api_err(request: Request, exc: anthropic.APIError):
    log.error("anthropic api error on %s: %s", request.url.path, exc)
    if _is_voice_path(request):
        return _voice_failure_twiml()
    return JSONResponse(
        status_code=503,
        content={"error": "anthropic_api",
                 "detail": f"Anthropic API error: {exc}"},
    )


@app.exception_handler(Exception)
async def _last_resort(request: Request, exc: Exception):
    """V6.2 last line of defense: any unhandled exception on a /voice/*
    path becomes friendly TwiML. Non-voice paths fall through to
    FastAPI's default 500 (preserves debugging info for admin routes)."""
    if not _is_voice_path(request):
        # Re-raise so FastAPI's normal 500 handling kicks in
        raise exc
    log.error("unhandled exception on %s: %s: %s",
              request.url.path, type(exc).__name__, exc)
    return _voice_failure_twiml()


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
    # V10.4 — combined-demo industry-context override. When the
    # prospect picks "HVAC" or "Real estate" in the demo's tenant
    # switcher, the chat is sent with industry="hvac" or
    # "real-estate" so the LLM responds with the matching business
    # context. The DB write still scopes to septic_pro (the demo's
    # marketing tenant) but the LLM persona honors the switcher.
    industry: str | None = None


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
            try:
                result = result.model_copy(update={"reply": scrubbed})
            except AttributeError:
                result.reply = scrubbed

    # V4.4 — strict grounding. Replaces sentences containing prices the
    # tenant never advertised with a "let me get the exact number" line.
    # Critical trust feature: a customer who hears "$249" expects $249.
    grounded, violations = _grounding.verify_reply(result.reply, client)
    if violations:
        log.warning("grounding fired count=%d call_sid=%s prices=%s",
                    len(violations), call_sid,
                    [v["prices_quoted"] for v in violations])
        try:
            result = result.model_copy(update={"reply": grounded})
        except AttributeError:
            result.reply = grounded

    # V8.3 — emergency keyword guard. The new prompt tells Claude "only
    # emergency when caller literally mentions one of: {{keywords}}".
    # Claude still over-classifies sometimes ("AC stopped in summer" got
    # marked Emergency despite the explicit anti-example). Override the
    # priority to "low" when the LLM said high but none of the tenant's
    # configured emergency_keywords appear in the caller's speech.
    if result.priority == "high":
        keywords = [k.lower() for k in (client.get("emergency_keywords") or [])]
        lower_speech = (user_message or "").lower()
        if keywords and not any(k in lower_speech for k in keywords):
            log.info("emergency_keyword_guard: downgrading priority "
                     "(LLM marked high but no keyword in speech) "
                     "call_sid=%s", call_sid)
            try:
                result = result.model_copy(update={"priority": "low"})
            except AttributeError:
                result.priority = "low"

    # V10.0 — V7.2 disfluency injection RETIRED. The conversation audit
    # showed 46% of assistant turns opened with a filler and diversity
    # was 0.2 — the LLM was already using the openers (per prompt), the
    # V8.9b cached fillers were already playing pre-reply, and V7.2 was
    # the third layer stacking on top. Removing it lets the LLM's
    # natural output land unmodified. The disfluency module is kept on
    # disk under src/disfluency.py (deprecated) for one version in case
    # of rollback; nothing calls it.

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
    """V9.5 — combined demo at /. Two panes (customer chat + operator
    portal) using the same V9.4 design system as the real portal.
    No marketing copy — the product IS the demo."""
    from src.design import demo_page, icon
    from src.client_portal import _today_body
    from fastapi.responses import HTMLResponse

    # ── Operator pane: real portal Today body for septic_pro ──────
    # V9.6.1 — refresh the seeded timestamps each render so "Recent
    # activity" never shows stale "13 hours ago" labels on the demo.
    try:
        from src import demo_seed as _demo_seed
        _demo_seed.refresh_timestamps()
    except Exception as e:
        log.debug("demo refresh_timestamps non-fatal: %s", e)
    try:
        operator_inner = _today_body(
            "septic_pro", t="", include_invoice_link=False)
    except Exception as e:
        log.error("demo: _today_body failed: %s", e)
        operator_inner = (
            '<div class="empty empty-warm">'
            '<div class="empty-title">Demo data not ready yet</div>'
            '<div class="empty-sub">Restart the server to seed the '
            'marketing tenant.</div></div>'
        )

    operator_pane = f"""
    <section class="demo-pane demo-pane-operator">
      <div class="pane-label">
        <span>What you see</span>
        <span class="live-pulse" id="live-pulse" aria-hidden="true">
          <span class="live-dot"></span> Live
        </span>
      </div>
      <div class="portal-shell">
        <div class="portal-shell-body" id="portal-body">{operator_inner}</div>
      </div>
    </section>
    """

    # ── Customer pane: phone-shell wrapping the chat widget ───────
    chat_inner = """
        <div class="phone-status">
          <span class="ps-time">9:41</span>
          <span class="ps-right">
            <span class="ps-icon" aria-hidden="true">
              <svg viewBox="0 0 16 10"><path d="M1 9h2V6H1zM5 9h2V4H5zM9 9h2V2H9zM13 9h2V0h-2z"/></svg>
            </span>
            <span class="ps-icon" aria-hidden="true">
              <svg viewBox="0 0 16 12"><path d="M8 12L0 4a11 11 0 0 1 16 0Z" fill="currentColor" stroke="none"/></svg>
            </span>
            <span class="ps-battery"><span class="ps-bat-fill"></span></span>
          </span>
        </div>
        <div class="phone-bar">
          <div class="biz">Septic Pro</div>
          <div class="biz-sub">+1 (844) 940-3274 · Open now</div>
        </div>
        <div class="phone-screen">
          <div class="chat-chips" id="callers"></div>
          <div class="phone-conv" id="conv-body">
            <div class="psys">Pick a caller above to start.</div>
          </div>
          <div class="phone-suggestions" id="suggestions">
            <button class="phone-suggestion" data-msg="My toilets are backing up and there's sewage in the basement!">Emergency</button>
            <button class="phone-suggestion" data-msg="Hey, I need to schedule a routine pumping.">Book a pump-out</button>
            <button class="phone-suggestion" data-msg="How much does a pump-out cost?">Price check</button>
            <button class="phone-suggestion" data-msg="Sorry, wrong number.">Wrong number</button>
          </div>
          <form class="phone-input" id="conv-form" autocomplete="off">
            <input id="conv-input" type="text"
                   placeholder="Type as the customer…"
                   aria-label="Message" disabled />
            <button type="submit" id="conv-send" disabled
                    aria-label="Send">→</button>
          </form>
        </div>
    """
    # V10.3 — owner-SMS preview phone (the third actor in the demo:
    # customer / AI / owner). Pre-seeded with Marcus's emergency brief
    # so the prospect immediately sees what Bob receives. Updates
    # dynamically when the prospect triggers an emergency or booking.
    owner_phone_inner = """
        <div class="phone-bar">
          <div>
            <div class="biz">Bob's phone<span class="biz-badge" id="owner-badge" style="display:none">0</span></div>
            <div class="biz-sub">Texts from your receptionist</div>
          </div>
        </div>
        <div class="phone-screen">
          <div class="owner-conv" id="owner-conv">
            <div class="owner-sms urgent">
              <div class="sms-from">AI Receptionist</div>
              <div>Emergency · Marcus Reilly · 412 Maple Lane, Lancaster · sewage backup · about to bridge.</div>
              <div class="sms-ts">6h ago</div>
              <div class="sms-read shown">
                <svg viewBox="0 0 12 12"><path d="M2 6l2.5 2.5L10 3"/></svg>
                Read
              </div>
            </div>
            <div class="owner-sms">
              <div class="sms-from">AI Receptionist</div>
              <div>Booking · Sarah Wong · Tuesday 1pm pump-out · 412 Oak Street.</div>
              <div class="sms-ts">yesterday</div>
              <div class="sms-read shown">
                <svg viewBox="0 0 12 12"><path d="M2 6l2.5 2.5L10 3"/></svg>
                Read
              </div>
            </div>
          </div>
        </div>
    """
    customer_pane = f"""
    <section class="demo-pane demo-pane-customer">
      <div class="pane-label">What your customer sees</div>
      <div class="phone-shell" style="position:relative;">{chat_inner}</div>
      <div class="pane-label" style="margin-top:24px">What you see on your phone</div>
      <div class="phone-shell owner-shell">{owner_phone_inner}</div>
    </section>
    """

    # ── Chat-widget JS (adapted from the V9.0 inline widget). ──────
    # Caller list renders as chips; rest of the conversation pattern
    # remains. Same backend endpoints (/missed-calls, /chat).
    chat_js = """
    <script>
    const DEMO_CLIENT_ID = "septic_pro";
    /* V10.4 — selected industry from the top-bar switcher. Sent with
       every /chat call so the LLM responds with matching business
       context. "septic" = current behavior (no override).
       Attached to window so the tenant-switcher script (which runs
       in a separate <script> block) can write to it. */
    if (typeof window.currentIndustry === "undefined") {
      window.currentIndustry = "septic";
    }
    if (typeof window.currentOwnerName === "undefined") {
      window.currentOwnerName = "Bob";
    }
    /* V10.1 — sort order now comes from /demo/callers (seeded
       persona order is the canonical demo flow). PREFERRED_ORDER
       retired with /missed-calls as the chat source. */
    let activeCaller = null;
    let callersById = {};
    const $body = document.getElementById("conv-body");
    const $input = document.getElementById("conv-input");
    const $send  = document.getElementById("conv-send");
    const $form  = document.getElementById("conv-form");
    const $chips = document.getElementById("callers");

    function escapeHTML(s){return (s||"").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
    function hashHue(s){
      let h = 0;
      for (let i = 0; i < (s||"").length; i++) h = ((h<<5)-h + s.charCodeAt(i)) | 0;
      return Math.abs(h) % 360;
    }

    /* V10.4 — smooth autoscroll. Animated scroll-to-bottom when new
     * content arrives; instant on first paint to avoid a load-time
     * scroll animation. */
    let _firstPaint = true;
    function _smoothScrollToBottom(){
      const target = $body.scrollHeight;
      if (_firstPaint){
        $body.scrollTop = target;
        _firstPaint = false;
        return;
      }
      try {
        $body.scrollTo({top: target, behavior: "smooth"});
      } catch(_) {
        $body.scrollTop = target;
      }
    }
    function appendMsg(role, text, opts){
      opts = opts || {};
      const div = document.createElement("div");
      const cls = role === "ai" ? "pmsg ai" : (role === "user" ? "pmsg user" : "psys");
      div.className = cls + (opts.loading ? " loading":"");
      div.textContent = text;
      if(opts.meta && opts.meta.length){
        const m = document.createElement("div");
        m.className = "pmeta";
        opts.meta.forEach(t=>{
          const span = document.createElement("span");
          span.className = "tag" + (/emergency|high/i.test(t) ? " emergency":"");
          span.textContent = t;
          m.appendChild(span);
        });
        div.appendChild(m);
      }
      $body.appendChild(div);
      _smoothScrollToBottom();
      return div;
    }
    function appendSystem(t){ appendMsg("sys", t); }

    /* V10.3 — push a new SMS bubble into the owner phone preview
       when the AI flags the call. V10.4 — also increments the iOS-
       style red badge on the owner phone bar so the prospect sees
       the new-notification count. */
    const $ownerConv  = document.getElementById("owner-conv");
    const $ownerBadge = document.getElementById("owner-badge");
    let _ownerBadgeCount = 0;
    function bumpOwnerBadge(){
      if (!$ownerBadge) return;
      _ownerBadgeCount += 1;
      $ownerBadge.textContent = String(_ownerBadgeCount);
      $ownerBadge.style.display = "inline-flex";
    }
    function pushOwnerSMS(caller, data){
      if (!$ownerConv) return;
      const intent   = (data && data.intent) || "";
      const priority = (data && data.priority) || "";
      const isEmerg  = priority === "high" || /emergency/i.test(intent);
      const isBook   = /scheduling|book/i.test(intent);
      let body = "";
      if (isEmerg){
        body = `Emergency · ${caller.name}` +
                (caller.address ? ` · ${caller.address}` : "") +
                ` · about to bridge.`;
      } else if (isBook){
        body = `Booking · ${caller.name}` +
                (caller.address ? ` · ${caller.address}` : "");
      } else {
        return;   /* low-priority chatter doesn't ping the owner */
      }
      const div = document.createElement("div");
      div.className = "owner-sms just-arrived" + (isEmerg ? " urgent" : "");
      div.innerHTML =
        `<div class="sms-from">AI Receptionist</div>` +
        `<div>${escapeHTML(body)}</div>` +
        `<div class="sms-ts">just now</div>` +
        `<div class="sms-read">` +
          `<svg viewBox="0 0 12 12"><path d="M2 6l2.5 2.5L10 3"/></svg>` +
          `Read` +
        `</div>`;
      $ownerConv.insertBefore(div, $ownerConv.firstChild);
      setTimeout(()=>div.classList.remove("just-arrived"), 600);
      bumpOwnerBadge();
      /* V10.5 — subtle owner read-receipt continuity. ~3.4s after
         the SMS lands, mark it as Read. Believable timing offset
         (the owner needed a moment to glance). No flashy reveal —
         the receipt just appears below the bubble. Slightly faster
         for emergencies, a touch slower for bookings. */
      const readDelay = isEmerg ? 2200 + Math.random() * 600
                                 : 3500 + Math.random() * 1200;
      setTimeout(function(){
        const r = div.querySelector(".sms-read");
        if (r) r.classList.add("shown");
      }, readDelay);
    }

    /* V10.4 — end-of-call summary card. Slides into the chat after
       the conversation reaches a closing signal: explicit close
       phrases from the AI ("talk soon", "we'll be in touch") OR
       after 4+ assistant turns. One card per session. */
    let _summaryShown = false;
    const _closeRegex = /\b(talk soon|talk later|we'?ll be in touch|we'?ll call you back|booked|sounds good — we'?re set|goodbye|have a good one)\b/i;
    function maybeShowCallSummary(caller, data){
      if (_summaryShown) return;
      const reply = (data && data.reply) || "";
      const aiTurns = $body.querySelectorAll(".pmsg.ai:not(.typing)").length;
      const closingDetected = _closeRegex.test(reply);
      if (!closingDetected && aiTurns < 4) return;
      _summaryShown = true;
      const sec = _threadStart ? Math.floor((Date.now() - _threadStart) / 1000) : 0;
      const m = String(Math.floor(sec / 60)).padStart(2, "0");
      const s = String(sec % 60).padStart(2, "0");
      const intent = (data && data.intent) || "General";
      const priority = (data && data.priority) || "low";
      const next = (priority === "high" || /emergency/i.test(intent))
        ? "Owner contacted — about to bridge"
        : (/scheduling|book/i.test(intent)
            ? "Booking captured — confirmation pending"
            : "Voicemail logged — follow-up by EOD");
      const card = document.createElement("div");
      card.className = "call-summary-card";
      card.innerHTML =
        `<div class="cs-title">Call summary</div>` +
        `<div class="cs-row"><b>Caller</b><span>${escapeHTML(caller.name || "Unknown")}</span></div>` +
        `<div class="cs-row"><b>Duration</b><span>${m}:${s}</span></div>` +
        `<div class="cs-row"><b>Intent</b><span>${escapeHTML(intent)}</span></div>` +
        `<div class="cs-row"><b>Next action</b><span>${escapeHTML(next)}</span></div>`;
      $body.appendChild(card);
      $body.scrollTop = $body.scrollHeight;
    }

    /* V10.5 — quiet onboarding hint. Pre-V10.5 this was a bobbing
       arrow overlay; that was demo theater. Now it's a static caption
       under the customer pane label, fading away on first chip click. */
    function maybeShowOnboarding(){
      try {
        if (localStorage.getItem("aircept_onboarded")) return;
      } catch (_) { /* private mode etc. — show anyway */ }
      const paneLabel = document.querySelector(
        ".demo-pane-customer .pane-label");
      if (!paneLabel) return;
      const hint = document.createElement("span");
      hint.className = "onboard-hint";
      hint.textContent = "Pick a caller to start";
      paneLabel.appendChild(hint);
      const dismiss = () => {
        hint.classList.add("dismissed");
        setTimeout(()=>hint.remove(), 300);
        try { localStorage.setItem("aircept_onboarded", "1"); } catch(_){}
        $chips.removeEventListener("click", dismiss);
      };
      $chips.addEventListener("click", dismiss);
    }

    /* V10.3 — animated typing-dots bubble while the AI is composing
       a reply. Replaces the static "…" loading placeholder. */
    function appendTyping(){
      const div = document.createElement("div");
      div.className = "pmsg ai typing";
      div.innerHTML = "<span></span><span></span><span></span>";
      $body.appendChild(div);
      $body.scrollTop = $body.scrollHeight;
      return div;
    }

    /* V10.3 — Delivered → Read indicator under the customer's
       outbound bubble. iMessage micro-detail; signals the message
       got through and the (AI's) "eyes" saw it. */
    function appendReceipt(){
      const r = document.createElement("div");
      r.className = "receipt";
      r.textContent = "Delivered";
      $body.appendChild(r);
      requestAnimationFrame(()=>r.classList.add("shown"));
      // After ~200ms, swap to "Read" with the accent color.
      setTimeout(()=>{
        r.textContent = "Read";
        r.classList.add("read");
      }, 220);
      // Once the AI's typing dots appear, the receipt fades back so
      // the cluster doesn't grow unbounded across a long thread.
      setTimeout(()=>{
        r.style.transition = "opacity 600ms ease";
        r.classList.remove("shown");
      }, 1500);
      return r;
    }

    /* V10.1 — /demo/callers is the unified-identity source. Same
       phones as the portal's seeded scenarios → same DiceBear seed →
       same avatar in both panes → chat exchanges land in the SAME
       portal partner card. */
    async function loadCallers(){
      try {
        const r = await fetch("/demo/callers");
        const list = await r.json();
        list.forEach(c => callersById[c.id] = c);
        $chips.innerHTML = list.map(c=>{
          const initial = (c.name||"?").charAt(0).toUpperCase();
          const hue = hashHue(c.phone||c.id);
          /* Same DiceBear seed as the portal-side call_card. */
          const seed = (c.phone||c.id||"").replace(/\\D/g,"") || (c.id||"x");
          const photo = `https://api.dicebear.com/9.x/notionists/svg?seed=${encodeURIComponent(seed)}`;
          return `<a class="chat-chip" data-id="${escapeHTML(c.id)}" href="#" style="--av-h:${hue}">
            <span class="av">
              <span class="av-initial">${escapeHTML(initial)}</span>
              <img class="av-img" src="${photo}" alt="" loading="lazy"
                   onerror="this.style.display='none'">
            </span>
            <span>${escapeHTML(c.name)}</span>
          </a>`;
        }).join("");
        $chips.querySelectorAll(".chat-chip").forEach(el=>{
          el.addEventListener("click", e=>{ e.preventDefault(); selectCaller(el.dataset.id); });
        });
        /* V10.4 — try to restore the prior chat state before
           auto-selecting the first caller. If a recent session was
           interrupted by F5, the prospect picks back up where they
           left off. */
        if (!restoreChatState()){
          const first = list[0];
          if(first) selectCaller(first.id);
        }
      } catch(e){
        $chips.innerHTML = `<div style="padding:10px;color:var(--muted);font-size:12px;">Demo offline. Try again in a moment.</div>`;
      }
    }

    function selectCaller(id){
      activeCaller = id;
      _summaryShown = false;   /* fresh thread, allow summary again */
      _threadStart = Date.now();  /* V10.5 — track start for summary duration */
      document.querySelectorAll(".chat-chip").forEach(el => el.classList.toggle("active", el.dataset.id === id));
      const c = callersById[id];
      $body.innerHTML = "";
      /* V10.5 — populate the chat immediately. The V10.4 sliding
         banner + 700ms ceremony was demo theater; real receptionists
         don't see that. A quiet system-line intro is enough. */
      const intro = c.name ? `${c.name} · ${c.phone}` : `New caller · ${c.phone}`;
      appendSystem(intro);
      if (c.scenario_hint || c.preview) {
        appendSystem(c.scenario_hint || c.preview);
      }
      if(c.type === "return" && c.address){
        appendSystem(`On file: ${c.address}${c.equipment ? " · " + c.equipment : ""}`);
      }
      $input.disabled = false;
      $send.disabled = false;
      $input.focus();
    }
    /* V10.5 — replaces the V10.4 ticking call-timer attention-grab.
       Just record when the thread started so the end-of-call summary
       can show the elapsed time once on close. */
    let _threadStart = 0;

    /* V9.6 — live-refresh the operator portal pane after each chat
       turn so prospects see the message they just typed appear in the
       operator's "Recent activity" feed. Plus a 10s background poll
       for general data movement. */
    const $portal = document.getElementById("portal-body");
    const $pulse  = document.getElementById("live-pulse");
    let _portalRefreshing = false;
    let _portalRefreshScheduled = null;
    /* V10.2 — remember the partner phone whose card should briefly
       flash after the next refresh (the partner the prospect just
       chatted as). Cleared once the flash is applied. */
    let _highlightPartnerDigits = null;

    function _normalizeForHighlight(s){
      const d = (s||"").replace(/\\D/g,"");
      if (d.length === 11 && d.startsWith("1")) return d.slice(1);
      return d;
    }

    async function refreshPortal(){
      if (!$portal || _portalRefreshing) return;
      _portalRefreshing = true;
      try {
        /* Subtle opacity fade so the swap reads as a deliberate
           update, not a flicker. ~140ms total. */
        $portal.style.transition = "opacity 140ms ease";
        $portal.style.opacity = "0.7";
        const r = await fetch("/demo/today", {cache:"no-store"});
        if (r.ok){
          const html = await r.text();
          $portal.innerHTML = html;
          /* Highlight the just-active partner's card (if any). */
          if (_highlightPartnerDigits){
            const sel =
              `details.call[data-partner="${_highlightPartnerDigits}"], ` +
              `.call[data-partner="${_highlightPartnerDigits}"]`;
            const target = $portal.querySelector(sel);
            if (target){
              target.classList.add("just-updated");
              /* Scroll into view if it's not visible. */
              try { target.scrollIntoView({behavior:"smooth", block:"nearest"}); }
              catch(_) { target.scrollIntoView(); }
              setTimeout(()=>target.classList.remove("just-updated"), 1700);
            }
            _highlightPartnerDigits = null;
          }
          if ($pulse){
            $pulse.classList.add("live-pulse-flash");
            setTimeout(()=>$pulse.classList.remove("live-pulse-flash"), 1200);
          }
          _markRefreshed();
        }
        $portal.style.opacity = "1";
      } catch(_) {
        $portal.style.opacity = "1";
        /* silent — next poll will retry */
      }
      finally { _portalRefreshing = false; }
    }
    function scheduleRefresh(delayMs){
      if (_portalRefreshScheduled) clearTimeout(_portalRefreshScheduled);
      _portalRefreshScheduled = setTimeout(refreshPortal, delayMs);
    }
    /* V10.4 — smart polling cadence. After a chat turn we burst-poll
       at 1.5s for ~30s so the prospect sees data update fluidly;
       then settle back to 10s when idle. Pausable via the floating
       demo control. */
    let _pollInterval = null;
    let _pollPaused = false;
    let _fastUntilTs = 0;
    function _pollTick(){
      if (_pollPaused) return;
      refreshPortal();
    }
    function _restartPoll(ms){
      if (_pollInterval) clearInterval(_pollInterval);
      _pollInterval = setInterval(_pollTick, ms);
    }
    function enterFastPoll(){
      _fastUntilTs = Date.now() + 30000;   /* 30s of fast cadence */
      _restartPoll(1500);
      /* Schedule a settle-back when the window expires. */
      setTimeout(()=>{
        if (Date.now() >= _fastUntilTs) _restartPoll(10000);
      }, 30000);
    }
    _restartPoll(10000);   /* baseline idle cadence */

    /* V10.4 — "Updated Xs ago" refresh indicator. Pinned next to the
       operator-pane label. Refreshed every second. */
    const $refreshLabel = document.createElement("span");
    $refreshLabel.className = "refresh-indicator";
    $refreshLabel.textContent = "Updated just now";
    const $opLabel = document.querySelector(".demo-pane-operator .pane-label");
    if ($opLabel) $opLabel.appendChild($refreshLabel);
    let _lastRefreshTs = Date.now();
    function _markRefreshed(){ _lastRefreshTs = Date.now(); }
    function _updateRefreshLabel(){
      const sec = Math.floor((Date.now() - _lastRefreshTs) / 1000);
      if (sec < 5) $refreshLabel.textContent = "Updated just now";
      else if (sec < 60) $refreshLabel.textContent = "Updated " + sec + "s ago";
      else $refreshLabel.textContent = "Updated " + Math.floor(sec/60) + "m ago";
    }
    setInterval(_updateRefreshLabel, 1000);

    async function send(text){
      if(!activeCaller || !text.trim()) return;
      appendMsg("user", text);
      /* V10.3 — receipt under the outbound bubble + animated typing
         dots while the AI composes. */
      appendReceipt();
      $input.value = "";
      $input.disabled = true;
      $send.disabled = true;
      const loading = appendTyping();
      try {
        const r = await fetch("/chat", {
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body: JSON.stringify({
            caller_id: activeCaller,
            message: text,
            client_id: DEMO_CLIENT_ID,
            industry: window.currentIndustry || "septic",
          }),
        });
        if(!r.ok){
          const detail = await r.text();
          loading.remove();
          appendSystem(`Error ${r.status}: ${detail.slice(0,120)}`);
          return;
        }
        const data = await r.json();
        loading.remove();
        const meta = [];
        if(data.intent) meta.push(data.intent);
        if(data.priority === "high") meta.push("priority: high");
        appendMsg("ai", data.reply, {meta});
        /* V10.2 — arm the continuity highlight so the next refresh
           briefly flashes the partner card the prospect just messaged.
           Makes the chat-→-portal cause-and-effect spatially obvious. */
        const c = callersById[activeCaller];
        if (c && c.phone){
          _highlightPartnerDigits = _normalizeForHighlight(c.phone);
        }
        /* V10.3 — if the AI flagged the call as an emergency or a
           booking, fire a new SMS into the owner-phone preview so
           the prospect sees the third-actor loop close in real time. */
        if (c){
          pushOwnerSMS(c, data);
          /* V10.4 — show the end-of-call summary card if the
             conversation has reached a closing signal. */
          maybeShowCallSummary(c, data);
        }
        /* Schedule the portal refresh ~600ms after the reply lands so
           the prospect sees the chat update first, then the portal
           catches up. */
        scheduleRefresh(600);
        /* V10.4 — enter fast-poll mode so subsequent refreshes are
           snappy while there's active conversation. */
        enterFastPoll();
      } catch(e){
        loading.remove();
        appendSystem("Network error.");
      } finally {
        $input.disabled = false;
        $send.disabled = false;
        $input.focus();
      }
    }
    $form.addEventListener("submit", e=>{ e.preventDefault(); send($input.value); });
    document.getElementById("suggestions").addEventListener("click", e=>{
      const btn = e.target.closest(".phone-suggestion");
      if(btn && !$input.disabled){ send(btn.dataset.msg); }
    });
    /* V10.4 — conversation persistence. After every appended bubble
       (user/ai/sys), snapshot the chat body to sessionStorage with
       the active caller. Restored on page load so a refresh mid-demo
       doesn't wipe the prospect's context. */
    const SESSION_KEY = "aircept_chat_state";
    function saveChatState(){
      try {
        sessionStorage.setItem(SESSION_KEY, JSON.stringify({
          caller: activeCaller,
          html: $body.innerHTML,
          industry: window.currentIndustry || "septic",
          ts: Date.now(),
        }));
      } catch(_) {}
    }
    function restoreChatState(){
      try {
        const raw = sessionStorage.getItem(SESSION_KEY);
        if (!raw) return false;
        const state = JSON.parse(raw);
        if (!state || !state.caller || !state.html) return false;
        /* Sessions older than 30 min are stale — drop them. */
        if (Date.now() - (state.ts || 0) > 30 * 60 * 1000) return false;
        if (!callersById[state.caller]) return false;
        activeCaller = state.caller;
        $body.innerHTML = state.html;
        document.querySelectorAll(".chat-chip").forEach(el =>
          el.classList.toggle("active", el.dataset.id === activeCaller));
        if (state.industry) window.currentIndustry = state.industry;
        $input.disabled = false;
        $send.disabled  = false;
        $body.scrollTop = $body.scrollHeight;
        return true;
      } catch(_) { return false; }
    }
    /* Wrap appendMsg + appendSystem so every write hits sessionStorage. */
    const _origAppendMsg = appendMsg;
    appendMsg = function(role, text, opts){
      const r = _origAppendMsg(role, text, opts);
      saveChatState();
      return r;
    };

    /* V10.4 — keyboard-shortcut overlay. `?` opens, `Esc` closes,
       `1`–`7` select corresponding caller chip, `Cmd/Ctrl+K` focuses
       the chat input. Small modal, very lightweight. */
    function _buildShortcutOverlay(){
      const overlay = document.createElement("div");
      overlay.className = "shortcut-overlay";
      overlay.innerHTML =
        '<div class="shortcut-modal" role="dialog" aria-modal="true">' +
        '<h3>Keyboard shortcuts</h3>' +
        '<ul>' +
        '<li><span>Focus chat input</span><span>⌘/Ctrl + K</span></li>' +
        '<li><span>Pick caller 1–7</span><span>1 – 7</span></li>' +
        '<li><span>Send message</span><span>Enter</span></li>' +
        '<li><span>Close this overlay</span><span>Esc</span></li>' +
        '</ul>' +
        '<div class="sm-foot">Press <b>?</b> to reopen this anytime.</div>' +
        '</div>';
      overlay.addEventListener("click", e => {
        if (e.target === overlay) overlay.classList.remove("shown");
      });
      document.body.appendChild(overlay);
      return overlay;
    }
    const $shortcutOverlay = _buildShortcutOverlay();
    document.addEventListener("keydown", function(e){
      if (e.key === "Escape"){
        $shortcutOverlay.classList.remove("shown"); return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k"){
        e.preventDefault();
        if (!$input.disabled) $input.focus();
        return;
      }
      const inField = ["INPUT","TEXTAREA"].indexOf(
        (document.activeElement && document.activeElement.tagName) || "") >= 0;
      if (inField) return;
      if (e.key === "?" || (e.shiftKey && e.key === "/")){
        e.preventDefault();
        $shortcutOverlay.classList.toggle("shown");
        return;
      }
      if (/^[1-7]$/.test(e.key)){
        const chips = document.querySelectorAll(".chat-chip");
        const idx = parseInt(e.key, 10) - 1;
        if (chips[idx]) chips[idx].click();
      }
    });

    /* V10.5 — wire the demo-drawer controls (pause + reset). The
       drawer markup is rendered by demo_page(); JS here just
       attaches the behaviors. */
    function _wireDemoDrawerControls(){
      const $pause = document.getElementById("dd-pause");
      const $reset = document.getElementById("dd-reset");
      if (!$pause || !$reset) return;
      $pause.addEventListener("click", function(){
        _pollPaused = !_pollPaused;
        $pause.classList.toggle("paused", _pollPaused);
        $pause.textContent = _pollPaused ? "Resume refresh" : "Pause refresh";
      });
      $reset.addEventListener("click", async function(){
        $reset.disabled = true;
        $reset.textContent = "Resetting…";
        try {
          await fetch("/demo/reset", {method:"POST"});
          try { sessionStorage.removeItem(SESSION_KEY); } catch(_){}
          $body.innerHTML = '<div class="psys">Pick a caller above to start.</div>';
          _ownerBadgeCount = 0;
          if ($ownerBadge){ $ownerBadge.style.display = "none"; $ownerBadge.textContent = "0"; }
          _summaryShown = false;
          _threadStart = 0;
          refreshPortal();
          $reset.textContent = "Reset demo";
        } catch(_){
          $reset.textContent = "Reset failed";
        } finally {
          $reset.disabled = false;
        }
      });
    }
    _wireDemoDrawerControls();

    loadCallers();
    maybeShowOnboarding();
    </script>
    """

    body = (
        f'<main class="demo-stage">{customer_pane}{operator_pane}</main>'
        f'{chat_js}'
    )
    return HTMLResponse(demo_page(title="AI Receptionist", body=body))


@app.get("/demo/callers")
def demo_callers():
    """V10.1 — the seeded demo personas as a chat-caller list. Same
    phone numbers as the portal's seeded scenarios, so picking
    "Marcus" in the chat lands in the SAME partner card the prospect
    sees in the portal.

    Replaces /missed-calls as the source of truth for the combined
    demo at /. Public; no auth (matches /demo/today)."""
    from src import demo_seed as _demo_seed
    try:
        # Make sure the personas exist in memory.json so a subsequent
        # /chat call resolves the caller_id. Cheap (idempotent).
        _demo_seed.register_personas_in_memory()
    except Exception as e:
        log.warning("demo personas registration in /demo/callers: %s", e)
    return _demo_seed.list_personas()


@app.post("/demo/reset")
def demo_reset():
    """V10.4 — purge accumulated web-chat exchanges from septic_pro
    + reseed. The demo accumulates SMS_<digits> rows on every chat
    turn; over a long demo session this clutters the portal feed.
    The "Reset demo" button in the floating control hits this and
    the operator gets a clean slate.

    Only touches the marketing-demo tenant (septic_pro). Real tenant
    data is never touched."""
    from src import demo_seed as _demo_seed
    try:
        result = _demo_seed.purge_then_seed()
        return {"ok": True, **result}
    except Exception as e:
        log.error("demo reset failed: %s", e)
        raise HTTPException(500, "demo reset failed")


@app.get("/demo/today")
def demo_today_fragment():
    """V9.6 — HTML fragment endpoint for live-refreshing the operator
    portal pane on the combined demo. Returns just the body content
    (no `<html>` chrome) so the JS can drop it into `#portal-body`.

    No auth: matches /. The fragment is the same body the real portal
    renders, just for septic_pro and without the invoice button.

    V9.6.1 — slides the seeded scenarios to "now − minutes_ago" on every
    fetch so the activity feed never reads stale "13 hours ago" labels.
    Cheap: six small UPDATEs against indexed rows. Real chat exchanges
    (call_sid LIKE 'SMS_<digits>') aren't touched."""
    from src.client_portal import _today_body
    from src import demo_seed as _demo_seed
    from fastapi.responses import HTMLResponse
    try:
        _demo_seed.refresh_timestamps()
    except Exception as e:
        log.debug("demo refresh_timestamps non-fatal: %s", e)
    try:
        body = _today_body("septic_pro", t="", include_invoice_link=False)
    except Exception as e:
        log.error("demo/today fragment failed: %s", e)
        body = (
            '<div class="empty empty-warm">'
            '<div class="empty-title">Demo data not ready</div>'
            '<div class="empty-sub">Refresh in a moment.</div></div>'
        )
    return HTMLResponse(body)


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

    # V9.6 — when the web chat targets a marketing-demo tenant, persist
    # each exchange to the SMS-style storage so the operator portal pane
    # in the combined demo surfaces it live.
    caller_phone = (caller.get("phone") or "").strip()
    sid = ""
    if caller_phone and (client.get("id") or "").startswith(("septic_pro",
                                                              "demo_")):
        digits = memory.normalize_phone(caller_phone)
        if digits:
            sid = f"SMS_{digits}"
            try:
                usage.log_sms(sid, client["id"], caller_phone,
                               body.message, direction="inbound")
            except Exception as e:
                log.warning("demo log_sms inbound failed: %s", e)

    # V10.4 — combined-demo industry context. When the tenant switcher
    # is set to HVAC or real-estate, prepend a small context cue to the
    # user message so the LLM responds with the matching business
    # persona. The cue is bracketed so Claude treats it as context, not
    # caller speech (the prompt forbids meta-narration, so it won't
    # echo the brackets). The DB still writes under septic_pro.
    effective_message = body.message
    industry = (body.industry or "").strip().lower()
    if industry == "hvac":
        effective_message = (
            "[Context: you're the receptionist for an HVAC company. "
            "Replace pump-out / septic references with furnace, AC, "
            "ducts, heat pump etc. as appropriate. Do NOT mention "
            "this context in your reply.] "
            + body.message
        )
    elif industry in ("real-estate", "realty", "real_estate"):
        effective_message = (
            "[Context: you're the receptionist for a real-estate "
            "agency. Replace pump-out / septic references with "
            "listings, showings, offers, disclosures. The owner is "
            "the agent. Do NOT mention this context in your reply.] "
            + body.message
        )

    result = _run_pipeline(caller, effective_message, client=client,
                            call_sid=sid)

    # V9.6 — also log the AI reply on the way out so the operator
    # portal preview shows both sides of the exchange.
    if sid and caller_phone:
        try:
            usage.log_sms(sid, client["id"], caller_phone,
                           result.get("reply", ""), direction="outbound")
        except Exception as e:
            log.warning("demo log_sms outbound failed: %s", e)

    return result


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


# V9.0 — public contact form. The landing page replaces the mailto: link
# with a real form so leads land in a file we can act on even if the
# prospect's browser has no mail client configured. Append-only JSONL
# at data/contact_leads.jsonl; operators read it directly.

class ContactIn(BaseModel):
    name: str
    business: str
    phone: str
    email: str | None = None
    note: str | None = None


_CONTACT_LEADS_PATH = ROOT / "data" / "contact_leads.jsonl"
_CONTACT_FIELD_LIMITS = {
    "name": 80, "business": 120, "phone": 40, "email": 120, "note": 600,
}


@app.post("/contact")
def contact(body: ContactIn, request: Request):
    """Public lead-capture endpoint for the landing page form."""
    name = (body.name or "").strip()
    business = (body.business or "").strip()
    phone = (body.phone or "").strip()
    if not name or not business or not phone:
        raise HTTPException(400, "name, business, phone required")
    # Field-length guards — match the form maxlengths so a bad client
    # can't write multi-MB rows.
    payload = {
        "name": name, "business": business, "phone": phone,
        "email": (body.email or "").strip() or None,
        "note": (body.note or "").strip() or None,
    }
    for k, limit in _CONTACT_FIELD_LIMITS.items():
        v = payload.get(k)
        if isinstance(v, str) and len(v) > limit:
            raise HTTPException(400, f"{k} too long (max {limit})")

    record = {
        "ts": int(time.time()),
        "ip": (request.client.host if request.client else None),
        "ua": request.headers.get("user-agent", "")[:200],
        **payload,
    }
    try:
        _CONTACT_LEADS_PATH.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        with _CONTACT_LEADS_PATH.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        # Don't surface filesystem detail to the public, but don't claim
        # success either — return 503 so the form shows the retry hint.
        raise HTTPException(503, "could not record lead") from e

    return {"ok": True}


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

def _greeting_for(client: dict, lang: str,
                  *, caller: dict = None,
                  recall_block: str = None) -> str:
    """V7.3 — time-of-day + recall-aware greeting. Delegates to
    src.greeting.greeting_for so the templates + bucket logic live in
    one place. The function keeps its existing 2-positional signature
    for backwards compatibility (every test that calls
    `_greeting_for(client, lang)` keeps working); new contextual args
    are keyword-only.

    Falls back to the v1 static greeting if the helper module fails to
    import or raises — never blocks the call on a greeting bug.
    """
    try:
        from src import greeting as _greeting
        return _greeting.greeting_for(
            client, lang, caller=caller, recall_block=recall_block)
    except Exception as e:
        log.warning("greeting.greeting_for failed (%s); using fallback", e)
        company = (client or {}).get("name") or "the office"
        if lang == "es":
            return f"Hola, habla Joanna de {company}— en que te puedo ayudar?"
        if lang == "hi":
            return f"Hey, main Joanna, {company} se— kya hua batao?"
        if lang == "gu":
            return f"Hey, hu Joanna, {company} thi— shu thayum kahejo?"
        return f"Hey, this is Joanna from {company}— what's going on?"


# ── V8.9b — endpointing-filler infrastructure ──────────────────────────
#
# When a non-trivial turn arrives, we:
#   1. spawn a background thread that runs _run_pipeline (LLM + filters)
#   2. instantly return TwiML <Play filler.mp3><Redirect /voice/respond>
#   3. /voice/respond pulls the result from the token store (polling
#      briefly if it's not ready yet) and emits the real TwiML
#
# Failure modes are all degraded to the synchronous path or a friendly
# "hang on" filler+retry — never a dropped call.

_THINK_MAX = 1000                # bounded LRU (V5.1 pattern)
_THINK_TTL_SECONDS = 30.0        # tokens older than this are junk
_think_store: dict = {}          # token -> {ready, result, error, ts, ctx}
_think_lock = threading.Lock()


def _think_store_put(*, caller, user_message, client, call_sid,
                     wrap_up_mode, From, lang) -> str:
    """Insert a pending-think record. Returns the freshly-minted
    token (URL-safe). Also opportunistically prunes expired + LRU-evicts
    if the store is over cap."""
    token = secrets.token_urlsafe(16)
    now = time.time()
    record = {
        "ready": False,
        "result": None,
        "error": None,
        "ts": now,
        "ctx": {
            "caller": caller, "user_message": user_message,
            "client": client, "call_sid": call_sid,
            "wrap_up_mode": wrap_up_mode, "From": From, "lang": lang,
        },
    }
    with _think_lock:
        _think_store[token] = record
        # Prune expired
        cutoff = now - _THINK_TTL_SECONDS
        stale = [t for t, v in _think_store.items() if v["ts"] < cutoff]
        for t in stale:
            _think_store.pop(t, None)
        # Bounded eviction — drop oldest if over cap
        if len(_think_store) > _THINK_MAX:
            oldest = sorted(_think_store.items(), key=lambda kv: kv[1]["ts"])
            for t, _ in oldest[: len(_think_store) - _THINK_MAX]:
                _think_store.pop(t, None)
    return token


def _think_store_get(token: str):
    with _think_lock:
        return _think_store.get(token)


def _think_store_pop(token: str):
    with _think_lock:
        return _think_store.pop(token, None)


def _think_worker(token: str) -> None:
    """Runs _run_pipeline in a background thread, stores result in the
    token store. Catches all exceptions so a thread crash never leaves
    a stale unready record (a callsite will see error=… and degrade)."""
    record = _think_store_get(token)
    if record is None:
        return
    ctx = record["ctx"]
    try:
        result = _run_pipeline(
            ctx["caller"], ctx["user_message"],
            client=ctx["client"], call_sid=ctx["call_sid"],
            wrap_up_mode=ctx["wrap_up_mode"],
        )
        with _think_lock:
            r = _think_store.get(token)
            if r is not None:
                r["ready"] = True
                r["result"] = result
    except Exception as e:
        log.error("V8.9b think_worker crashed token=%s: %s: %s",
                  token[:8], type(e).__name__, e)
        with _think_lock:
            r = _think_store.get(token)
            if r is not None:
                r["ready"] = True
                r["error"] = f"{type(e).__name__}: {e}"


def _endpointing_enabled(client: Optional[dict]) -> bool:
    """V8.9b feature flag. Off by default — opt-in per tenant via the
    `endpointing_fillers: true` YAML field. Set to true for ace_hvac
    only at first; widen once the live behavior is validated."""
    if not client:
        return False
    return bool(client.get("endpointing_fillers", False))


def _maybe_filler_for_async(client: Optional[dict],
                              call_sid: str = ""):
    """Return a cached filler payload or None. Falls through to None
    on any condition that would make the async path risky:
      - non-ElevenLabs tenant (no cached audio infra)
      - no cached filler on disk (would force a live render — defeats
        the latency win and adds a network round-trip in the critical
        path)
      - PUBLIC_BASE_URL unset (Twilio can't fetch the audio anyway)

    V10.0 — passes call_sid through so the cached filler picker can
    avoid repeating fillers within a single call.
    """
    try:
        return _audio_cache_module.filler_payload_for(
            client, call_sid=call_sid)
    except Exception as e:
        log.warning("V8.9b filler_payload_for failed: %s", e)
        return None


def _emit_audio(vr, message: str, lang: str, client: dict = None):
    """V8.1 — render `message` and append a top-level <Play>/<Say> to vr,
    WITHOUT wrapping it in a <Gather>. Used by terminal flows where the
    next step is <Dial> or <Hangup> (emergency transfer, force-end,
    capped-call goodbye), so we still get the upgraded ElevenLabs voice
    instead of dropping to Polly mid-call.

    Always falls back to Polly on render failure (same contract as
    _respond's else branch).
    """
    if _humanize.is_enabled(client):
        message = _humanize.humanize_for_speech(message)
    payload = _tts.render(message, client=client, lang=lang)
    if payload.kind == "play" and payload.url:
        vr.play(payload.url)
    else:
        style = _voice_style.style_for(client, mode="main")
        ssml_text = (_voice_style.apply_ssml(payload.text, style=style)
                     if style else payload.text)
        polly_voice = payload.polly_voice or _voice_for(lang, client, mode="main")
        vr.say(ssml_text, voice=polly_voice)


def _respond(vr, message: str, lang: str, client: dict = None):
    """The ONE pattern used everywhere: Say (or Play) inside Gather.
    Caller can interrupt. If they stay silent, Twilio re-fires
    /voice/gather with empty SpeechResult and we handle it there.

    V3.3 — Polly path can wrap in SSML for natural pacing.
    V4.1 — Pluggable TTS. If the tenant opts into ElevenLabs (or
    another non-Polly provider), the response uses <Play> with a cached
    audio URL. On any failure we transparently fall back to Polly so
    the call survives.
    V8.9a — actionOnEmptyResult=true. Twilio's default behavior on an
    empty gather is to "fall through" to the next verb in the TwiML;
    if there isn't one (our case), the call ENDS. That's why some
    callers reported "it just hung up on me" after a long pause or
    inaudible turn. Setting actionOnEmptyResult=true forces Twilio to
    re-fire our action URL with empty SpeechResult so we can re-prompt
    instead of dropping the call. Also bumped timeout 5 → 8 seconds
    for natural pacing.
    """
    g = vr.gather(
        input="speech dtmf",
        action="/voice/gather",
        method="POST",
        speech_timeout="auto",
        speech_model="phone_call",
        enhanced=True,
        language=_stt_lang(lang),
        timeout=8,
        action_on_empty_result=True,
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
def voice_incoming(From: str = Form(default=""), To: str = Form(default=""),
                   CallSid: str = Form(default="")):
    if not TWILIO_AVAILABLE:
        raise HTTPException(500, "twilio package not installed")

    client = tenant.load_client_by_number(To)
    usage.start_call(CallSid, client["id"], From, To)
    call_timer.record_start(CallSid, client["id"])

    # V4.5 — call recording. Best-effort REST API call after start_call.
    if _recordings.is_enabled(client):
        try:
            base = (os.environ.get("PUBLIC_BASE_URL", "") or "").rstrip("/")
            if base and CallSid:
                _recordings.start_recording_via_rest(
                    CallSid, _twilio_client(),
                    callback_url=f"{base}/voice/recording",
                )
        except Exception as e:
            log.error("call recording start failed: %s", e)

    # V3.11 — hard usage cap. If the tenant has set plan.hard_cap_calls
    # and the current month's total has hit it, politely refuse rather
    # than burning more LLM tokens for a client who's beyond their plan.
    cap_status = _usage_cap.is_capped(client)
    if cap_status["capped"]:
        log.info("usage_cap_hit call_sid=%s client=%s current=%d cap=%d",
                 CallSid, client["id"], cap_status["current"], cap_status["cap"])
        vr_cap = VoiceResponse()
        # V8.1 — emit through TTS abstraction so the voice stays
        # consistent even when we're declining the call. Caller hears
        # the same Joanna they would on a normal call.
        _emit_audio(vr_cap, _usage_cap.capped_message(client), "en", client=client)
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
        _emit_audio(vr, "Thanks, we're not taking calls from this number. Goodbye.",
                    "en", client=client)
        vr.hangup()
        usage.end_call(CallSid, outcome="spam_number")
        call_timer.record_end(CallSid)
        return _twiml(str(vr))

    caller = memory.get_or_create_by_phone(From)
    saved_lang = caller.get("language")
    vr = VoiceResponse()

    # Returning caller with known language — skip menu, greet directly.
    # V7.3 — the greeting helper now handles time-of-day variation,
    # named-returning-caller, AND recall-aware ("calling back about
    # yesterday?") all in one place. Best-effort recall lookup: if it
    # fails for any reason, fall through to plain bucket greeting.
    if saved_lang and saved_lang in VOICE_MAP:
        recall_block = None
        try:
            from src import recall as _recall
            recall_block = _recall.build_recall_block(
                client_id=client.get("id", ""),
                from_phone=From,
                exclude_call_sid=CallSid,
            )
        except Exception as e:
            log.debug("recall lookup failed for greeting: %s", e)
        greeting = _greeting_for(
            client, saved_lang,
            caller=caller, recall_block=recall_block,
        )
        _respond(vr, greeting, saved_lang, client=client)
        return _twiml(str(vr))

    # New caller — language selection (only time this ever happens)
    # V4.5 — recording disclosure (legal in 2-party-consent states)
    if _recordings.is_enabled(client):
        vr.say(_recordings.disclosure_text(),
               voice=_voice_for("en", client, mode="transactional"))
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
def voice_setlang(From: str = Form(default=""), Digits: str = Form(default="1"),
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
def voice_gather(From: str = Form(default=""),
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

    # V8.9a — empty speech (Twilio's actionOnEmptyResult=true now fires
    # our webhook even on silence). Track consecutive empties so a
    # disconnected / inaudible caller doesn't loop forever — after
    # EMPTY_RETRY_BUDGET strikes we politely close out instead of
    # cutting them off mid-pause.
    if not SpeechResult.strip():
        retries = call_timer.bump_empty_retry(CallSid)
        if retries > call_timer.EMPTY_RETRY_BUDGET:
            log.info("empty-speech budget exceeded call_sid=%s retries=%d",
                     CallSid, retries)
            owner = client.get("owner_name", "the owner")
            goodbye = (f"Sorry, having trouble hearing you. "
                       f"{owner}'ll give you a call back shortly. Talk soon.")
            _emit_audio(vr, goodbye, lang, client=client)
            vr.hangup()
            usage.end_call(CallSid, outcome="inaudible_timeout")
            call_timer.record_end(CallSid)
            return _twiml(str(vr))
        # Within budget — vary the reprompt a touch so it doesn't loop
        # the exact same phrase
        reprompt = ("Sorry, didn't catch that— what was that?"
                    if retries == 1 else
                    "Still there? Go ahead.")
        _respond(vr, reprompt, lang, client=client)
        return _twiml(str(vr))

    # V8.9a — caller spoke something coherent; reset the empty counter
    # so the NEXT silence cycle starts fresh.
    call_timer.reset_empty_retry(CallSid)

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
        _emit_audio(vr2, "Thanks, we're not interested. Goodbye.",
                    lang, client=client)
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
        _emit_audio(vr, goodbye, lang, client=client)
        vr.hangup()
        usage.end_call(CallSid, outcome="duration_capped")
        call_timer.record_end(CallSid)
        return _twiml(str(vr))

    # V8.9b — endpointing-filler path: if the tenant has it on AND a
    # cached filler is available, fire off the LLM in a background
    # thread and return filler+redirect TwiML instantly. Twilio plays
    # the filler (caller hears "Mhm —" / "Lemme see —" in ~300ms) while
    # /voice/respond will pick up the LLM result and emit the real
    # reply. Falls through to the synchronous path on ANY failure
    # condition (no filler cache hit, feature flag off, missing token
    # infra, etc.) — graceful degradation.
    filler_payload = (_maybe_filler_for_async(client, call_sid=CallSid)
                       if _endpointing_enabled(client) else None)
    if filler_payload is not None:
        token = _think_store_put(
            caller=caller, user_message=SpeechResult, client=client,
            call_sid=CallSid, wrap_up_mode=timer["wrap_up_mode"],
            From=From, lang=lang,
        )
        try:
            threading.Thread(
                target=_think_worker, args=(token,), daemon=True,
                name=f"think-{token[:8]}",
            ).start()
        except RuntimeError as e:
            log.warning("V8.9b thread start failed (%s); falling back to sync", e)
            _think_store_pop(token)
        else:
            log.info("V8.9b filler dispatched call_sid=%s token=%s",
                     CallSid, token[:8])
            vr.play(filler_payload.url)
            vr.redirect(f"/voice/respond?t={token}", method="POST")
            return _twiml(str(vr))

    # ── Synchronous path (current behavior, also fallback for V8.9b) ──
    result = _run_pipeline(caller, SpeechResult, client=client,
                           call_sid=CallSid, wrap_up_mode=timer["wrap_up_mode"])
    _emit_pipeline_result(vr, result,
                          caller=caller, client=client, lang=lang,
                          From=From, CallSid=CallSid,
                          user_message=SpeechResult)
    return _twiml(str(vr))


def _emit_pipeline_result(vr, result: dict, *,
                          caller: dict, client: dict, lang: str,
                          From: str, CallSid: str,
                          user_message: str):
    """V8.9b — post-pipeline emission, shared between the synchronous
    /voice/gather path and the asynchronous /voice/respond path. Runs
    sentiment escalation + emergency routing + final TwiML build.

    Mutates `vr` (the VoiceResponse). Does NOT call _twiml on it; the
    caller controls when to serialize.
    """
    # V3.7 — sentiment tracking. If the caller has been frustrated/angry
    # for N consecutive turns, promote this to a priority=high call so
    # the rest of the handler routes them to escalation_phone.
    sentiment_result = _sentiment.record(CallSid,
                                          result.get("sentiment") or "neutral")
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
            "summary": user_message[:200] if user_message else "",
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
                summary=user_message,
                address=(caller.get("address") or None),
                call_sid=CallSid,
                twilio_client=_twilio_client(),
                twilio_from=os.environ.get("TWILIO_NUMBER"),
            )
        except Exception as e:  # never fail the call for an owner-alert bug
            log.error("owner_notify raised unexpectedly: %s", e)
        # V8.1 — emergency replies now go through the same TTS
        # abstraction as normal replies, so callers don't hear the
        # voice change mid-call when something gets classified as
        # emergency. Build a single combined utterance + render once.
        on_call = client.get("escalation_phone") or os.environ.get("ON_CALL_NUMBER")
        if on_call:
            combined = (result["reply"] + " Hang tight— connecting you now.")
            _emit_audio(vr, combined, lang, client=client)
            vr.dial(on_call)
            usage.end_call(CallSid, outcome="emergency_transfer", emergency=True)
        else:
            combined = (result["reply"]
                        + " Got a tech being paged— they'll call you back in ten.")
            _emit_audio(vr, combined, lang, client=client)
        return

    # Normal reply — one Say inside one Gather. That's it.
    _respond(vr, result["reply"], lang, client=client)


@app.post("/voice/respond")
def voice_respond(t: str = "",
                  From: str = Form(default=""),
                  To: str = Form(default=""),
                  CallSid: str = Form(default=""),
                  Language: str = Form(default="")):
    """V8.9b — endpointing-filler follow-up endpoint. Twilio fetches
    this after playing the filler emitted by /voice/gather. We pull
    the (hopefully ready) LLM result from the token store and emit the
    real reply TwiML. Falls through to graceful degradation on every
    failure mode so a dropped token never drops a call.

    Polling: up to ~2.5s of 50ms checks. In practice the LLM is usually
    done by the time the ~800-1200ms filler audio finishes, so the wait
    is short. If we hit the timeout (LLM genuinely stalled), we emit a
    short "hang on" filler + redirect to ourselves for one more retry.
    """
    if not TWILIO_AVAILABLE:
        raise HTTPException(500, "twilio package not installed")
    if not t:
        # Bogus request — no token. Degrade.
        log.warning("voice_respond called without token")
        vr = VoiceResponse()
        client = tenant.load_client_by_number(To)
        _respond(vr, "Sorry, lost my place — what was that?", "en", client=client)
        return _twiml(str(vr))

    record = _think_store_get(t)
    if record is None:
        # Token expired or never existed (Twilio retry past TTL).
        # Best-effort: pretend nothing happened and re-prompt.
        log.warning("voice_respond token expired/unknown t=%s", t[:8])
        vr = VoiceResponse()
        client = tenant.load_client_by_number(To)
        caller = memory.get_or_create_by_phone(From)
        lang = (Language[:2] if Language else "") or caller.get("language") or DEFAULT_LANG
        _respond(vr, "Sorry, lost my place there — what was that?", lang,
                 client=client)
        return _twiml(str(vr))

    # Poll for readiness — most of the time the LLM finishes during the
    # filler's playback, so this loop usually exits on the first check.
    deadline = time.time() + 2.5
    while not record["ready"] and time.time() < deadline:
        time.sleep(0.05)
        record = _think_store_get(t) or record

    if not record["ready"]:
        # LLM still running. Play a short "hang on" filler + retry.
        # This is the degrade-to-second-filler path; almost never fires
        # because Anthropic Haiku 4.5 finishes well inside 2.5s.
        log.warning("voice_respond LLM still pending t=%s — emitting retry filler",
                    t[:8])
        client = tenant.load_client_by_number(To)
        vr = VoiceResponse()
        _emit_audio(vr, "Hang on one sec —", Language[:2] or "en", client=client)
        vr.redirect(f"/voice/respond?t={t}", method="POST")
        return _twiml(str(vr))

    # Pop on success/error — we've consumed it
    _think_store_pop(t)
    ctx = record["ctx"]

    if record["error"]:
        # Background thread crashed. V6.2 friendly TwiML failure keeps
        # the caller on the line with a coherent message.
        log.error("voice_respond thread error t=%s err=%s", t[:8], record["error"])
        return _voice_failure_twiml()

    # Happy path — emit the real result via the shared helper.
    vr = VoiceResponse()
    _emit_pipeline_result(vr, record["result"],
                          caller=ctx["caller"], client=ctx["client"],
                          lang=ctx["lang"], From=ctx["From"],
                          CallSid=ctx["call_sid"],
                          user_message=ctx["user_message"])
    return _twiml(str(vr))


@app.post("/voice/status")
def voice_status(From: str = Form(default=""),
                 CallStatus: str = Form(default=""),
                 To: str = Form(default=""), CallSid: str = Form(default=""),
                 CallDuration: str = Form(default="0")):
    """Twilio StatusCallback. Records call end in the usage tracker, and for
    missed calls fires a recovery SMS.

    V6.2 — every field defaults to '' so Twilio quirks (missing
    CallStatus on a stale event, retry without From) return 200 with
    action:none rather than 422. The handler short-circuits below when
    CallStatus is empty or unrecognized."""
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


@app.post("/voice/recording")
def voice_recording(CallSid: str = Form(default=""),
                    RecordingSid: str = Form(default=""),
                    RecordingUrl: str = Form(default=""),
                    RecordingDuration: str = Form(default="0"),
                    RecordingStatus: str = Form(default="")):
    """V4.5 — Twilio's recording-status callback. Fires when a call's
    recording completes. We persist the metadata onto the calls row."""
    log.info("recording_callback call_sid=%s rec_sid=%s status=%s duration=%s",
             CallSid, RecordingSid, RecordingStatus, RecordingDuration)
    if RecordingStatus and RecordingStatus != "completed":
        return {"ok": True, "skipped": RecordingStatus}
    try:
        duration = int(RecordingDuration or "0")
    except ValueError:
        duration = 0
    _recordings.store_recording(
        call_sid=CallSid,
        recording_sid=RecordingSid,
        recording_url=RecordingUrl,
        duration_s=duration,
    )
    return {"ok": True}


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
