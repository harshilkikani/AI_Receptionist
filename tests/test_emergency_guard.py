"""V8.3 — emergency-keyword guard tests.

The new prompt tells Claude to only mark `priority=high` when the
caller mentions a tenant-configured emergency keyword. Claude still
over-classifies sometimes (a real example from live testing: "AC
stopped working last night and the house is getting hot" got marked
Emergency). v8.3 adds a deterministic guard in main._run_pipeline:
if the LLM says high but no keyword from the tenant's
`emergency_keywords` list appears in the caller's speech, downgrade
to low. This eliminates spurious emergency routing without crippling
the legitimate cases.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import llm
import main
from llm import ChatResponse


_KEYWORDS = ["burst", "flood", "flooding", "leak", "leaking",
             "gas", "no heat", "fire", "smoke", "carbon monoxide"]


def _client_with_keywords():
    return {
        "id": "ace_hvac",
        "name": "Ace HVAC",
        "owner_name": "the owner",
        "emergency_keywords": _KEYWORDS,
        "plan": {},
    }


def _new_caller():
    return {"id": "c1", "phone": "+15555550199", "type": "new",
            "history": [], "conversation": []}


def _fake_llm(reply: str, *, priority: str = "low",
              intent: str = "General"):
    return lambda *a, **k: (ChatResponse(
        reply=reply, intent=intent, priority=priority,
    ), (10, 5))


def test_emergency_guard_downgrades_when_keyword_absent(monkeypatch):
    """LLM marked high but caller didn't say a real emergency word.
    Guard fires → priority becomes low."""
    monkeypatch.setattr(llm, "chat_with_usage", _fake_llm(
        "Okay, getting a tech out there.", priority="high",
        intent="Emergency"))
    out = main._run_pipeline(
        _new_caller(),
        "my ac stopped working last night and the house is getting hot",
        client=_client_with_keywords(),
        call_sid="CA_guard_1",
    )
    assert out["priority"] == "low"


def test_emergency_guard_keeps_high_when_keyword_present(monkeypatch):
    """Legitimate emergency — caller said 'gas' which IS in the
    tenant's keyword list. Guard must NOT fire."""
    monkeypatch.setattr(llm, "chat_with_usage", _fake_llm(
        "Okay — getting a tech out there now. Address?",
        priority="high", intent="Emergency"))
    out = main._run_pipeline(
        _new_caller(),
        "I smell gas in the basement and I'm scared",
        client=_client_with_keywords(),
        call_sid="CA_guard_2",
    )
    assert out["priority"] == "high"


def test_emergency_guard_ignores_low_priority(monkeypatch):
    """If LLM said low, the guard doesn't activate — there's nothing
    to downgrade."""
    monkeypatch.setattr(llm, "chat_with_usage", _fake_llm(
        "Sure, what's the address?", priority="low",
        intent="Scheduling"))
    out = main._run_pipeline(
        _new_caller(),
        "my AC quit",   # no emergency keyword either, but priority=low so guard skips
        client=_client_with_keywords(),
        call_sid="CA_guard_3",
    )
    assert out["priority"] == "low"


def test_emergency_guard_matches_multiword_keywords(monkeypatch):
    """`no heat` is a two-word keyword. Substring match should fire."""
    monkeypatch.setattr(llm, "chat_with_usage", _fake_llm(
        "Okay, getting someone out.", priority="high",
        intent="Emergency"))
    out = main._run_pipeline(
        _new_caller(),
        "we have no heat and it's freezing in here",
        client=_client_with_keywords(),
        call_sid="CA_guard_4",
    )
    assert out["priority"] == "high"


def test_emergency_guard_handles_partial_word_matches(monkeypatch):
    """`leak` should match `leaking` since the keyword list explicitly
    lists `leak` as the substring. Substring-match works both ways."""
    monkeypatch.setattr(llm, "chat_with_usage", _fake_llm(
        "Okay, on it.", priority="high", intent="Emergency"))
    out = main._run_pipeline(
        _new_caller(),
        "water is leaking from the ceiling",
        client=_client_with_keywords(),
        call_sid="CA_guard_5",
    )
    assert out["priority"] == "high"


def test_emergency_guard_case_insensitive(monkeypatch):
    """Caller speech / keywords compared lowercase. Mixed-case speech
    shouldn't slip through."""
    monkeypatch.setattr(llm, "chat_with_usage", _fake_llm(
        "Okay.", priority="high", intent="Emergency"))
    out = main._run_pipeline(
        _new_caller(),
        "OH MY GOD THE BASEMENT IS FLOODING",
        client=_client_with_keywords(),
        call_sid="CA_guard_6",
    )
    assert out["priority"] == "high"


def test_emergency_guard_no_keywords_configured(monkeypatch):
    """Tenant with empty emergency_keywords list — guard can't validate
    so it leaves priority alone (trust the LLM, since there's no rule
    to apply)."""
    monkeypatch.setattr(llm, "chat_with_usage", _fake_llm(
        "Okay.", priority="high", intent="Emergency"))
    client = _client_with_keywords()
    client["emergency_keywords"] = []
    out = main._run_pipeline(
        _new_caller(),
        "AC broken",
        client=client,
        call_sid="CA_guard_7",
    )
    assert out["priority"] == "high"
