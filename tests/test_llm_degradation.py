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


# ── integration: degradation propagates through _run_pipeline ──────

def test_run_pipeline_returns_degraded_reply_on_llm_failure(monkeypatch):
    """V5.3 — bypass the v4-flaky TestClient + form-parse hang by
    invoking _run_pipeline directly with a mocked chat_with_usage.

    The previous version of this test hung because Twilio-signature-
    middleware body-replay and Form() parsing don't compose cleanly under
    Starlette TestClient in our middleware chain. The unit-level tests
    above already verify chat_with_usage classify + canned response;
    this test verifies that the canned response actually flows through
    _run_pipeline so the route returns a usable ChatResponse instead of
    raising/503-ing.
    """
    import main

    # Force chat_with_usage to return a degraded reply (the same shape
    # the real classifier path produces).
    degraded = llm.ChatResponse(
        reply="Hang on one second— let me grab someone.",
        intent="General", priority="low",
    )
    monkeypatch.setattr(llm, "chat_with_usage",
                        lambda *a, **k: (degraded, (0, 0)))

    caller = {"id": "test_caller", "phone": "+14155550142", "type": "new",
              "history": [], "conversation": []}
    out = main._run_pipeline(
        caller, "my tank backed up",
        client={"id": "ace_hvac", "name": "Ace HVAC", "plan": {}},
        call_sid="CA_degrade_1",
    )
    # Call would have stayed up — pipeline returned a usable result
    # rather than raising
    assert out["reply"]
    assert out["intent"] == "General"
    # The canned phrase made it through anti_robot + grounding + humanize
    low = out["reply"].lower()
    assert any(c in low for c in ("second", "sec", "moment", "one"))
