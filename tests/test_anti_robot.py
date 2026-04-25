"""V4.3 — anti-robot scrubber tests."""
from __future__ import annotations

import pytest

from src import anti_robot as ar


# ── opener strips ────────────────────────────────────────────────────

def test_strip_so_youre_asking():
    out, fired = ar.scrub("So you're asking about pricing. Pump-out is $475.")
    assert "So you're asking" not in out
    assert "Pump-out is $475" in out
    assert fired


def test_strip_i_understand_your_concern():
    out, fired = ar.scrub("I understand your concern about the timing. We'll be there Tuesday.")
    assert "I understand" not in out
    assert "Tuesday" in out
    assert fired


def test_strip_thank_you_for_calling():
    out, _ = ar.scrub("Thank you for calling Septic Pro. What can I do?")
    assert "Thank you for calling" not in out
    assert "What can I do" in out


def test_strip_id_be_happy_to_help():
    out, _ = ar.scrub("I'd be happy to help with that. Tell me your address.")
    assert "I'd be happy" not in out
    assert "Tell me your address" in out


def test_strip_let_me_help_you_with_that():
    out, _ = ar.scrub("Let me help you with that. What's your phone number?")
    assert "Let me help you" not in out
    assert "phone number" in out


def test_strip_of_course():
    out, _ = ar.scrub("Of course! Tuesday morning works.")
    # Either way "Of course" should be gone
    assert "of course" not in out.lower()
    assert "Tuesday morning" in out


def test_multiple_openers_stripped():
    out, fired = ar.scrub(
        "Thank you for calling. I'd be happy to help. So you're asking about pricing. "
        "Pump-out is $475."
    )
    assert "Thank you" not in out
    assert "I'd be happy" not in out
    assert "So you're" not in out
    assert "$475" in out
    assert len(fired) >= 3


# ── substitutions ────────────────────────────────────────────────────

def test_certainly_becomes_sure():
    out, _ = ar.scrub("Certainly, we can do that.")
    assert "Certainly" not in out
    assert "Sure" in out


def test_absolutely_becomes_yeah():
    out, _ = ar.scrub("Absolutely, that works.")
    assert "Absolutely" not in out
    assert out.lower().startswith("yeah")


def test_apologize_for_inconvenience():
    out, _ = ar.scrub("We can fit you in Tuesday. I apologize for the inconvenience.")
    assert "apologize for the inconvenience" not in out
    assert "sorry" in out.lower()


def test_please_be_advised_stripped():
    out, _ = ar.scrub("Please be advised that we're closed Sundays.")
    assert "Please be advised" not in out
    assert "we're closed Sundays" in out.lower() or "We're closed" in out


def test_how_may_i_assist_replaced():
    out, _ = ar.scrub("How may I assist you today?")
    assert "How may I assist" not in out
    assert "what's up" in out.lower()


# ── case preservation ────────────────────────────────────────────────

def test_first_letter_capitalized_after_strip():
    """If we strip an opener leaving lowercase first letter, capitalize it."""
    out, _ = ar.scrub("So you're asking about pricing. pump-out is $475.")
    # Whatever survives, it should start with a capital
    assert out[0].isupper()


def test_compact_double_spaces():
    out, _ = ar.scrub("Of course!  Tuesday  morning  works.")
    assert "  " not in out


# ── safety ───────────────────────────────────────────────────────────

def test_empty_input():
    assert ar.scrub("") == ("", [])
    assert ar.scrub(None) == ("", [])


def test_clean_input_passes_through():
    inp = "Yeah, what's the address?"
    out, fired = ar.scrub(inp)
    assert out == inp
    assert fired == []


def test_no_full_strip_to_empty():
    """If a reply is ONLY a forbidden phrase, we keep the original text
    rather than emit empty audio."""
    inp = "I apologize for the inconvenience."
    out, _ = ar.scrub(inp)
    # Should contain something, not be empty
    assert out.strip() != ""


def test_does_not_mangle_numbers_or_addresses():
    inp = "Got it — 4273 Mill Creek Road, Tuesday at 9 AM."
    out, _ = ar.scrub(inp)
    assert "4273" in out
    assert "9 AM" in out
    assert "Tuesday" in out


# ── per-tenant toggle ────────────────────────────────────────────────

def test_is_enabled_default_true():
    assert ar.is_enabled(None) is True
    assert ar.is_enabled({}) is True


def test_is_enabled_explicit_false():
    assert ar.is_enabled({"anti_robot_scrub": False}) is False
    assert ar.is_enabled({"anti_robot_scrub": "false"}) is False


# ── pipeline integration ─────────────────────────────────────────────

def test_pipeline_strips_robot_phrases(monkeypatch):
    import main, llm
    from llm import ChatResponse
    fake = ChatResponse(
        reply="Certainly! I understand your concern. Tuesday morning at 9 AM works.",
        intent="Scheduling", priority="low",
    )
    monkeypatch.setattr(llm, "chat_with_usage",
                        lambda *a, **k: (fake, (50, 10)))
    caller = {"id": "x", "phone": "+15551234567", "type": "new",
              "history": [], "conversation": []}
    out = main._run_pipeline(
        caller, "what time can you come?",
        client={"id": "ace_hvac", "name": "Ace HVAC", "plan": {}},
        call_sid="CA_ar_1",
    )
    assert "Certainly" not in out["reply"]
    assert "I understand your concern" not in out["reply"]
    assert "Tuesday morning" in out["reply"]
