"""V4.4 — strict grounding (anti-hallucination) tests."""
from __future__ import annotations

import pytest

from src import grounding


def _client(*, pricing_summary="", services="", id="test_client",
            knowledge_md=None, **extra):
    c = {
        "id": id,
        "pricing_summary": pricing_summary,
        "services": services,
        "hours": "Mon-Fri 8am-5pm",
        "service_area": "local",
    }
    c.update(extra)
    if knowledge_md is not None:
        # Caller injects a mock; we'll patch knowledge.load_kb in tests
        c["_test_kb"] = knowledge_md
    return c


# ── _extract_prices ──────────────────────────────────────────────────

def test_extract_simple_dollar():
    assert grounding._extract_prices("It's $475.") == [475.0]


def test_extract_with_decimal():
    assert grounding._extract_prices("It's $129.95.") == [129.95]


def test_extract_with_thousands_separator():
    assert grounding._extract_prices("$1,500 install fee.") == [1500.0]


def test_extract_multiple():
    out = grounding._extract_prices("$129 service call, $475 pump-out.")
    assert out == [129.0, 475.0]


def test_extract_none():
    assert grounding._extract_prices("No price here") == []
    assert grounding._extract_prices("") == []


# ── allowed prices ───────────────────────────────────────────────────

def test_allowed_prices_from_pricing_summary():
    c = _client(pricing_summary="Pump-out $475. Install $6,000.")
    prices = grounding._allowed_prices(c)
    assert 475.0 in prices
    assert 6000.0 in prices


def test_allowed_prices_empty_client():
    assert grounding._allowed_prices(None) == set()


def test_allowed_prices_pulls_from_knowledge(monkeypatch):
    """KB pricing should also count as allowed."""
    fake_kb = (
        ("Pricing", "Pump-outs from $525 for 1500-gallon", frozenset()),
    )
    from src import knowledge
    monkeypatch.setattr(knowledge, "load_kb",
                        lambda cid: fake_kb if cid == "septic" else tuple())
    c = _client(id="septic", pricing_summary="")
    prices = grounding._allowed_prices(c)
    assert 525.0 in prices


# ── tolerance ────────────────────────────────────────────────────────

def test_close_enough_within_tolerance():
    # ±20% default — $500 within 20% of $475 (5.3% off)
    assert grounding._is_close_enough(500, {475}) is True


def test_close_enough_outside_tolerance():
    # $750 vs $475 → 58% off — too far
    assert grounding._is_close_enough(750, {475}) is False


def test_close_enough_empty_allowed():
    assert grounding._is_close_enough(100, set()) is False


# ── verify_reply ─────────────────────────────────────────────────────

def test_verify_no_prices_passes():
    c = _client(pricing_summary="Pump-out $475.")
    out, violations = grounding.verify_reply(
        "Sure, what's your address?", c)
    assert out == "Sure, what's your address?"
    assert violations == []


def test_verify_grounded_price_passes():
    c = _client(pricing_summary="Pump-out from $475 for 1000-gallon tanks.")
    out, _ = grounding.verify_reply("Pump-out is $475.", c)
    assert "$475" in out


def test_verify_close_enough_passes():
    c = _client(pricing_summary="Pump-out from $475.")
    # $500 within 20% of $475 — allowed
    out, violations = grounding.verify_reply("It's about $500.", c)
    assert "$500" in out
    assert violations == []


def test_verify_invented_price_replaced():
    c = _client(pricing_summary="Pump-out from $475 for 1000-gallon tanks.")
    out, violations = grounding.verify_reply(
        "Sure, AC compressor swap is $249.", c)
    # The $249 sentence should be replaced
    assert "$249" not in out
    assert "let me check" in out.lower() or "exact number" in out.lower()
    assert len(violations) == 1


def test_verify_partial_replacement():
    c = _client(pricing_summary="Pump-out from $475.")
    inp = ("Pump-out is $475. Inspection is $999 for residential.")
    out, violations = grounding.verify_reply(inp, c)
    # First sentence kept (grounded), second replaced
    assert "$475" in out
    assert "$999" not in out
    assert len(violations) == 1


def test_verify_no_allowed_prices_skips_check():
    """If pricing_summary + KB are empty, we can't ground anything; pass through."""
    c = _client(pricing_summary="", services="")
    out, violations = grounding.verify_reply(
        "Pump-out is $999.", c)
    assert "$999" in out
    assert violations == []


def test_verify_disabled_per_tenant():
    c = _client(pricing_summary="Pump-out $475.", strict_grounding=False)
    out, violations = grounding.verify_reply("It's $9999.", c)
    assert "$9999" in out
    assert violations == []


def test_verify_empty_input():
    assert grounding.verify_reply("", {}) == ("", [])
    assert grounding.verify_reply(None, {}) == ("", [])


def test_verify_handles_exception_gracefully(monkeypatch):
    """A bad client object shouldn't crash the receptionist."""
    monkeypatch.setattr(grounding, "_allowed_prices",
                        lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    out, violations = grounding.verify_reply("It's $999.", {"id": "x"})
    # Falls back to original
    assert out == "It's $999."
    assert violations == []


def test_verify_collapses_repeated_fallbacks():
    """If MULTIPLE sentences hit, we collapse to ONE fallback line."""
    c = _client(pricing_summary="Pump-out $475.")
    inp = "AC swap is $249. Furnace install is $899. Heater fix is $325."
    out, _ = grounding.verify_reply(inp, c)
    # The fallback line should appear at most once or twice (allowing
    # for sentence boundaries), not 3x identical
    fallback_count = out.lower().count("let me check the exact number")
    assert fallback_count <= 2  # collapsed


# ── per-tenant toggle ────────────────────────────────────────────────

def test_is_enabled_default_true():
    assert grounding.is_enabled(None) is True
    assert grounding.is_enabled({}) is True


def test_is_enabled_explicit_false():
    assert grounding.is_enabled({"strict_grounding": False}) is False
    assert grounding.is_enabled({"strict_grounding": "false"}) is False


# ── pipeline integration ─────────────────────────────────────────────

def test_pipeline_replaces_invented_price(monkeypatch):
    import main
    import llm
    from llm import ChatResponse

    fake = ChatResponse(
        reply="Sure, AC compressor swap is $249.",
        intent="Quote", priority="low",
    )
    monkeypatch.setattr(llm, "chat_with_usage",
                        lambda *a, **k: (fake, (50, 10)))

    caller = {"id": "x", "phone": "+15551234567", "type": "new",
              "history": [], "conversation": []}
    out = main._run_pipeline(
        caller, "how much for an AC swap?",
        client={"id": "ace_hvac", "name": "Ace HVAC",
                "pricing_summary": "Service call $129. AC install $4800-8500.",
                "plan": {}},
        call_sid="CA_grnd_1",
    )
    assert "$249" not in out["reply"]
    assert "let me check" in out["reply"].lower() or "exact number" in out["reply"].lower()


def test_pipeline_keeps_grounded_price(monkeypatch):
    import main
    import llm
    from llm import ChatResponse

    fake = ChatResponse(
        reply="Service call's $129.",
        intent="Quote", priority="low",
    )
    monkeypatch.setattr(llm, "chat_with_usage",
                        lambda *a, **k: (fake, (50, 10)))

    caller = {"id": "x", "phone": "+15551234567", "type": "new",
              "history": [], "conversation": []}
    out = main._run_pipeline(
        caller, "service call cost?",
        client={"id": "ace_hvac", "name": "Ace HVAC",
                "pricing_summary": "Service call $129.",
                "plan": {}},
        call_sid="CA_grnd_2",
    )
    assert "$129" in out["reply"]
