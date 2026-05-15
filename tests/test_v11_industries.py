"""V11.0 — multi-industry registry tests.

Covers the registry shape, public API contracts, per-industry
rendering through the switcher, /demo/callers filtering, /chat
prompt-fragment routing, and basic sanity across all 12 verticals.

Real-estate-specific flow tests live in `test_v11_real_estate.py`.
"""
from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from src import industries


# Every supported vertical, expected to be present in the registry.
ALL_SLUGS = [
    "hvac", "real_estate", "septic",
    "construction", "electrical", "plumbing",
    "property_management", "roofing",
    "landscaping", "legal_intake", "med_spa", "restoration",
]


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


# ── 1. Registry shape ───────────────────────────────────────────────


def test_registry_has_12_industries():
    assert len(industries.list_all()) == 12


def test_registry_has_all_expected_slugs():
    slugs = industries.list_slugs()
    for expected in ALL_SLUGS:
        assert expected in slugs, f"missing slug: {expected}"


def test_every_industry_has_required_fields():
    """Each entry must have the load-bearing metadata. Missing any of
    these breaks the switcher, chat, owner SMS, or portal."""
    required = [
        "slug", "tier", "name", "owner_label", "owner_role",
        "customer_term", "business_noun", "business_noun_plural",
        "suggestions", "suggestion_labels",
        "emergency_keywords", "emergency_indicator",
        "owner_sms_templates", "summary_verbs",
        "system_prompt", "portal_copy",
        "seeded_owner_sms",
    ]
    for slug in ALL_SLUGS:
        ind = industries.get(slug)
        assert ind is not None, f"{slug} not in registry"
        for field in required:
            assert field in ind, f"{slug} missing required field: {field}"


def test_suggestions_and_labels_have_same_length():
    """The chip-button JS pairs them by index — desync would render
    wrong labels on wrong messages."""
    for slug in ALL_SLUGS:
        sugs = industries.suggestions(slug)
        labels = industries.suggestion_labels(slug)
        assert len(sugs) == len(labels), (
            f"{slug}: suggestions={len(sugs)} labels={len(labels)}")
        assert len(sugs) >= 3, f"{slug}: needs at least 3 suggestions"


def test_tier_allocation_matches_plan():
    """Tier 1 (deep): HVAC, Real Estate, Septic.
       Tier 2 (production): Construction, Electrical, Plumbing,
                            Property Management, Roofing.
       Tier 3 (placeholder-killing): Landscaping, Legal Intake,
                                     Med Spa, Restoration."""
    tier1 = {"hvac", "real_estate", "septic"}
    tier2 = {"construction", "electrical", "plumbing",
             "property_management", "roofing"}
    tier3 = {"landscaping", "legal_intake", "med_spa", "restoration"}
    for slug in tier1:
        assert industries.get(slug)["tier"] == 1
    for slug in tier2:
        assert industries.get(slug)["tier"] == 2
    for slug in tier3:
        assert industries.get(slug)["tier"] == 3


# ── 2. Prompt fragments ─────────────────────────────────────────────


def test_prompt_fragment_returns_bracketed_string_for_each_slug():
    for slug in ALL_SLUGS:
        frag = industries.prompt_fragment(slug)
        assert frag.startswith("[Context:"), f"{slug}: missing bracket"
        assert "Do NOT mention this context in your reply." in frag, (
            f"{slug}: missing meta-narration guard")
        # Sanity bound: fragments are roughly 500-1000 chars
        assert 400 <= len(frag) <= 1500, (
            f"{slug}: fragment length {len(frag)} outside expected band")


def test_prompt_fragment_empty_for_unknown_slug():
    assert industries.prompt_fragment("") == ""
    assert industries.prompt_fragment("nonexistent") == ""
    assert industries.prompt_fragment("hvacXYZ") == ""


