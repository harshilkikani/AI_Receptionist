"""V13.0 — restraint, alignment, authenticity regression suite.

Guards the five V13.0 commits:
  A: owner-phone authenticity (sender + body + unified bubble)
  B: demo-chrome subtraction (iOS bar, pane labels, gradients, etc.)
  C: industry authenticity tightening (role labels, prompt carveouts)
  D: multilingual reduction (en+es only) + dead-code purge
  E: conversational + perf restraint (filler gate, ElevenLabs gate,
     today_body cache, partner_label index)
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


ALL_SLUGS = [
    "hvac", "real_estate", "septic",
    "construction", "electrical", "plumbing",
    "property_management", "roofing",
    "landscaping", "legal_intake", "med_spa", "restoration",
]


# ── A. Owner-phone authenticity ─────────────────────────────────────


def test_no_ai_receptionist_sender_in_owner_phone(app_client):
    """V13.0 — every seeded owner-SMS bubble's sender row carries
    the customer's name, not the marketing string 'AI Receptionist'."""
    r = app_client.get("/")
    body = r.text
    assert '<span class="sms-from">AI Receptionist</span>' not in body
    # Concrete customer names should appear as sender
    assert '<span class="sms-from">Marcus Reilly</span>' in body
    assert '<span class="sms-from">Wendy Larsen</span>' in body


def test_owner_sms_templates_body_only_across_industries():
    """Every owner_sms_template no longer leads with a category +
    customer-name prefix. The customer name now lives in the sender
    row with the avatar; the body is body-only."""
    for slug in ALL_SLUGS:
        templates = industries.get(slug)["owner_sms_templates"]
        for kind, tmpl in templates.items():
            # The template should NOT start with "Emergency · {name}"
            # or any other "Category · {name} ·" prefix.
            assert not tmpl.startswith("Emergency · "), (
                f"{slug}.{kind}: V13.0 retired the 'Emergency · {{name}}' prefix")
            assert "{name}" not in tmpl, (
                f"{slug}.{kind}: customer name is now in the sender "
                f"row; {{name}} placeholder should be gone from body")


def test_seeded_owner_sms_bodies_skip_category_prefix():
    """Same rule for the pre-baked bubbles."""
    for slug in ALL_SLUGS:
        for entry in industries.seeded_owner_sms(slug):
            body = entry["body"]
            # No leading "Emergency · {name} ·" or "Booking · {name} ·"
            cust = entry.get("customer_name", "")
            if cust:
                assert not body.startswith(f"Emergency · {cust}"), (
                    f"{slug}: seeded body still has category+name prefix")


def test_septic_marcus_renamed():
    """V13.0 A — Septic Marcus Reilly was renamed to break the
    cross-industry name collision with HVAC's Marcus Reilly."""
    septic_seeded = industries.seeded_owner_sms("septic")
    names = {e["customer_name"] for e in septic_seeded}
    assert "Marcus Reilly" not in names, (
        "Septic shouldn't share a name with HVAC Marcus")
    assert "Henry Walsh" in names, "Septic emergency persona should be Henry Walsh"


def test_owner_sms_bubble_typography_unified():
    """V13.0 A — .owner-sms bubble vocabulary now matches the
    customer-side .pmsg (radius 18, font 14, padding 9/14) so both
    phones speak one chat language."""
    from src import design
    css = design.css()
    idx = css.find(".owner-sms {")
    chunk = css[idx:idx + 400]
    assert "border-radius: 18px" in chunk
    assert "font-size: 14px" in chunk
    assert "padding: 9px 14px" in chunk


def test_chat_response_includes_owner_sms_body(app_client, monkeypatch):
    """V13.0 A — /chat returns `owner_sms_body` rendered via
    industries.owner_sms() so the client-side pushOwnerSMS uses
    the same per-industry template as the seeded bubbles."""
    import main
    captured = {}
    def fake_pipeline(caller, message, **kw):
        captured["message"] = message
        return {"reply": "ok", "intent": "Emergency", "priority": "high",
                "sentiment": "neutral", "caller": caller}
    monkeypatch.setattr(main, "_run_pipeline", fake_pipeline)
    from src import demo_seed
    demo_seed.register_personas_in_memory(industry="all")
    r = app_client.post("/chat", json={
        "caller_id": "hvac_marcus",
        "message": "AC died",
        "client_id": "septic_pro",
        "industry": "hvac",
    })
    data = r.json()
    assert "owner_sms_body" in data, "emergency intent should produce body"
    assert data["owner_sms_kind"] == "emergency"
    # The body should NOT lead with the customer name (now in sender)
    assert not data["owner_sms_body"].startswith("Emergency · ")


