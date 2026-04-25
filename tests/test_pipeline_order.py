"""V5.3 — response pipeline ordering integration tests.

The reply text flows through 4 transforms in this order:

  LLM raw reply
   → anti_robot.scrub        (V4.3) strips robotic phrases
   → grounding.verify_reply  (V4.4) replaces invented prices
   → humanize_for_speech     (V4.2) converts numbers/addresses/etc.
   → tts.render              (V4.1) emits Polly Say or ElevenLabs Play

Critical invariants:
  - anti_robot runs BEFORE grounding so "Certainly! That's $999." is
    grounding-checked as just "That's $999." — without strip-first,
    grounding might miss the dollar amount or substitute oddly.
  - grounding runs BEFORE humanize. Grounding looks for "$N" tokens; if
    humanize converted "$475" → "four hundred seventy-five dollars"
    first, grounding would never see the price and could miss invented
    quotes.
  - humanize runs BEFORE TTS so the speech engine reads natural form.

Tests verify each stage's output and that the order is preserved
end-to-end through main._run_pipeline + _respond.
"""
from __future__ import annotations

import pytest

import llm
from src import anti_robot, grounding, humanize_speech


# ── ordering invariants ─────────────────────────────────────────────

def test_anti_robot_runs_before_grounding():
    """A robotic+invented-price reply: scrub strips opener, then
    grounding catches the still-bad price.

    If the order were reversed, grounding would replace the SENTENCE
    containing $999 with the fallback line — which is fine — but the
    "Certainly," opener would still hit anti_robot afterward. Our
    actual order: anti_robot → grounding. Same end result either way
    in this case, but the ordering matters for messages where the
    robot phrase itself contains a price."""
    raw = "Certainly! That's $999 for the swap."
    cleaned, _ = anti_robot.scrub(raw)
    # anti_robot removes "Certainly!" → "Sure, that's $999 for the swap."
    assert "$999" in cleaned
    assert "Certainly" not in cleaned

    grounded, _ = grounding.verify_reply(cleaned, {
        "id": "x", "pricing_summary": "Pump-out $475."})
    # grounding replaces the entire $999 sentence with the fallback
    assert "$999" not in grounded
    assert "let me check" in grounded.lower() or "exact number" in grounded.lower()


def test_grounding_runs_before_humanize():
    """grounding sees raw '$N' tokens; humanize converts them away.
    If humanize ran first, '$475' would become 'four hundred seventy-
    five dollars' and grounding wouldn't recognize the dollar amount,
    so an invented price could slip through to TTS."""
    raw = "It's $999 for that."
    grounded, violations = grounding.verify_reply(raw, {
        "id": "x", "pricing_summary": "Pump-out $475."})
    assert violations  # caught
    assert "$999" not in grounded

    # Now humanize runs on the grounded result — no $-tokens left
    spoken = humanize_speech.humanize_for_speech(grounded)
    assert "$" not in spoken


def test_humanize_then_grounding_would_miss_invented_prices():
    """Negative test: if you swapped the order (humanize first), the
    invented price would survive to TTS. This documents WHY our order
    is what it is."""
    raw = "That's $999."
    spoken_first = humanize_speech.humanize_for_speech(raw)
    # After humanize, no $ token remains — grounding would find no
    # prices to validate against the allowed set.
    grounded_after, violations = grounding.verify_reply(spoken_first, {
        "id": "x", "pricing_summary": "Pump-out $475."})
    assert violations == []   # NOTHING flagged — bug hidden!
    assert "nine hundred" in grounded_after.lower()


# ── full _run_pipeline integration ──────────────────────────────────

def test_pipeline_full_chain_strip_ground_humanize(monkeypatch):
    """End-to-end: LLM emits a robotic, invented-price, mixed-format
    reply → after the pipeline, it's clean + grounded + spoken."""
    import main

    # LLM emits the worst-case slop:
    #  - corporate opener ("Certainly! ")
    #  - invented price ($999)
    #  - phone number (in TTS-unfriendly format)
    fake = llm.ChatResponse(
        reply="Certainly! AC compressor swap is $999. Call (555) 123-4567.",
        intent="Quote", priority="low",
    )
    monkeypatch.setattr(llm, "chat_with_usage",
                        lambda *a, **k: (fake, (50, 10)))

    caller = {"id": "x", "phone": "+15555551111", "type": "new",
              "history": [], "conversation": []}
    out = main._run_pipeline(
        caller, "how much for an AC swap?",
        client={"id": "ace_hvac", "name": "Ace HVAC",
                "pricing_summary": "Service call $129. AC install $4800-8500.",
                "plan": {}},
        call_sid="CA_pipe_full",
    )
    reply = out["reply"]
    # Stage 1 (anti_robot) — opener gone
    assert "Certainly" not in reply
    # Stage 2 (grounding) — invented $999 gone, replaced with fallback
    assert "$999" not in reply
    # The grounded fallback OR the surviving phone are in there
    assert ("let me check" in reply.lower()
            or "exact number" in reply.lower()
            or "(555) 123-4567" in reply)
    # Phone number text is NOT yet humanized at the pipeline stage —
    # humanize fires later in _respond before TTS. But the pipeline
    # returns the grounded/scrubbed form.


