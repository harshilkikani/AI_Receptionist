"""V6.1 — End-to-end call-flow smoke test.

Every other test in this repo is a unit. Until now, the only thing
proving the live phone path worked was actually picking up the phone.
After 5 versions and 9 audit passes, the demo went dark and no test
fired.

This file simulates a Twilio inbound call: same form fields, valid
signature, full handler chain (RequestID → SecurityHeaders →
AdminRateLimit → TwilioSig → tenant routing → spam → call_timer → LLM
→ anti_robot → grounding → humanize → tts). LLM is mocked so the test
runs offline in <1s; everything else is real.

Asserts on every step:
  - HTTP 200 (NEVER 4xx/5xx — Twilio plays "application error" on 5xx)
  - Body parses as XML (TwiML is XML; an invalid response is a
    silent voice failure)
  - <Response> root element present
  - Latency under 2 seconds (Twilio webhook timeout is 15s, but we
    want a real budget)

If this test ever fails, the live demo is broken. Period.
"""
from __future__ import annotations

import importlib
import time
import xml.etree.ElementTree as ET
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


_AUTH_TOKEN = "test-auth-token-e2e"
_TENANT_NUMBER = "+18449403274"     # ace_hvac
_CALLER = "+14155550199"


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def signed_client(monkeypatch, tmp_path):
    """A TestClient that auto-signs every voice POST with the Twilio
    validator so the signature middleware doesn't reject it.

    Also mocks every outbound network IO (Anthropic, Twilio REST,
    webhooks) so the suite stays offline and never hangs on a real-API
    timeout. The order matters: reload main FIRST so the app picks up
    the test env, then patch module-level symbols. Patching before the
    reload would be undone by the reload.

    Returns (client, sign_fn).
    """
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest_e2e")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", _AUTH_TOKEN)
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-mock")
    from src import security
    security.reset_buckets()

    # Isolated memory store
    import memory as _memory
    monkeypatch.setattr(_memory, "MEMORY_FILE", tmp_path / "memory_e2e.json")
    if hasattr(_memory, "_cache"):
        _memory._cache = None

    # Reload main with test env in place
    import main
    importlib.reload(main)

    # NOW patch the module-level external-IO symbols. Patching before
    # the reload was being silently undone.
    from llm import ChatResponse
    import llm

    def fake_chat(*args, **kwargs):
        return (ChatResponse(
            reply="Got it — let me check on that.",
            intent="General",
            priority="low",
        ), (40, 12))
    monkeypatch.setattr(llm, "chat_with_usage", fake_chat)

    from src import owner_notify, webhooks as _webhooks_mod, recordings as _rec
    monkeypatch.setattr(owner_notify, "notify_emergency",
                        lambda *a, **k: {"sent": False, "reason": "mocked"})
    monkeypatch.setattr(_webhooks_mod, "fire_safe", lambda *a, **k: None)
    monkeypatch.setattr(_rec, "start_recording_via_rest",
                        lambda *a, **k: None)
    # _twilio_client() constructs a real TwilioClient — stub so anything
    # that grabs it (owner_notify, feedback SMS, recording start)
    # cannot reach Twilio's API.
    monkeypatch.setattr(main, "_twilio_client", lambda: None)

    # `raise_server_exceptions=False` matches production behavior — our
    # @app.exception_handler(Exception) converts crashes to TwiML 200,
    # but Starlette TestClient re-raises by default which would mask
    # the conversion. Production uvicorn does NOT re-raise.
    c = TestClient(main.app, raise_server_exceptions=False)

    from twilio.request_validator import RequestValidator
    validator = RequestValidator(_AUTH_TOKEN)

    def sign(path: str, params: dict) -> str:
        url = f"http://testserver{path}"
        return validator.compute_signature(url, params)

    return c, sign


# ── Helpers ─────────────────────────────────────────────────────────────

def _post_voice(client, sign, path: str, params: dict):
    """Post to a voice webhook with valid Twilio signature + timing."""
    sig = sign(path, params)
    t0 = time.perf_counter()
    r = client.post(path, data=params, headers={"X-Twilio-Signature": sig})
    elapsed = time.perf_counter() - t0
    return r, elapsed