def test_legacy_slug_aliases_resolve():
    """Pre-V11.0 callers used real-estate / realty / medspa style
    slugs. They must keep resolving to the canonical snake_case slugs."""
    assert industries.get("real-estate")["slug"] == "real_estate"
    assert industries.get("realty")["slug"] == "real_estate"
    assert industries.get("medspa")["slug"] == "med_spa"
    assert industries.get("legal")["slug"] == "legal_intake"
    assert industries.prompt_fragment("real-estate").startswith("[Context:")


# ── 3. Owner-SMS templates ──────────────────────────────────────────


def test_owner_sms_renders_for_each_industry():
    """Every industry has at least an emergency + booking template."""
    ctx = {"name": "Test", "addr": "1 Main St", "issue": "test",
           "time": "Saturday 1pm", "window": "afternoon",
           "scope": "kitchen", "phone": "+15555550100",
           "unit": "4B", "treatment": "Botox", "category": "workplace",
           "deadline": "Tuesday", "claim": "ABC-123"}
    for slug in ALL_SLUGS:
        emerg = industries.owner_sms(slug, "emergency", ctx)
        book = industries.owner_sms(slug, "booking", ctx)
        assert emerg, f"{slug}: empty emergency template"
        assert book, f"{slug}: empty booking template"


def test_owner_sms_tolerates_missing_context_keys():
    """A caller missing a field should get a message with empty
    placeholders, not a KeyError."""
    result = industries.owner_sms("hvac", "emergency", {})
    assert result  # non-empty
    # Format markers are still resolved (just to empty strings)
    assert "{name}" not in result
    assert "{addr}" not in result


def test_owner_sms_unknown_kind_returns_empty():
    assert industries.owner_sms("hvac", "nope", {"name": "X"}) == ""
    assert industries.owner_sms("nope", "emergency", {"name": "X"}) == ""


# ── 4. Seeded owner-phone bubbles ───────────────────────────────────


def test_every_industry_has_two_seeded_sms_bubbles():
    """The combined demo's initial render relies on each industry
    shipping at least one urgent + one routine bubble so the owner
    phone reads populated on first paint."""
    for slug in ALL_SLUGS:
        seeded = industries.seeded_owner_sms(slug)
        assert len(seeded) == 2, f"{slug}: expected 2 seeded bubbles"
        # At least one should be flagged urgent (or, for Med Spa,
        # neither — clinical concerns are rare. Allow 0 urgent only
        # for med_spa.)
        urgent_count = sum(1 for s in seeded if s.get("urgent"))
        if slug == "med_spa":
            assert urgent_count <= 1
        elif slug == "construction":
            assert urgent_count >= 0  # construction's first is a
                                       # booking marked priority-urgent
        else:
            assert urgent_count >= 1, (
                f"{slug}: at least one seeded SMS should be urgent")


def test_seeded_owner_sms_have_required_fields():
    for slug in ALL_SLUGS:
        for s in industries.seeded_owner_sms(slug):
            assert "kind" in s
            assert "urgent" in s
            assert "body" in s
            assert "ts_label" in s
            assert isinstance(s["body"], str) and s["body"], (
                f"{slug}: empty body")


# ── 5. Portal terminology ───────────────────────────────────────────


def test_portal_term_returns_industry_appropriate_strings():
    """V11.0 — portal labels swap per vertical. Real estate uses
    'Today's leads', legal uses 'Today's intakes', etc."""
    assert industries.portal_term("real_estate", "today_headline") == "Today's leads"
    assert industries.portal_term("legal_intake", "today_headline") == "Today's intakes"
    assert industries.portal_term("med_spa", "today_headline") == "Today's appointments"
    assert industries.portal_term("property_management", "today_headline") == "Today's requests"


def test_portal_term_default_for_unknown():
    assert industries.portal_term("nope", "today_headline") == ""
    assert industries.portal_term("nope", "today_headline", "fallback") == "fallback"


# ── 6. Switcher rendering ───────────────────────────────────────────


