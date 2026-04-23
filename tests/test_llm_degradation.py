"""V3.1 — graceful LLM degradation tests.

When Anthropic fails (rate limit, timeout, auth, generic API error), the
receptionist must stay on the line with a canned reply instead of 503-ing
the Twilio webhook.
"""
from __future__ import annotations

from unittest.mock import patch

import anthropic
import pytest

import llm


@pytest.fixture(autouse=True)
def _reset_stats():
    llm.reset_degradation_stats()
    yield
    llm.reset_degradation_stats()


def _mock_parse_to_raise(exc):
    """Patch _anthropic.beta.messages.parse to raise `exc`."""
    return patch.object(
        llm._anthropic.beta.messages, "parse",
        side_effect=exc,
    )


# ── classify helper ────────────────────────────────────────────────────

def test_classify_rate_limit():
    class _RL(anthropic.RateLimitError):
        def __init__(self):  # pragma: no cover
            pass
    # We can't easily instantiate without a response object; classify
    # handles both instance-based AND name-based matching.
    class FakeRL(Exception):
        pass
    FakeRL.__name__ = "RateLimitError"
    assert llm._classify_anthropic_error(FakeRL()) == "rate_limit"


def test_classify_timeout():
    class FakeTO(Exception):
        pass
    FakeTO.__name__ = "APITimeoutError"
    assert llm._classify_anthropic_error(FakeTO()) == "timeout"


def test_classify_auth_from_typeerror():
    assert llm._classify_anthropic_error(TypeError("api_key not found")) == "auth"
    assert llm._classify_anthropic_error(TypeError("missing auth_token")) == "auth"


def test_classify_typeerror_non_auth_is_unknown():
    assert llm._classify_anthropic_error(TypeError("int + str")) == "unknown"


def test_classify_unknown_exception():
    assert llm._classify_anthropic_error(ValueError("whatever")) == "unknown"


# ── chat_with_usage degrades ──────────────────────────────────────────

def test_chat_degrades_on_rate_limit():
    class FakeRateLimit(Exception): pass
    FakeRateLimit.__name__ = "RateLimitError"

    with _mock_parse_to_raise(FakeRateLimit()):
        reply, usage = llm.chat_with_usage(
            None, "hi", conversation=[], client=None,
        )
    assert reply.intent == "General"
    assert reply.priority == "low"
    # Canned reply should signal delay without being an LLM answer
    delay_words = ("sec", "one", "hang", "moment", "beat", "right", "hold",
                   "bear", "quick")
    low = reply.reply.lower()
    assert any(w in low for w in delay_words), f"no delay cue: {reply.reply!r}"
    assert usage == (0, 0)
    stats = llm.degradation_stats()
    assert stats["total"] == 1
    assert stats["by_reason"].get("rate_limit") == 1


def test_chat_degrades_on_timeout():
    class FakeTimeout(Exception): pass
    FakeTimeout.__name__ = "APITimeoutError"
    with _mock_parse_to_raise(FakeTimeout()):
        reply, _ = llm.chat_with_usage(None, "hi", [], None)
    assert reply.intent == "General"
    assert llm.degradation_stats()["by_reason"].get("timeout") == 1


def test_chat_degrades_on_auth_typeerror():
    with _mock_parse_to_raise(TypeError("api_key is required")):
        reply, _ = llm.chat_with_usage(None, "hi", [], None)
    assert reply.intent == "General"
    assert llm.degradation_stats()["by_reason"].get("auth") == 1


def test_chat_degrades_on_generic_exception():
    with _mock_parse_to_raise(RuntimeError("something else")):
        reply, _ = llm.chat_with_usage(None, "hi", [], None)
    assert llm.degradation_stats()["by_reason"].get("unknown") == 1


def test_recover_degrades_gracefully():
    class FakeRateLimit(Exception): pass
    FakeRateLimit.__name__ = "RateLimitError"
    with _mock_parse_to_raise(FakeRateLimit()):
        reply = llm.recover(None)
    # Returns a ChatResponse, never raises
    assert hasattr(reply, "reply")
    assert hasattr(reply, "intent")


# ── stats getters ──────────────────────────────────────────────────────

def test_stats_reset():
    llm._degraded_response("rate_limit")
    assert llm.degradation_stats()["total"] == 1
    llm.reset_degradation_stats()
    assert llm.degradation_stats()["total"] == 0


def test_stats_tracks_last_reason():
    llm._degraded_response("timeout")
    s = llm.degradation_stats()
    assert s["last_reason"] == "timeout"
    assert s["last_ts"] is not None


def test_degraded_response_varies():
    """With multiple phrases per reason, repeated calls pull from the list."""
    seen = set()
    for _ in range(20):
        r = llm._degraded_response("rate_limit")
        seen.add(r.reply)
    assert len(seen) >= 1
    # Every rate_limit phrase should signal delay somehow
    delay_cues = ("sec", "beat", "second", "moment", "bear", "calls")
    for s in seen:
        assert any(c in s.lower() for c in delay_cues), f"no delay cue: {s!r}"


# ── integration: voice webhook doesn't crash on LLM failure ────────

def test_voice_gather_survives_llm_failure(monkeypatch):
    import importlib
    from fastapi.testclient import TestClient

    class FakeRL(Exception): pass
    FakeRL.__name__ = "RateLimitError"

    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    c = TestClient(main.app)

    with _mock_parse_to_raise(FakeRL()):
        r = c.post(
            "/voice/gather",
            data={"From": "+14155550142", "To": "+18449403274",
                  "CallSid": "CA_degrade_1", "SpeechResult": "my tank backed up"},
        )
    # Call stays up — 200 TwiML, not 503
    assert r.status_code == 200
    assert "<Response>" in r.text
    assert "<Say " in r.text
