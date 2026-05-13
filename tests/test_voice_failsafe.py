"""V6.2 — voice-path failsafe error handling.

Twilio plays "We are sorry, an application error has occurred" on any
non-2xx response or invalid body. We never want a real caller to hear
that. These tests guard the conversion: every voice-path failure mode
returns 200 + a polite TwiML <Say>, no matter what blew up underneath.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


_AUTH_TOKEN = "test-failsafe-token"


@pytest.fixture
def failsafe_client(monkeypatch, tmp_path):
    """Like e2e signed_client but every external IO is stubbed."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest_fs")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", _AUTH_TOKEN)
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-mock")
    from src import security
    security.reset_buckets()

    import memory as _memory
    monkeypatch.setattr(_memory, "MEMORY_FILE", tmp_path / "memory_fs.json")
    if hasattr(_memory, "_cache"):
        _memory._cache = None

    import main
    importlib.reload(main)

    from src import owner_notify, webhooks as _webh, recordings as _rec
    monkeypatch.setattr(owner_notify, "notify_emergency",
                        lambda *a, **k: None)
    monkeypatch.setattr(_webh, "fire_safe", lambda *a, **k: None)
    monkeypatch.setattr(_rec, "start_recording_via_rest",
                        lambda *a, **k: None)
    monkeypatch.setattr(main, "_twilio_client", lambda: None)
    return TestClient(main.app, raise_server_exceptions=False)


def _assert_friendly_twiml(r, path):
    """200 + TwiML <Say> — what a caller WANTS to hear when the
    backend trips."""
    assert r.status_code == 200, (
        f"{path} returned {r.status_code} — Twilio plays 'application "
        f"error' on anything other than 200. Body: {r.text[:300]}"
    )
    assert "<Response>" in r.text, f"{path} returned non-TwiML: {r.text[:200]}"
    assert "<Say" in r.text, f"{path} TwiML has no <Say>: {r.text[:200]}"
    assert "<Hangup" in r.text or "Hangup" in r.text, \
        f"{path} doesn't hang up: {r.text[:200]}"


# ── Anthropic API failures → TwiML, not 503 ────────────────────────────

def test_anthropic_auth_error_on_voice_returns_twiml(failsafe_client, monkeypatch):
    import llm
    import anthropic

    def explode(*a, **k):
        # Recreate AuthenticationError. anthropic constructors are
        # picky; build it via a minimal Response-like surrogate.
        try:
            import httpx
            req = httpx.Request("POST", "https://x")
            resp = httpx.Response(401, request=req)
            raise anthropic.AuthenticationError(
                message="bad key", response=resp, body={"x": 1})
        except TypeError:
            # SDK signature differs across versions — fall back to bare
            raise anthropic.AuthenticationError("bad key")
    monkeypatch.setattr(llm, "chat_with_usage", explode)

    r = failsafe_client.post("/voice/gather", data={
        "From": "+14155550199", "To": "+18449403274",
        "CallSid": "CA_fs_auth", "SpeechResult": "hello",
        "Language": "en-US",
    })
    _assert_friendly_twiml(r, "/voice/gather (auth)")


def test_anthropic_api_error_on_voice_returns_twiml(failsafe_client, monkeypatch):
    import llm
    import anthropic

    def explode(*a, **k):
        try:
            import httpx
            req = httpx.Request("POST", "https://x")
            resp = httpx.Response(500, request=req)
            raise anthropic.APIError(
                message="upstream broke", request=req, body={"x": 1})
        except TypeError:
            raise anthropic.APIError("upstream broke")
    monkeypatch.setattr(llm, "chat_with_usage", explode)

    r = failsafe_client.post("/voice/gather", data={
        "From": "+14155550199", "To": "+18449403274",
        "CallSid": "CA_fs_api", "SpeechResult": "hello",
        "Language": "en-US",
    })
    _assert_friendly_twiml(r, "/voice/gather (api)")


def test_typerror_missing_api_key_on_voice_returns_twiml(failsafe_client, monkeypatch):
    """SDK raises TypeError('api_key is not set') when key is missing."""
    import llm

    def explode(*a, **k):
        raise TypeError("api_key client option must be set")
    monkeypatch.setattr(llm, "chat_with_usage", explode)

    r = failsafe_client.post("/voice/gather", data={
        "From": "+14155550199", "To": "+18449403274",
        "CallSid": "CA_fs_key", "SpeechResult": "hello",
        "Language": "en-US",
    })
    _assert_friendly_twiml(r, "/voice/gather (key missing)")


