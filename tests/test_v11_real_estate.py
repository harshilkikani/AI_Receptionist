"""V11.0 — real-estate first-class flow tests.

Real estate became one of the strongest demo verticals in V11.0 per
the user brief. This file exercises the full real-estate flow:
buyer inquiry → showing booking → agent owner-SMS notification →
portal entry. Plus deeper checks on the registry's real-estate
content — speed-to-lead language, weekend showing slots, lockbox-
emergency handling, no-market-value rule.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import industries


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


# ── 1. Registry content ─────────────────────────────────────────────


def test_real_estate_is_tier_1():
    """V11.0 — real estate ships at first-class depth alongside HVAC
    and Septic."""
    re = industries.get("real_estate")
    assert re["tier"] == 1


def test_real_estate_uses_realtor_vocabulary():
    """The vertical's nouns must read native to agents — buyer,
    showing, listing — not generic customer-service language."""
    re = industries.get("real_estate")
    assert re["customer_term"] == "buyer"
    assert re["business_noun"] == "showing"
    assert re["business_noun_plural"] == "showings"
    assert re["owner_role"] == "agent"


def test_real_estate_prompt_mentions_speed_to_lead():
    """The brief specifically called out speed-to-lead psychology as
    the central real-estate motif. The prompt must encode it."""
    prompt = industries.prompt_fragment("real_estate")
    lower = prompt.lower()
    assert "speed-to-lead" in lower or "speed to lead" in lower or "5 minutes" in lower, (
        "real-estate prompt should encode speed-to-lead psychology")


def test_real_estate_prompt_offers_weekend_showings():
    """Saturday/Sunday afternoon is the conventional showing window
    — the prompt should default to offering those."""
    prompt = industries.prompt_fragment("real_estate").lower()
    assert "saturday" in prompt or "sunday" in prompt or "weekend" in prompt


def test_real_estate_prompt_does_not_quote_market_value():
    """The agent's job — not the receptionist's. Verbalizing this in
    the prompt prevents the AI from hallucinating prices on calls."""
    prompt = industries.prompt_fragment("real_estate").lower()
    assert ("don't quote market value" in prompt
            or "not the receptionist's job" in prompt
            or "market value" in prompt and "agent" in prompt), (
        "real-estate prompt should defer market-value to the agent")


def test_real_estate_prompt_handles_lockbox_as_emergency():
    """Lockbox / access issues at an active showing are the canonical
    real-estate emergency — agent paged immediately."""
    prompt = industries.prompt_fragment("real_estate").lower()
    assert "lockbox" in prompt or "access" in prompt
    assert "emergenc" in prompt or "immediately" in prompt or "text the agent" in prompt


def test_real_estate_emergency_keywords_include_lockbox_terms():
    keywords = [k.lower() for k in industries.emergency_keywords("real_estate")]
    # At least one lockbox-related trigger
    assert any("lock" in k for k in keywords), (
        f"real-estate emergency keywords missing lockbox triggers: {keywords}")


def test_real_estate_seller_intake_routes_to_cma():
    """V11.0: seller inquiries → CMA prep callback. V13.0: the
    owner-SMS template is body-only (sender carries the customer
    name), so 'Seller' no longer appears in the template literal.
    The CMA-prep routing is encoded in the system prompt — verify
    there instead."""
    prompt = industries.get("real_estate")["system_prompt"]
    assert "CMA" in prompt
    assert "list" in prompt.lower()  # "wanting to list"
    # Quote template still carries the CMA workflow hook
    quote_template = industries.get("real_estate")["owner_sms_templates"]["quote"]
    assert "CMA" in quote_template or "list" in quote_template.lower()


# ── 2. Owner-SMS rendering ──────────────────────────────────────────


def test_real_estate_emergency_owner_sms_format():
    """V11.0 → V13.0 — emergency template is body-only. The
    customer name now lives in the sender row (with avatar) so the
    body is just the situational details: '{addr} — lockbox stuck.
    Buyer on-site at {phone}.'"""
    rendered = industries.owner_sms("real_estate", "emergency", {
        "name": "Jordan Bailey",
        "addr": "1100 Birch Road",
        "phone": "+15550103005",
    })
    assert "1100 Birch" in rendered
    # Name no longer in the body (sender row carries it)
    assert "Jordan Bailey" not in rendered
    # Lockbox situation conveyed by the body
    assert "lockbox" in rendered.lower()