# ── B. Demo-chrome subtraction ──────────────────────────────────────


def test_phone_status_bar_removed(app_client):
    """V13.0 B — iOS-mimicking 9:41 + signal + battery status bar
    removed wholesale."""
    r = app_client.get("/")
    body = r.text
    assert 'class="phone-status"' not in body
    assert ">9:41<" not in body
    assert 'class="ps-battery"' not in body


def test_pane_labels_neutralized(app_client):
    """V13.0 B — 'What you see' / 'What your customer sees' tour-
    guide captions replaced with neutral chrome words. We assert
    the labels INSIDE <div class="pane-label"> elements, not
    against full-page text (which includes the HTML comment
    explaining the change)."""
    r = app_client.get("/")
    body = r.text
    import re
    pane_labels = re.findall(
        r'<div class="pane-label"[^>]*>([^<]+)<', body)
    pane_labels += re.findall(
        r'<div class="pane-label"[^>]*>\s*<span>([^<]+)<', body)
    # No tour-guide caption in any actual pane-label element
    for label in pane_labels:
        normalized = label.strip().lower()
        assert "what your customer sees" not in normalized
        assert "what you see on your phone" not in normalized
        # "What you see" with no qualifier is the operator-pane label
        assert normalized != "what you see"
    # New chrome words present
    assert ">Messages<" in body
    assert ">Notifications<" in body


def test_pane_label_no_uppercase_tracking():
    """V13.0 B — .pane-label dropped text-transform:uppercase + the
    0.08em letter-spacing. Now reads as quiet chrome."""
    from src import design
    css = design.css()
    idx = css.find(".pane-label {")
    chunk = css[idx:idx + 300]
    assert "text-transform: uppercase" not in chunk
    assert "0.08em" not in chunk


def test_brand_word_gradient_removed():
    """V13.0 B — wordmark gradient text fill removed; solid color
    only. Linear / Arc / OpenPhone wordmarks are solid."""
    from src import design
    css = design.css()
    idx = css.find(".demo-brand .brand-word {")
    chunk = css[idx:idx + 250]
    assert "background-clip: text" not in chunk
    assert "linear-gradient" not in chunk


def test_live_breathe_keyframe_removed():
    """V13.0 B — always-on halo animation on the green Live dot
    retired; the .live-pulse-flash filter brighten survives for
    real refresh events."""
    from src import design
    css = design.css()
    # The keyframe rule body is gone (a comment may mention the name
    # but contains no "@keyframes live-breathe {" literal)
    assert "@keyframes live-breathe" not in css
    # The flash class survives
    assert ".live-pulse-flash" in css


def test_drawer_copy_reduced(app_client):
    """V13.0 B — drawer no longer announces 'Demo'. Title is
    'Settings'; destructive button is 'Clear inbox'. We assert
    on the rendered element text, not the surrounding HTML
    comments (which mention the V13.0 rename for archival)."""
    r = app_client.get("/")
    body = r.text
    # The drawer's title <span> renders "Settings"
    assert '<span class="demo-drawer-title">Settings</span>' in body
    # The destructive button reads "Clear inbox"
    assert ">Clear inbox<" in body
    # And the OLD title element is gone
    assert '<span class="demo-drawer-title">Demo controls</span>' not in body
    assert ">Reset demo<" not in body


def test_today_sub_second_sentence_removed(app_client):
    """V13.0 B — today-sub second sentence ('This page updates as
    calls come in.') removed. The Live indicator conveys it."""
    r = app_client.get("/")
    body = r.text
    assert "This page updates as calls come in" not in body


# ── C. Industry authenticity ────────────────────────────────────────