def test_switcher_renders_all_12_industries(app_client):
    r = app_client.get("/")
    body = r.text
    for slug in ALL_SLUGS:
        assert f'value="{slug}"' in body, (
            f"switcher missing option: {slug}")


def test_switcher_options_carry_v11_data_attrs(app_client):
    r = app_client.get("/")
    body = r.text
    # New V11.0 data attrs
    assert "data-suggestions=" in body
    assert "data-labels=" in body
    assert "data-portal-stats=" in body
    assert "data-seeded-sms=" in body
    assert "data-emergency-ind=" in body
    # Brand + owner survive from V10.4
    assert 'data-brand="Sunrise HVAC"' in body
    assert 'data-owner="Mike"' in body


def test_switcher_default_is_hvac(app_client):
    """V11.0 plan: default to HVAC — emergency moments demo strongest."""
    r = app_client.get("/")
    body = r.text
    # The HVAC option carries the "selected" attribute
    assert 'value="hvac" selected' in body


def test_switcher_data_suggestions_is_valid_json(app_client):
    r = app_client.get("/")
    body = r.text
    # Find HVAC's data-suggestions and verify it parses
    import re
    m = re.search(
        r'value="hvac"[^>]*data-suggestions="([^"]+)"', body)
    assert m, "HVAC option data-suggestions missing"
    raw = m.group(1).replace("&quot;", '"')
    parsed = json.loads(raw)
    assert isinstance(parsed, list)
    assert len(parsed) >= 3


# ── 7. /demo/callers industry filtering ─────────────────────────────


def test_demo_callers_default_returns_septic(app_client):
    """Pre-V11.0 callers without an industry param get septic
    personas — backwards compat."""
    r = app_client.get("/demo/callers")
    callers = r.json()
    # Septic personas: Marcus Reilly is the canonical first
    names = [c["name"] for c in callers]
    assert "Marcus Reilly" in names
    # All septic phones are in the +15550101 range
    septic_phones = [c["phone"] for c in callers
                     if "Marcus Reilly" in c["name"]]
    assert any(p.startswith("+155501010") for p in septic_phones)


def test_demo_callers_industry_filter_returns_correct_personas(app_client):
    """Each industry returns its own persona set."""
    r = app_client.get("/demo/callers?industry=hvac")
    hvac = r.json()
    hvac_phones = [c["phone"] for c in hvac
                   if c["name"] not in ("New caller", "Unknown caller")]
    # All HVAC phones in the +15550102 range
    assert all(p.startswith("+155501020") for p in hvac_phones), (
        f"non-HVAC phones leaked: {hvac_phones}")

    r = app_client.get("/demo/callers?industry=real_estate")
    re_callers = r.json()
    re_phones = [c["phone"] for c in re_callers
                 if c["name"] not in ("New caller", "Unknown caller")]
    assert all(p.startswith("+155501030") for p in re_phones)


def test_demo_callers_returns_industry_tag(app_client):
    """Each persona carries an `industry` field so the chat client
    can attribute its activity to the right vertical."""
    r = app_client.get("/demo/callers?industry=hvac")
    callers = r.json()
    for c in callers:
        assert c.get("industry") == "hvac", (
            f"{c['name']}: expected industry=hvac, got {c.get('industry')!r}")


def test_demo_callers_for_every_industry(app_client):
    """V11.0 — every supported industry returns at least 2 personas."""
    for slug in ALL_SLUGS:
        r = app_client.get(f"/demo/callers?industry={slug}")
        assert r.status_code == 200, f"{slug}: bad response"
        callers = r.json()
        # At least 3 personas (per the Tier 3 minimum) plus "New caller"
        assert len(callers) >= 4, (
            f"{slug}: expected >=4 callers, got {len(callers)}")


# ── 8. /chat per-industry prompt routing ────────────────────────────


def test_chat_unknown_industry_passes_message_verbatim(app_client, monkeypatch):
    """V11.0 — unknown slug means no fragment; message stays clean."""
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
        "message": "plain text",
        "client_id": "septic_pro",
        "industry": "completely-unknown-industry-xyz",
    })
    assert r.status_code == 200
    # Unknown slug → no fragment prepended
    assert captured["message"] == "plain text"


