"""V8.9a — silence-handling tests.

Real bug from a live call: when the caller paused too long, spoke
quietly, or trailed off mid-sentence, Twilio's <Gather> timed out
with no speech, the action URL was never called (default
actionOnEmptyResult=false), and the call dropped because there was
no verb after the Gather in our TwiML. v8.9a sets
actionOnEmptyResult=true and adds a bounded retry counter in
call_timer state.

These tests cover:
  - The retry counter bumps + resets correctly
  - _respond emits actionOnEmptyResult=true on every gather
  - /voice/gather re-prompts on empty within budget
  - /voice/gather politely closes after budget exhausted
  - Coherent speech resets the counter
"""
from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


_AUTH_TOKEN = "test-silence-token"
_TENANT_NUMBER = "+18449403274"
_CALLER = "+15555550199"


@pytest.fixture
def signed_client(monkeypatch, tmp_path):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest_sil")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", _AUTH_TOKEN)
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-mock")
    from src import security
    security.reset_buckets()
    import memory as _memory
    monkeypatch.setattr(_memory, "MEMORY_FILE", tmp_path / "memory_silence.json")
    if hasattr(_memory, "_cache"):
        _memory._cache = None

    import main
    importlib.reload(main)

    # Neutralize all external IO
    from llm import ChatResponse
    import llm
    from src import owner_notify, webhooks as _webhooks, recordings as _rec
    monkeypatch.setattr(llm, "chat_with_usage",
                        lambda *a, **k: (ChatResponse(
                            reply="What's the address?",
                            intent="Scheduling", priority="low"), (10, 5)))
    monkeypatch.setattr(owner_notify, "notify_emergency",
                        lambda *a, **k: None)
    monkeypatch.setattr(_webhooks, "fire_safe", lambda *a, **k: None)
    monkeypatch.setattr(_rec, "start_recording_via_rest",
                        lambda *a, **k: None)
    monkeypatch.setattr(main, "_twilio_client", lambda: None)

    c = TestClient(main.app, raise_server_exceptions=False)
    from twilio.request_validator import RequestValidator
    validator = RequestValidator(_AUTH_TOKEN)
    def sign(path, params):
        return validator.compute_signature(f"http://testserver{path}", params)
    return c, sign


def _post(client, sign, path, params):
    sig = sign(path, params)
    return client.post(path, data=params, headers={"X-Twilio-Signature": sig})


# ── call_timer helpers ─────────────────────────────────────────────────

def test_bump_empty_retry_increments():
    from src import call_timer
    sid = "CA_silence_bump_1"
    call_timer.record_start(sid, "ace_hvac")
    assert call_timer.bump_empty_retry(sid) == 1
    assert call_timer.bump_empty_retry(sid) == 2
    assert call_timer.bump_empty_retry(sid) == 3
    call_timer.record_end(sid)


def test_reset_empty_retry_zeroes():
    from src import call_timer
    sid = "CA_silence_reset_1"
    call_timer.record_start(sid, "ace_hvac")
    call_timer.bump_empty_retry(sid)
    call_timer.bump_empty_retry(sid)
    call_timer.reset_empty_retry(sid)
    assert call_timer.bump_empty_retry(sid) == 1
    call_timer.record_end(sid)


def test_bump_empty_retry_on_unknown_sid_safe():
    from src import call_timer
    # Caller never called record_start — should be a no-op
    assert call_timer.bump_empty_retry("CA_nonexistent_xyz") == 0


def test_reset_empty_retry_on_unknown_sid_safe():
    from src import call_timer
    call_timer.reset_empty_retry("CA_nonexistent_xyz")  # must not raise


# ── _respond emits actionOnEmptyResult=true ────────────────────────────

def test_respond_gather_has_action_on_empty_result(signed_client):
    """Without this flag set, Twilio drops the call on silence —
    this was the bug from the live test report."""
    client, sign = signed_client
    r = _post(client, sign, "/voice/setlang",
              {"From": _CALLER, "To": _TENANT_NUMBER, "Digits": "1"})
    assert r.status_code == 200
    assert ("actionOnEmptyResult=\"true\"" in r.text
            or "action_on_empty_result=\"true\"" in r.text), (
        f"<Gather> missing actionOnEmptyResult=true: {r.text[:400]}")


def test_respond_gather_has_breathing_room_timeout(signed_client):
    """Default Twilio timeout is 5s — too short. V8.9a sets it to 8."""
    client, sign = signed_client
    r = _post(client, sign, "/voice/setlang",
              {"From": _CALLER, "To": _TENANT_NUMBER, "Digits": "1"})
    assert r.status_code == 200
    assert 'timeout="8"' in r.text, (
        f"<Gather> doesn't have timeout=8: {r.text[:400]}")