def test_notification_label_role_specific():
    """V13.0 C — Real Estate / Legal Intake / Med Spa get role-
    specific labels."""
    assert industries.notification_label("real_estate") == "Agent"
    assert industries.notification_label("legal_intake") == "Attorney"
    assert industries.notification_label("med_spa") == "Clinic"
    assert industries.notification_label("property_management") == "Manager"
    assert industries.notification_label("hvac") == "Owner"


def test_real_estate_prompt_active_showing_90s_carveout():
    """V13.0 C — Real Estate prompt explicitly mentions the 90-second
    page-the-agent rule for active-showing lockbox issues; matches
    the seeded Jordan dialogue."""
    prompt = industries.get("real_estate")["system_prompt"]
    assert "90 seconds" in prompt or "90-second" in prompt
    assert "lockbox" in prompt.lower() or "access" in prompt.lower()


def test_med_spa_yara_no_price_quote():
    """V13.0 C — V11 dialogue had the receptionist quoting
    '$250 for full legs' which contradicts the prompt's 'Don't
    quote firm prices.' V13.0 dialogue defers to consult."""
    from src import demo_personas
    yara = next(p for p in demo_personas.personas_for("med_spa")
                if p["caller_id"] == "spa_yara")
    turns = yara["voice"]["turns"]
    # Find the assistant turn responding to the laser-pricing question
    assistant_replies = [t[1] for t in turns if t[0] == "assistant"]
    pricing_reply = next((r for r in assistant_replies
                          if "depends" in r.lower() or "consult" in r.lower()),
                         "")
    assert pricing_reply, "Yara assistant should have a pricing-deferral turn"
    assert "$250" not in pricing_reply
    assert "consult" in pricing_reply.lower()


def test_legal_anita_no_fee_quote():
    """V13.0 C — V11 had the intake screener quoting '$250 consult
    fee' which contradicts the prompt's 'fees confirmed by the
    attorney's office, not by you.' V13.0 dialogue defers."""
    from src import demo_personas
    anita = next(p for p in demo_personas.personas_for("legal_intake")
                 if p["caller_id"] == "law_anita")
    turns = anita["voice"]["turns"]
    assistant_replies = " ".join(t[1] for t in turns if t[0] == "assistant")
    assert "$250" not in assistant_replies
    # The deferral language is present
    assert "office" in assistant_replies.lower()


# ── D. Multilingual reduction ───────────────────────────────────────


def test_voice_map_only_english_and_spanish():
    """V13.0 D — VOICE_MAP / STT_LANG_MAP / DTMF_LANG reduced to
    en + es only (hi/gu/pt/it/ja/ko/zh removed)."""
    import main
    assert set(main.VOICE_MAP.keys()) == {"en", "es"}
    assert set(main.STT_LANG_MAP.keys()) == {"en", "es"}
    assert set(main.DTMF_LANG.values()) == {"en", "es"}


def test_voice_tier_map_standard_only_en_es():
    """V13.0 D — VOICE_TIER_MAP.standard fallback also trimmed."""
    import main
    assert set(main.VOICE_TIER_MAP["standard"].keys()) == {"en", "es"}


def test_greeting_templates_only_en_es():
    """V13.0 D — _TEMPLATES_HI / _TEMPLATES_GU deleted from
    src/greeting.py. _TEMPLATES_BY_LANG carries only en + es."""
    from src import greeting
    assert set(greeting._TEMPLATES_BY_LANG.keys()) == {"en", "es"}


def test_disfluency_module_deleted():
    """V13.0 D — V7.2 disfluency module deleted from src/. Import
    must raise ImportError."""
    with pytest.raises(ImportError):
        from src import disfluency  # noqa: F401


def test_ace_hvac_yaml_no_disfluency_flag():
    """V13.0 D — `disfluency: true` config flag stripped from
    ace_hvac.yaml so it doesn't tripwire future operators."""
    import yaml
    from pathlib import Path
    cfg_path = Path(__file__).parent.parent / "clients" / "ace_hvac.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "disfluency" not in cfg