def _assert_twiml_ok(r, elapsed, path):
    """Twilio expects 200 with valid TwiML in <15s. We require <2s."""
    assert r.status_code == 200, (
        f"{path} returned {r.status_code} — Twilio plays "
        f'"application error" on anything non-2xx. Body: {r.text[:300]}'
    )
    assert "<Response>" in r.text, f"{path} returned non-TwiML: {r.text[:300]}"
    # Strict XML parse — catches truncation, mismatched tags, etc.
    try:
        ET.fromstring(r.text)
    except ET.ParseError as e:
        pytest.fail(f"{path} returned malformed TwiML: {e}\nBody: {r.text[:500]}")
    assert elapsed < 2.0, (
        f"{path} took {elapsed:.2f}s — Twilio webhook timeout is 15s "
        f"and slow responses kill caller experience"
    )


# ── The smoke tests ─────────────────────────────────────────────────────

def test_new_caller_full_flow(signed_client):
    """Brand-new caller dials in:
      1. /voice/incoming   → language-selection menu
      2. /voice/setlang    → greeting in chosen language
      3. /voice/gather × 3 → LLM responses
      4. /voice/status     → call lifecycle close
    Every step must be 200 + valid TwiML in <2s.
    """
    client, sign = signed_client
    call_sid = "CA_e2e_newcaller_001"

    # 1. Incoming
    r, t = _post_voice(client, sign, "/voice/incoming", {
        "From": _CALLER, "To": _TENANT_NUMBER, "CallSid": call_sid,
    })
    _assert_twiml_ok(r, t, "/voice/incoming")
    # New caller → should offer language menu via DTMF gather
    assert "/voice/setlang" in r.text or "press 1" in r.text.lower()

    # 2. Language selection (English = 1)
    r, t = _post_voice(client, sign, "/voice/setlang", {
        "From": _CALLER, "To": _TENANT_NUMBER, "Digits": "1",
    })
    _assert_twiml_ok(r, t, "/voice/setlang")
    # Greeting should reach the gather phase
    assert "/voice/gather" in r.text.lower() or "<Gather" in r.text

    # 3a. First real turn — caller describes problem
    r, t = _post_voice(client, sign, "/voice/gather", {
        "From": _CALLER, "To": _TENANT_NUMBER, "CallSid": call_sid,
        "SpeechResult": "my AC stopped working last night",
        "Language": "en-US",
    })
    _assert_twiml_ok(r, t, "/voice/gather#1")

    # 3b. Second turn — caller gives address
    r, t = _post_voice(client, sign, "/voice/gather", {
        "From": _CALLER, "To": _TENANT_NUMBER, "CallSid": call_sid,
        "SpeechResult": "address is 12 main street, apartment 4",
        "Language": "en-US",
    })
    _assert_twiml_ok(r, t, "/voice/gather#2")

    # 3c. Third turn — caller confirms callback
    r, t = _post_voice(client, sign, "/voice/gather", {
        "From": _CALLER, "To": _TENANT_NUMBER, "CallSid": call_sid,
        "SpeechResult": "yeah callback works, thanks",
        "Language": "en-US",
    })
    _assert_twiml_ok(r, t, "/voice/gather#3")

    # 4. Status callback at call end
    r, t = _post_voice(client, sign, "/voice/status", {
        "From": _CALLER, "To": _TENANT_NUMBER, "CallSid": call_sid,
        "CallStatus": "completed", "CallDuration": "97",
    })
    # /voice/status returns JSON, not TwiML — separate assertion
    assert r.status_code == 200, f"/voice/status returned {r.status_code}"


def test_returning_caller_skips_menu(signed_client):
    """A caller whose phone is already on file → no DTMF language
    menu, greeting goes straight to gather."""
    client, sign = signed_client

    # Seed memory with a returning caller (same phone). `memory` is the
    # top-level module, not under src/.
    import memory
    caller = memory.get_or_create_by_phone(_CALLER)
    memory.update_caller(caller["id"], language="en")

    r, t = _post_voice(client, sign, "/voice/incoming", {
        "From": _CALLER, "To": _TENANT_NUMBER,
        "CallSid": "CA_e2e_returning_001",
    })
    _assert_twiml_ok(r, t, "/voice/incoming (returning)")
    # No DTMF prompt — direct gather for speech
    assert "press 1" not in r.text.lower()


def test_empty_speech_reprompts(signed_client):
    """Caller didn't speak → we re-prompt instead of crashing."""
    client, sign = signed_client
    r, t = _post_voice(client, sign, "/voice/gather", {
        "From": _CALLER, "To": _TENANT_NUMBER,
        "CallSid": "CA_e2e_silent_001",
        "SpeechResult": "",      # empty
        "Language": "en-US",
    })
    _assert_twiml_ok(r, t, "/voice/gather (empty speech)")
    # Should NOT immediately hang up
    assert "<Hangup" not in r.text