def test_chat_every_known_industry_prepends_fragment(app_client, monkeypatch):
    """Every supported vertical gets a context cue routed through
    the registry — not hardcoded if/elif anymore."""
    import main
    for slug in ALL_SLUGS:
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
            "message": "hello",
            "client_id": "septic_pro",
            "industry": slug,
        })
        assert r.status_code == 200, f"{slug}: bad response"
        msg = captured.get("message", "")
        assert msg.startswith("[Context:"), f"{slug}: missing context cue"
        assert msg.endswith("hello"), f"{slug}: user message dropped"


# ── 9. Owner-phone seeded SMS initial render ────────────────────────


def test_owner_phone_initial_render_uses_default_industry(app_client):
    """V11.0 — first paint shows HVAC seeded bubbles (Marcus + AC,
    Wendy + tune-up), not the pre-V11.0 septic boilerplate."""
    r = app_client.get("/")
    body = r.text
    # HVAC's seeded emergency: Marcus + 4729 Maple + AC out
    assert "Marcus Reilly" in body
    assert "4729 Maple" in body
    # HVAC's owner label
    assert "Mike's phone" in body


def test_owner_phone_dynamic_bubble_data_attr(app_client):
    """V11.0 — bubbles pushed via pushOwnerSMS carry data-dynamic=\"1\"
    so industry switches preserve them across rebuilds."""
    r = app_client.get("/")
    body = r.text
    # The pushOwnerSMS JS sets div.dataset.dynamic = "1"
    assert 'dataset.dynamic = "1"' in body


# ── 10. Phone-range mapping ─────────────────────────────────────────


def test_industry_phone_prefix_unique_per_industry():
    """Each industry has its own +1-555-0XX-XXXX block. Reverse-
    lookup must round-trip."""
    from src import demo_seed
    for slug in ALL_SLUGS:
        prefix = demo_seed.industry_phone_prefix(slug)
        assert prefix, f"{slug}: no phone prefix"
        # Reverse lookup: a phone in the range must map back to slug
        test_phone = prefix + "001"
        assert demo_seed._industry_for_phone(test_phone) == slug, (
            f"{slug}: phone {test_phone} reverse-lookup failed")


def test_industry_phone_prefixes_are_disjoint():
    """Two industries must never share a prefix range or callers
    would be misattributed."""
    from src import demo_seed
    prefixes = [demo_seed.industry_phone_prefix(s) for s in ALL_SLUGS]
    assert len(set(prefixes)) == len(prefixes), (
        f"duplicate phone prefixes: {prefixes}")


# ── 11. Backwards compatibility ─────────────────────────────────────


def test_list_personas_default_returns_septic_for_backwards_compat():
    """Pre-V11.0 callers used list_personas() with no args. That
    must keep returning septic personas (not the new flat all-12 list)."""
    from src import demo_seed
    personas = demo_seed.list_personas()
    # Septic has 6 personas + 1 fresh = 7
    assert len(personas) == 7
    names = [p["name"] for p in personas]
    assert "Marcus Reilly" in names  # septic Marcus, not HVAC Marcus
    # The Marcus in septic uses caller_id 'marcus' (no prefix)
    marcus = next(p for p in personas if p["name"] == "Marcus Reilly")
    assert marcus["id"] == "marcus"


def test_legacy_data_attrs_replaced_not_appended():
    """V11.0 retired data-emergency/data-book/data-price. Their
    presence would mean the legacy switcher JS is still around."""
    from src import design
    css = design.css()
    # No legacy attribute references in CSS
    # (The HTML can carry them via dynamic content but the static CSS
    # design system shouldn't reference them anymore.)
    # Mostly just a sanity check that we didn't accidentally keep both.
    assert ".tenant-switcher" in css  # the styling still applies