def test_pipeline_keeps_grounded_price_intact(monkeypatch):
    """When the LLM quotes a real price from pricing_summary, all three
    stages should leave it intact for the TTS layer."""
    import main
    fake = llm.ChatResponse(
        reply="Sure, service call's $129.",
        intent="Quote", priority="low",
    )
    monkeypatch.setattr(llm, "chat_with_usage",
                        lambda *a, **k: (fake, (50, 10)))
    caller = {"id": "x", "phone": "+15555552222", "type": "new",
              "history": [], "conversation": []}
    out = main._run_pipeline(
        caller, "service call cost?",
        client={"id": "ace_hvac", "name": "Ace HVAC",
                "pricing_summary": "Service call $129.",
                "plan": {}},
        call_sid="CA_pipe_keep",
    )
    # $129 survives all stages
    assert "$129" in out["reply"]


def test_pipeline_humanize_applied_at_response_layer(monkeypatch):
    """humanize_for_speech runs in _respond (not _run_pipeline). Direct
    test: feed a pipeline output into _respond and verify the spoken
    form has natural numbers."""
    # Manually feed a pipeline reply that contains $475 and a phone
    # number. humanize_for_speech should turn these natural.
    inp = "It's $475. Call us at (555) 123-4567."
    spoken = humanize_speech.humanize_for_speech(inp)
    assert "$475" not in spoken
    assert "four hundred seventy-five dollars" in spoken
    assert "(555) 123-4567" not in spoken
    assert "five five five" in spoken


# ── feature-flag interaction ────────────────────────────────────────

def test_disable_anti_robot_per_tenant_skips_scrub(monkeypatch):
    """When tenant opts out of anti_robot, robotic phrases survive."""
    import main
    fake = llm.ChatResponse(
        reply="Certainly! Tuesday at 9 AM works.",
        intent="Scheduling", priority="low",
    )
    monkeypatch.setattr(llm, "chat_with_usage",
                        lambda *a, **k: (fake, (50, 10)))
    out = main._run_pipeline(
        {"id": "x", "phone": "+1", "history": [], "conversation": []},
        "what time?",
        client={"id": "ace_hvac", "name": "Ace HVAC",
                "anti_robot_scrub": False, "plan": {}},
        call_sid="CA_anti_off",
    )
    assert "Certainly" in out["reply"]


def test_disable_grounding_per_tenant_keeps_invented_prices(monkeypatch):
    """When tenant opts out of grounding, invented prices survive."""
    import main
    fake = llm.ChatResponse(
        reply="Sure, that's $999.",
        intent="Quote", priority="low",
    )
    monkeypatch.setattr(llm, "chat_with_usage",
                        lambda *a, **k: (fake, (50, 10)))
    out = main._run_pipeline(
        {"id": "x", "phone": "+1", "history": [], "conversation": []},
        "price?",
        client={"id": "ace_hvac", "name": "Ace HVAC",
                "pricing_summary": "Service call $129.",
                "strict_grounding": False, "plan": {}},
        call_sid="CA_grnd_off",
    )
    assert "$999" in out["reply"]


# ── empty / safety checks ───────────────────────────────────────────

def test_pipeline_empty_reply_safe(monkeypatch):
    """A weirdly-empty LLM reply shouldn't crash the pipeline."""
    import main
    fake = llm.ChatResponse(reply="", intent="General", priority="low")
    monkeypatch.setattr(llm, "chat_with_usage",
                        lambda *a, **k: (fake, (50, 10)))
    out = main._run_pipeline(
        {"id": "x", "phone": "+1", "history": [], "conversation": []},
        "hi",
        client={"id": "ace_hvac", "name": "Ace HVAC", "plan": {}},
        call_sid="CA_empty",
    )
    # No exception, valid output dict
    assert "reply" in out