def test_real_estate_booking_owner_sms_format():
    """V11.0 → V13.0 — booking template is body-only."""
    rendered = industries.owner_sms("real_estate", "booking", {
        "name": "Caleb Morrison",
        "addr": "1100 Birch Road",
        "time": "Saturday 1pm",
        "phone": "+15550103001",
    })
    assert "1100 Birch" in rendered
    assert "Saturday 1pm" in rendered
    # Name no longer in the body
    assert "Caleb Morrison" not in rendered


# ── 3. Personas ─────────────────────────────────────────────────────


def test_real_estate_has_six_personas():
    """Tier-1 verticals ship 6 personas covering the canonical
    real-estate scenarios."""
    from src import demo_seed
    personas = demo_seed.list_personas(industry="real_estate")
    # 6 seeded + 1 fresh
    assert len(personas) == 7
    # The 6 canonical persona names ship
    names = {p["name"] for p in personas}
    expected = {
        "Caleb Morrison",    # buyer inquiry / Saturday tour
        "Priya Shah",        # open-house follow-up / disclosure
        "Daniel Ellis",      # seller / CMA
        "Sienna Park",       # returning buyer / negotiation
        "Jordan Bailey",     # active-showing lockbox emergency
        "Emily Rodriguez",   # after-hours Zillow lead
    }
    for name in expected:
        assert name in names, f"real-estate persona missing: {name}"


def test_real_estate_lockbox_persona_is_emergency():
    """Jordan Bailey is the canonical lockbox-emergency scenario.
    Voice.emergency must be True so the demo flags it red in the portal."""
    from src import demo_seed
    personas = demo_seed.list_personas(industry="real_estate")
    jordan = next((p for p in personas if "Jordan" in p["name"]), None)
    assert jordan is not None
    # Find the underlying scenario for emergency check
    from src import demo_personas
    scenarios = demo_personas.personas_for("real_estate")
    jordan_sc = next(s for s in scenarios if s["caller_id"] == "re_jordan")
    assert jordan_sc.get("voice", {}).get("emergency") is True


def test_real_estate_emily_has_cross_channel_history():
    """Emily Rodriguez ships with both a voice call AND a follow-up
    SMS — the canonical V11.0 'unified timeline' demonstration for
    real-estate (call landed at 9pm, agent-confirm SMS 1min later)."""
    from src import demo_personas
    scenarios = demo_personas.personas_for("real_estate")
    emily = next(s for s in scenarios if s["caller_id"] == "re_emily")
    assert emily.get("voice") is not None
    assert emily.get("sms") is not None
    assert emily["sms"].get("minutes_after_voice", 0) >= 1


# ── 4. Switcher renders real estate ─────────────────────────────────