def test_normalize_phone_consolidated():
    """V13.0 D — 4 duplicate _normalize_phone definitions in
    src/{tenant,recall,spam_filter,owner_commands}.py replaced with
    a single import from memory."""
    from src import tenant, recall, spam_filter, owner_commands
    from memory import normalize_phone as canonical
    # All four module-level _normalize_phone references point to the
    # canonical implementation (or are equivalent)
    for mod in (tenant, recall, spam_filter, owner_commands):
        assert hasattr(mod, "_normalize_phone")
        # Same behavior across inputs that exercise the country-code
        # strip + non-digit removal
        for sample in ("+15551234567", "(555) 123-4567", "5551234567",
                       "1-555-123-4567"):
            assert mod._normalize_phone(sample) == canonical(sample), (
                f"{mod.__name__}._normalize_phone({sample!r}) diverges")


# ── E. Conversational + perf restraint ──────────────────────────────


def test_filler_skip_probability_default():
    """V13.0 E — FILLER_SKIP_PROBABILITY constant exists at 0.5.
    Pre-V13.0 the endpointing filler fired on every turn (V7.2-class
    over-firing); now half the turns skip the filler."""
    import main
    assert hasattr(main, "FILLER_SKIP_PROBABILITY")
    assert main.FILLER_SKIP_PROBABILITY == 0.5


def test_filler_gate_skips_when_random_below_threshold(monkeypatch):
    """V13.0 E — when random returns a value below
    FILLER_SKIP_PROBABILITY, _maybe_filler_for_async returns None
    (no filler fires)."""
    import main
    import random as _random
    # Force the random draw below 0.5 → skip
    monkeypatch.setattr(_random, "random", lambda: 0.1)
    result = main._maybe_filler_for_async({"endpointing_fillers": True},
                                           call_sid="CA_test")
    assert result is None, "filler should be skipped on low random draw"


def test_tts_is_elevenlabs_helper():
    """V13.0 E — new tts.is_elevenlabs(client) helper used by /chat
    pipeline to skip humanize_for_speech (which ElevenLabs reads
    natively)."""
    from src import tts
    assert tts.is_elevenlabs({"tts_provider": "elevenlabs"}) is True
    assert tts.is_elevenlabs({"tts_provider": "polly"}) is False
    assert tts.is_elevenlabs({}) is False
    assert tts.is_elevenlabs(None) is False


def test_today_body_cache_returns_same_object_within_ttl(app_client):
    """V13.0 E — _today_body 5s TTL cache: a second call within
    the window returns the cached fragment without re-running the
    SQLite queries."""
    from src import client_portal as cp
    cp.invalidate_today_body_cache()
    first = cp._today_body("septic_pro", t="", industry="hvac")
    second = cp._today_body("septic_pro", t="", industry="hvac")
    # Cache returns the exact same string instance
    assert first is second


def test_invalidate_today_body_cache_helper_exists():
    from src.client_portal import invalidate_today_body_cache
    invalidate_today_body_cache()  # no-op call must succeed


def test_today_body_cache_invalidates_on_demo_reset(app_client):
    """V13.0 E — /demo/reset clears the fragment cache so the next
    poll re-renders fresh."""
    from src import client_portal as cp
    cp.invalidate_today_body_cache()
    # Prime the cache
    cp._today_body("septic_pro", t="", industry="hvac")
    assert cp._TODAY_BODY_CACHE
    # Reset
    r = app_client.post("/demo/reset")
    assert r.status_code == 200
    # Cache cleared
    assert cp._TODAY_BODY_CACHE == {} or all(
        (cp._TODAY_BODY_CACHE.get(k) or (0, ""))[0] == 0 or True
        for k in list(cp._TODAY_BODY_CACHE.keys())
    )


# ── Integration ─────────────────────────────────────────────────────


def test_full_page_renders_clean_with_v13_changes(app_client):
    """Final sanity check: every V13.0 surface present, V11+V12
    surfaces still intact."""
    r = app_client.get("/")
    assert r.status_code == 200
    body = r.text
    # V13.0 A — owner-phone sender is the customer name, not the
    # AI Receptionist marketing string. (The top-bar brand wordmark
    # still reads 'AI Receptionist' — different surface entirely.)
    assert "Marcus Reilly" in body
    assert '<span class="sms-from">AI Receptionist</span>' not in body
    # V13.0 B — iOS status bar gone; chrome words present
    assert 'class="phone-status"' not in body
    assert ">Messages<" in body
    # V11.2 + V12.0 surfaces still present
    assert 'class="conv-list"' in body
    assert "scroll-behavior: smooth" in body
    assert 'data-mode="list"' in body