# ── /voice/gather empty-speech flow ────────────────────────────────────

def test_empty_speech_first_attempt_reprompts(signed_client):
    """1st empty SpeechResult → re-prompt + gather (caller stays on)."""
    client, sign = signed_client
    sid = "CA_silence_first_1"
    # Establish call
    _post(client, sign, "/voice/incoming",
          {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid})
    r = _post(client, sign, "/voice/gather",
              {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
               "SpeechResult": "", "Language": "en-US"})
    assert r.status_code == 200
    assert "<Gather" in r.text, "first empty should re-prompt, not end"
    assert "<Hangup" not in r.text
    # Re-prompt phrasing — first attempt is the "didn't catch that" one
    assert "didn't catch" in r.text.lower() or "didn t catch" in r.text.lower()


def test_empty_speech_second_attempt_still_reprompts(signed_client):
    """Within budget (2), should still re-prompt with varied phrasing."""
    client, sign = signed_client
    sid = "CA_silence_second_1"
    _post(client, sign, "/voice/incoming",
          {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid})
    # 1st empty
    _post(client, sign, "/voice/gather",
          {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
           "SpeechResult": "", "Language": "en-US"})
    # 2nd empty
    r = _post(client, sign, "/voice/gather",
              {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
               "SpeechResult": "", "Language": "en-US"})
    assert r.status_code == 200
    assert "<Gather" in r.text
    assert "<Hangup" not in r.text


def test_empty_speech_over_budget_ends_politely(signed_client):
    """After EMPTY_RETRY_BUDGET, gracefully close — don't loop."""
    client, sign = signed_client
    sid = "CA_silence_overbudget_1"
    _post(client, sign, "/voice/incoming",
          {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid})
    # Burn the budget (default 2)
    _post(client, sign, "/voice/gather",
          {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
           "SpeechResult": "", "Language": "en-US"})
    _post(client, sign, "/voice/gather",
          {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
           "SpeechResult": "", "Language": "en-US"})
    # 3rd empty — over budget → polite end
    r = _post(client, sign, "/voice/gather",
              {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
               "SpeechResult": "", "Language": "en-US"})
    assert r.status_code == 200
    assert "<Hangup" in r.text, f"should hang up after budget: {r.text[:400]}"
    # Should mention the caller's owner-name fallback
    body_lower = r.text.lower()
    assert ("call back" in body_lower or "callback" in body_lower
            or "give you a call" in body_lower)


def test_coherent_speech_resets_counter(signed_client):
    """Caller speaks something coherent after one empty → next empty
    should be treated as 'first', not 'over budget'."""
    client, sign = signed_client
    sid = "CA_silence_reset_1"
    _post(client, sign, "/voice/incoming",
          {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid})
    # 1st empty
    _post(client, sign, "/voice/gather",
          {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
           "SpeechResult": "", "Language": "en-US"})
    # 2nd empty
    _post(client, sign, "/voice/gather",
          {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
           "SpeechResult": "", "Language": "en-US"})
    # Coherent speech — should reset
    r = _post(client, sign, "/voice/gather",
              {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
               "SpeechResult": "my ac is making a weird noise",
               "Language": "en-US"})
    assert r.status_code == 200
    assert "<Gather" in r.text  # back to normal flow
    # Now another empty — should re-prompt, NOT hang up
    r2 = _post(client, sign, "/voice/gather",
               {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
                "SpeechResult": "", "Language": "en-US"})
    assert "<Gather" in r2.text, "counter wasn't reset by coherent speech"
    assert "<Hangup" not in r2.text


def test_empty_speech_then_real_then_empty_is_fresh_cycle(signed_client):
    """Real → empty → empty should re-prompt twice, not hang up at
    retry #2 (because the real speech in the middle reset the counter)."""
    client, sign = signed_client
    sid = "CA_silence_freshcycle_1"
    _post(client, sign, "/voice/incoming",
          {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid})
    _post(client, sign, "/voice/gather",
          {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
           "SpeechResult": "what are your hours", "Language": "en-US"})
    # Then two consecutive empties — should NOT exceed budget
    r1 = _post(client, sign, "/voice/gather",
               {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
                "SpeechResult": "", "Language": "en-US"})
    r2 = _post(client, sign, "/voice/gather",
               {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": sid,
                "SpeechResult": "", "Language": "en-US"})
    assert "<Hangup" not in r1.text
    assert "<Hangup" not in r2.text