def test_switcher_includes_real_estate_with_correct_brand(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'value="real_estate"' in body
    assert 'data-brand="Lawrence Realty"' in body
    assert 'data-owner="Lauren"' in body


def test_switcher_real_estate_carries_realtor_specific_suggestions(app_client):
    """The data-suggestions JSON for real_estate must include real-
    estate-native scenarios (showings, listings) not generic ones."""
    r = app_client.get("/")
    body = r.text
    import re as _re, json
    m = _re.search(
        r'value="real_estate"[^>]*data-suggestions="([^"]+)"', body)
    assert m, "real_estate option's data-suggestions missing"
    raw = m.group(1).replace("&quot;", '"')
    suggestions = json.loads(raw)
    # At least one suggestion mentions a listing/showing/tour
    blob = " ".join(suggestions).lower()
    assert any(t in blob for t in ("listing", "showing", "tour", "birch"))


def test_real_estate_portal_uses_leads_terminology(app_client):
    """Portal Today headline data attr swaps to 'Today's leads' for
    real estate (vs 'Today's calls' for HVAC)."""
    r = app_client.get("/")
    body = r.text
    import re as _re
    m = _re.search(
        r'value="real_estate"[^>]*data-portal-stats="([^"]+)"', body)
    assert m
    # HTML-escape the data-attr contents (apostrophes become &#x27;);
    # unescape before JSON parsing.
    import html as _html
    raw = _html.unescape(m.group(1))
    import json
    parsed = json.loads(raw)
    assert parsed["today_headline"] == "Today's leads"
    assert parsed["partner_term"] == "lead"


# ── 5. /demo/callers and /chat E2E ──────────────────────────────────


def test_demo_callers_real_estate_returns_realtor_personas(app_client):
    r = app_client.get("/demo/callers?industry=real_estate")
    assert r.status_code == 200
    callers = r.json()
    names = [c["name"] for c in callers]
    assert "Caleb Morrison" in names
    assert "Jordan Bailey" in names
    # All non-fresh callers are in the +15550103 range
    for c in callers:
        if c["name"] not in ("New caller", "Unknown caller"):
            assert c["phone"].startswith("+155501030"), (
                f"{c['name']}: phone {c['phone']} not in real-estate range")


def test_chat_real_estate_routes_through_registry_prompt(app_client, monkeypatch):
    """End-to-end: posting to /chat with industry='real_estate' must
    deliver the registry's real-estate prompt to the pipeline."""
    import main
    captured = {}
    def fake_pipeline(caller, message, **kw):
        captured["message"] = message
        return {"reply": "ok", "intent": "General", "priority": "low",
                "sentiment": "neutral", "caller": caller}
    monkeypatch.setattr(main, "_run_pipeline", fake_pipeline)
    from src import demo_seed
    demo_seed.register_personas_in_memory()
    r = app_client.post("/chat", json={
        "caller_id": "marcus",
        "message": "Is 1100 Birch still available?",
        "client_id": "septic_pro",
        "industry": "real_estate",
    })
    assert r.status_code == 200
    msg = captured["message"]
    assert msg.startswith("[Context:")
    # Real-estate-specific signals from the prompt
    lower = msg.lower()
    assert "real-estate brokerage" in lower or "real estate brokerage" in lower
    assert "listing" in lower or "showing" in lower
    # User message preserved
    assert msg.endswith("Is 1100 Birch still available?")


def test_chat_real_estate_legacy_slug_also_works(app_client, monkeypatch):
    """Pre-V11.0 callers used 'real-estate' (hyphenated). The legacy
    alias must still resolve."""
    import main
    captured = {}
    def fake_pipeline(caller, message, **kw):
        captured["message"] = message
        return {"reply": "ok", "intent": "General", "priority": "low",
                "sentiment": "neutral", "caller": caller}
    monkeypatch.setattr(main, "_run_pipeline", fake_pipeline)
    from src import demo_seed
    demo_seed.register_personas_in_memory()
    app_client.post("/chat", json={
        "caller_id": "marcus", "message": "buyer here",
        "client_id": "septic_pro", "industry": "real-estate",
    })
    msg = captured["message"]
    assert msg.startswith("[Context:")


# ── 6. Seeded owner-phone bubbles ───────────────────────────────────


def test_real_estate_seeded_bubbles_show_lockbox_and_showing(app_client):
    """V11.0 → V13.0 — seeded bubbles use natural-language bodies
    without the leading 'Category · {name} ·' prefix (the customer
    name lives in the sender row with the avatar). Verify the
    workflow signals (lockbox + Saturday showing) survive."""
    seeded = industries.seeded_owner_sms("real_estate")
    bodies = " ".join(s["body"] for s in seeded)
    customer_names = {s.get("customer_name") for s in seeded}
    assert "lockbox" in bodies.lower()
    assert "Saturday" in bodies
    assert "1100 Birch" in bodies
    # Customer-name continuity preserved via the customer_name field
    assert "Jordan Bailey" in customer_names
    assert "Caleb Morrison" in customer_names
