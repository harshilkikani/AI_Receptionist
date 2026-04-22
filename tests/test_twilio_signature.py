"""P6 — Twilio webhook signature verification."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


_AUTH_TOKEN = "test-auth-token-abc123"


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", _AUTH_TOKEN)
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "true")
    # Don't let the rate limiter bite us during these tests
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


def _sign(url: str, params: dict) -> str:
    from twilio.request_validator import RequestValidator
    return RequestValidator(_AUTH_TOKEN).compute_signature(url, params)


def test_valid_signature_passes(app_client):
    url = "http://testserver/voice/incoming"
    params = {"From": "+14155550142", "To": "+18449403274",
              "CallSid": "CA_sig_valid"}
    sig = _sign(url, params)
    r = app_client.post(
        "/voice/incoming",
        data=params,
        headers={"X-Twilio-Signature": sig},
    )
    # Should reach the handler — 200 with TwiML
    assert r.status_code == 200
    assert "<Response>" in r.text


def test_missing_signature_rejected(app_client):
    params = {"From": "+14155550142", "To": "+18449403274",
              "CallSid": "CA_sig_missing"}
    r = app_client.post("/voice/incoming", data=params)
    assert r.status_code == 403
    assert "invalid_twilio_signature" in r.text


def test_wrong_signature_rejected(app_client):
    params = {"From": "+14155550142", "To": "+18449403274",
              "CallSid": "CA_sig_wrong"}
    r = app_client.post(
        "/voice/incoming",
        data=params,
        headers={"X-Twilio-Signature": "not-a-real-sig"},
    )
    assert r.status_code == 403


def test_shadow_mode_passes_through(monkeypatch):
    """Flag off → invalid signature is logged but request proceeds."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", _AUTH_TOKEN)
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    c = TestClient(main.app)
    params = {"From": "+14155550142", "To": "+18449403274",
              "CallSid": "CA_sig_shadow"}
    r = c.post("/voice/incoming", data=params)
    assert r.status_code == 200


def test_non_voice_paths_unaffected(app_client):
    """Security middleware doesn't block /missed-calls."""
    r = app_client.get("/missed-calls")
    assert r.status_code == 200


def test_form_body_still_parses_downstream(app_client):
    """The middleware re-yields the body; FastAPI Form parsing must work."""
    url = "http://testserver/voice/gather"
    params = {"From": "+14155550142", "To": "+18449403274",
              "SpeechResult": "", "CallSid": "CA_body"}
    sig = _sign(url, params)
    r = app_client.post("/voice/gather", data=params,
                         headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200
    # Empty speech → reprompt TwiML (Say element present with attributes)
    assert "<Say " in r.text


def test_sms_incoming_verified(app_client):
    url = "http://testserver/sms/incoming"
    params = {"From": "+14155550142", "To": "+18449403274", "Body": "hi"}
    sig = _sign(url, params)
    r = app_client.post("/sms/incoming", data=params,
                         headers={"X-Twilio-Signature": sig})
    # Returns TwiML (may be empty if LLM key is placeholder → 503)
    # Either way the signature passed through the middleware.
    assert r.status_code in (200, 503)


def test_missing_auth_token_passes_in_shadow(monkeypatch):
    """No auth token + enforce=true still blocks (validator_unavailable →
    treated as invalid). No token + enforce=false passes."""
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    c = TestClient(main.app)
    r = c.post("/voice/incoming",
               data={"From": "+1", "To": "+1", "CallSid": "x"})
    # Falls through with a warning
    assert r.status_code == 200


def test_public_base_url_override(monkeypatch):
    """When PUBLIC_BASE_URL is set, that's the URL we verify against."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", _AUTH_TOKEN)
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://public.example.com")
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    c = TestClient(main.app)
    public_url = "https://public.example.com/voice/incoming"
    params = {"From": "+1", "To": "+1", "CallSid": "CA_pub"}
    sig = _sign(public_url, params)
    r = c.post("/voice/incoming", data=params,
               headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200