def test_generic_exception_on_voice_returns_twiml(failsafe_client, monkeypatch):
    """Last line of defense: any unhandled exception still degrades
    gracefully on a voice path."""
    import llm
    monkeypatch.setattr(llm, "chat_with_usage",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("boom")))
    r = failsafe_client.post("/voice/gather", data={
        "From": "+14155550199", "To": "+18449403274",
        "CallSid": "CA_fs_generic", "SpeechResult": "hello",
        "Language": "en-US",
    })
    _assert_friendly_twiml(r, "/voice/gather (generic)")


# ── Non-voice paths still surface real errors ──────────────────────────

def test_anthropic_error_on_chat_route_still_5xx(failsafe_client, monkeypatch):
    """Non-voice paths must NOT swallow errors — admin/debug need to
    see the real failure mode. V6.2's friendly-TwiML conversion is
    voice-only."""
    import llm
    import anthropic

    def explode(*a, **k):
        try:
            import httpx
            req = httpx.Request("POST", "https://x")
            resp = httpx.Response(401, request=req)
            raise anthropic.AuthenticationError(
                message="bad key", response=resp, body={"x": 1})
        except TypeError:
            raise anthropic.AuthenticationError("bad key")
    monkeypatch.setattr(llm, "chat_with_usage", explode)

    r = failsafe_client.post("/chat", json={
        "caller_id": "test123", "message": "hi"})
    # JSON 503 (or 500). Just not 200 with TwiML.
    assert r.status_code >= 400
    assert "<Response>" not in r.text


# ── /voice/status field permissiveness ─────────────────────────────────

def test_voice_status_missing_callstatus(failsafe_client):
    """Twilio sometimes posts /voice/status missing fields. Handler
    must return 200 with action:none rather than 422."""
    r = failsafe_client.post("/voice/status", data={
        "From": "+14155550199", "To": "+18449403274",
        "CallSid": "CA_fs_status",
        # No CallStatus, no CallDuration
    })
    assert r.status_code == 200


def test_voice_status_missing_from(failsafe_client):
    r = failsafe_client.post("/voice/status", data={
        "CallStatus": "completed", "CallSid": "CA_fs_status2",
    })
    assert r.status_code == 200


# ── Helper coverage ────────────────────────────────────────────────────

def test_voice_failure_twiml_builder_is_valid_xml():
    """The TwiML our failsafe emits must always parse — otherwise
    Twilio still says 'application error'."""
    import xml.etree.ElementTree as ET
    import main
    r = main._voice_failure_twiml()
    # Response object — body lives on .body
    body = r.body.decode("utf-8") if hasattr(r, "body") else r.text
    ET.fromstring(body)
    assert "<Response>" in body
    assert "<Say" in body
    assert "<Hangup" in body or "Hangup" in body


def test_voice_failure_twiml_custom_message():
    import main
    r = main._voice_failure_twiml("Quick test message.")
    body = r.body.decode("utf-8")
    assert "Quick test message." in body


def test_is_voice_path_helper():
    """_is_voice_path keys the exception handlers' branch logic — must
    match exactly the same prefixes Twilio webhooks use."""
    from fastapi.testclient import TestClient
    import importlib, main as _main
    importlib.reload(_main)
    c = TestClient(_main.app, raise_server_exceptions=False)
    from starlette.requests import Request

    # Build minimal scope to test the helper
    def _make_req(path):
        scope = {"type": "http", "method": "POST", "path": path,
                 "headers": []}
        return Request(scope)

    assert _main._is_voice_path(_make_req("/voice/incoming")) is True
    assert _main._is_voice_path(_make_req("/voice/gather")) is True
    assert _main._is_voice_path(_make_req("/voice/status")) is True
    assert _main._is_voice_path(_make_req("/admin")) is False
    assert _main._is_voice_path(_make_req("/chat")) is False
    assert _main._is_voice_path(_make_req("/voice")) is False  # no trailing slash