def test_emergency_keyword_marks_priority_high(signed_client, monkeypatch):
    """LLM returns priority='high'. The handler must still produce
    valid TwiML — no 5xx — even when the emergency-transfer code path
    activates. signed_client already mocked the LLM; we override
    AFTER it runs so the high-priority response is what fires."""
    from llm import ChatResponse
    import llm

    def fake_high(*a, **k):
        return (ChatResponse(
            reply="I'm getting you connected to our on-call tech right now.",
            intent="Emergency",
            priority="high",
        ), (40, 18))
    monkeypatch.setattr(llm, "chat_with_usage", fake_high)

    client, sign = signed_client
    r, t = _post_voice(client, sign, "/voice/gather", {
        "From": _CALLER, "To": _TENANT_NUMBER,
        "CallSid": "CA_e2e_emergency_001",
        "SpeechResult": "my basement is flooding right now",
        "Language": "en-US",
    })
    _assert_twiml_ok(r, t, "/voice/gather (emergency)")


def test_no_5xx_under_llm_failure(signed_client, monkeypatch):
    """If chat_with_usage crashes hard, the handler MUST still return
    200 + TwiML, not 5xx. This is what makes the difference between
    a glitchy call and 'application error' in the caller's ear."""
    import llm

    def crashing(*a, **k):
        raise RuntimeError("anthropic exploded")
    monkeypatch.setattr(llm, "chat_with_usage", crashing)

    client, sign = signed_client
    r, t = _post_voice(client, sign, "/voice/gather", {
        "From": _CALLER, "To": _TENANT_NUMBER,
        "CallSid": "CA_e2e_llmfail_001",
        "SpeechResult": "are you guys still open today",
        "Language": "en-US",
    })
    # The whole point of V6.2 is this must be 200 + TwiML even on
    # backend failure. V6.1 documents the expectation; V6.2 makes it
    # pass if it doesn't already.
    _assert_twiml_ok(r, t, "/voice/gather (LLM crashed)")


def test_signature_rejection_returns_403_not_500(signed_client):
    """The signature middleware must NEVER bubble an exception into a
    5xx — at worst it rejects with 403."""
    client, _ = signed_client
    r = client.post("/voice/incoming", data={
        "From": _CALLER, "To": _TENANT_NUMBER,
        "CallSid": "CA_e2e_unsigned",
    })   # no signature header
    assert r.status_code == 403
    assert r.status_code < 500


def test_every_voice_route_under_2s(signed_client):
    """Latency budget guard. All 4 voice POST routes must round-trip in
    under 2s with a mocked LLM."""
    client, sign = signed_client
    call_sid = "CA_e2e_latency_001"

    paths_and_params = [
        ("/voice/incoming",
         {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": call_sid}),
        ("/voice/setlang",
         {"From": _CALLER, "To": _TENANT_NUMBER, "Digits": "1"}),
        ("/voice/gather",
         {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": call_sid,
          "SpeechResult": "hi how much for a service call",
          "Language": "en-US"}),
        ("/voice/status",
         {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": call_sid,
          "CallStatus": "completed", "CallDuration": "60"}),
    ]
    for path, params in paths_and_params:
        r, t = _post_voice(client, sign, path, params)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        assert t < 2.0, f"{path} took {t:.3f}s — over the 2s budget"


def test_status_callback_no_5xx_on_missing_fields(signed_client):
    """Twilio sometimes posts /voice/status with surprising omissions.
    The handler must NEVER 5xx — at worst respond with action: none."""
    client, sign = signed_client
    r, t = _post_voice(client, sign, "/voice/status", {
        "From": _CALLER, "To": _TENANT_NUMBER,
        "CallSid": "CA_e2e_status_001",
        # No CallStatus, no CallDuration
    })
    assert r.status_code == 200


def test_no_python_traceback_in_responses(signed_client, monkeypatch):
    """If FastAPI returns the default error page, the body would
    contain 'Traceback'. That should NEVER happen on /voice/* routes."""
    client, sign = signed_client
    # Try to induce an error: malformed To field
    r, t = _post_voice(client, sign, "/voice/incoming", {
        "From": _CALLER, "To": "not-a-phone-number", "CallSid": "CA_traceback",
    })
    # Either we degrade gracefully (200) or we 4xx — but never expose
    # a traceback.
    assert "Traceback" not in r.text
    assert "raise " not in r.text
    if r.status_code >= 500:
        pytest.fail(f"5xx response from /voice/incoming on malformed To: "
                    f"{r.status_code} body: {r.text[:300]}")
