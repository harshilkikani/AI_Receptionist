"""V11.1 — refinement and production-hardening tests.

Covers all four production code commits:
  A: notification_label per industry + duplicate-notification dedup
  B: portal partner-card name resolution + unified avatars
  C: iMessage-style owner-phone bubbles with customer avatars
  D: brand mark + drawer modernization + typography pass

V11.0 shipped six commits with 1577 tests passing. V11.1 adds
regression guards for the issues the user surfaced after V11.0
went live.
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


# ── A. Notification labels ──────────────────────────────────────────


def test_notification_label_for_every_industry():
    """Per user direction ('Owner / Manager for all'), every vertical
    has either 'Owner' or 'Manager' — nothing else."""
    for slug in ALL_SLUGS:
        label = industries.notification_label(slug)
        assert label in ("Owner", "Manager"), (
            f"{slug}: unexpected notification_label {label!r}")


def test_property_management_uses_manager():
    """The one Manager vertical."""
    assert industries.notification_label("property_management") == "Manager"


def test_all_other_industries_use_owner():
    """Everything else is Owner."""
    for slug in ALL_SLUGS:
        if slug == "property_management":
            continue
        assert industries.notification_label(slug) == "Owner", (
            f"{slug} should be Owner, got {industries.notification_label(slug)!r}")


def test_notification_label_default_for_unknown_slug():
    assert industries.notification_label("nope") == "Owner"
    assert industries.notification_label("") == "Owner"
    assert industries.notification_label("nope", default="X") == "X"


def test_owner_phone_bar_uses_notification_label(app_client):
    """V11.1 — owner phone bar reads 'Owner notifications', not
    'Mike's phone' or 'Lauren's phone'."""
    r = app_client.get("/")
    body = r.text
    assert "Owner notifications" in body
    # Pre-V11.1 personal-demo labels retired
    assert "Mike's phone" not in body
    assert "Lauren's phone" not in body
    assert "Bob's phone" not in body or body.count("Bob's phone") == 0


def test_switcher_options_carry_notif_label(app_client):
    """Industry switcher options carry data-notif-label so the JS
    can swap the owner-phone label on industry change."""
    r = app_client.get("/")
    body = r.text
    assert 'data-notif-label="Owner"' in body
    assert 'data-notif-label="Manager"' in body


# ── A. Duplicate notification dedup ─────────────────────────────────


def test_owner_alert_dedup_state_initialized_in_js(app_client):
    """V11.1 — pushOwnerSMS gates on a (caller_id, intent_category)
    Set persisted in sessionStorage. Verify the JS structure is
    present so the dedup actually fires."""
    r = app_client.get("/")
    body = r.text
    # Constants and helpers
    assert "OWNER_DEDUP_KEY" in body
    assert "_ownerDedupSet" in body
    assert "_ownerDedupAdd" in body
    # Early return on duplicate
    assert "if (dedup.has(dedupKey)) return" in body
    # Persisted to sessionStorage
    assert "sessionStorage.setItem(OWNER_DEDUP_KEY" in body


def test_owner_alert_dedup_cleared_on_demo_reset(app_client):
    """The Reset Demo button clears the dedup so a fresh session
    starts clean. Without this, a prior demo session's bookings
    would block new ones from firing."""
    r = app_client.get("/")
    body = r.text
    assert "window.clearOwnerAlertDedup" in body
    # The reset handler clears
    assert "clearOwnerAlertDedup()" in body


def test_owner_alert_dedup_cleared_on_industry_switch(app_client):
    """Switching industry should also clear so the new vertical's
    personas can fire alerts cleanly."""
    r = app_client.get("/")
    body = r.text
    # reloadDemoCallers calls clearOwnerAlertDedup before loading
    assert "clearOwnerAlertDedup" in body
    # Both Reset Demo and reloadDemoCallers invoke the helper —
    # two callers is the minimum.
    assert body.count("clearOwnerAlertDedup()") >= 2


# ── B. Portal partner-card name resolution ──────────────────────────


def test_partner_label_resolves_by_phone_scan():
    """V11.1 — pre-V11.1 _partner_label looked up memory by phone-
    digit caller_id, but memory.json is keyed by caller_id strings
    (e.g. 're_caleb'). Result: portal cards rendered '(555) 010-3001'
    instead of 'Caleb Morrison'. Fixed by scanning all callers and
    matching by phone field."""
    from src.client_portal import _partner_label
    from src import demo_seed
    # Make sure the personas exist in memory.json
    demo_seed.register_personas_in_memory(industry="all")
    # Real estate persona lookup
    assert _partner_label("+15550103001") == "Caleb Morrison"
    assert _partner_label("+15550103005") == "Jordan Bailey"
    # HVAC persona lookup
    assert _partner_label("+15550102001") == "Marcus Reilly"
    # Unknown phone — formatted phone fallback
    label = _partner_label("+19995551234")
    assert "999" in label or label == "Unknown caller"


def test_portal_today_shows_real_names_not_phone_digits(app_client):
    """End-to-end: /demo/today?industry=real_estate returns cards
    with persona names, not raw phone digits."""
    from src import demo_seed
    demo_seed.seed_septic_pro()
    r = app_client.get("/demo/today?industry=real_estate")
    body = r.text
    assert "Caleb Morrison" in body or "Jordan Bailey" in body
    # The portal still references the phone number internally (in the
    # `from` field) but the headline label must be a name.
    # Pull out the .who divs and check none are "(555) 010-3001"
    import re
    who_labels = re.findall(r'<div class="who">([^<]+)</div>', body)
    # All who labels should contain letters (a name), not be pure
    # phone-format strings
    for label in who_labels:
        # Allow "Unknown caller" or names; reject pure phone formats
        assert not re.match(r"^\(\d{3}\) \d{3}-\d{4}$", label), (
            f"portal card showed raw phone instead of name: {label!r}")


# ── B. Avatar unification ───────────────────────────────────────────


def test_chat_caller_chip_uses_pravatar(app_client):
    """V11.1 — chat caller chips switched from DiceBear notionists
    (cartoonish) to Pravatar (real photos) so the same person looks
    identical in the chat chip and the portal card."""
    r = app_client.get("/")
    body = r.text
    # Pravatar URL is in the loadCallers JS (primary src)
    assert "i.pravatar.cc/150?u=" in body
    # DiceBear is still used as a fallback via onerror chain
    assert "api.dicebear.com" in body


def test_owner_phone_bubble_has_customer_avatar(app_client):
    """V11.1 — each owner-phone alert carries a small avatar of the
    customer the alert is about, so the notification visually belongs
    to a person rather than a generic 'AI Receptionist' sender."""
    r = app_client.get("/")
    body = r.text
    # New bubble structure
    assert 'class="sms-av"' in body
    assert 'class="sms-head"' in body
    assert 'class="sms-body"' in body
    # Avatar URL — HVAC seeded emergency uses Marcus's phone digits
    assert "i.pravatar.cc/150?u=5550102001" in body  # Marcus


# ── C. iMessage-style bubble structure ──────────────────────────────


def test_owner_bubble_renders_avatar_sender_timestamp_in_head(app_client):
    """The sms-head row contains avatar, sender, and timestamp on
    one flex row (iMessage pattern), not the pre-V11.1 stacked
    'sender / body / timestamp' vertical layout."""
    r = app_client.get("/")
    body = r.text
    # Inside a sms-head there should be both an sms-av and an sms-from
    import re
    head_matches = re.findall(
        r'<div class="sms-head">(.*?)</div>',
        body, re.DOTALL)
    assert head_matches, "no sms-head elements found"
    for head in head_matches:
        assert "sms-av" in head or "sms-from" in head
        # sms-from + sms-ts should both appear in the head row
        if "sms-from" in head:
            assert "sms-ts" in head, (
                "sms-from and sms-ts should both live in sms-head")


def test_owner_bubble_read_receipt_is_glyph_only(app_client):
    """V11.1 — Read receipt is a single muted check glyph; no
    'Read' word. font-size: 0 on the container hides any text
    content if present."""
    from src import design
    css = design.css()
    # The CSS rule sets font-size: 0 to suppress text
    assert ".owner-sms .sms-read" in css
    # Find the rule and verify font-size: 0 is present
    idx = css.find(".owner-sms .sms-read {")
    chunk = css[idx:idx + 250]
    assert "font-size: 0" in chunk


def test_seeded_owner_sms_carries_customer_phone_for_each_industry():
    """V11.1 C — every industry's seeded_owner_sms entries carry a
    customer_phone field so the rendering can derive avatar URLs
    without parsing the body string."""
    for slug in ALL_SLUGS:
        seeded = industries.seeded_owner_sms(slug)
        assert len(seeded) >= 2, f"{slug}: needs at least 2 seeded bubbles"
        for entry in seeded:
            assert "customer_phone" in entry, (
                f"{slug}: seeded SMS entry missing customer_phone")
            assert entry["customer_phone"].startswith("+1555"), (
                f"{slug}: customer_phone not in NANP-fictional range "
                f"({entry['customer_phone']!r})")
            assert "customer_name" in entry, (
                f"{slug}: seeded SMS entry missing customer_name")


# ── D. Brand mark ───────────────────────────────────────────────────


def test_brand_mark_uses_svg_glyph_not_plain_dot(app_client):
    """V11.1 — the top-left 'AI Receptionist · dot' replaced with an
    SVG glyph (speech bubble + three dots). Pre-V11.1 .dot is now
    display:none, kept only as a no-op for any historical paths."""
    r = app_client.get("/")
    body = r.text
    # New mark element present
    assert 'class="brand-mark"' in body
    assert 'class="brand-word"' in body
    # SVG inside brand-mark
    assert "viewBox=\"0 0 24 24\"" in body or "viewBox='0 0 24 24'" in body


def test_brand_mark_css_styled_with_accent_tinted_background(app_client):
    """The mark sits in an accent-tinted rounded square (premium SaaS
    pattern). Verify the CSS hooks are present."""
    from src import design
    css = design.css()
    assert ".demo-brand .brand-mark" in css
    assert ".demo-brand .brand-word" in css
    # Subtle gradient on the wordmark
    assert "background-clip: text" in css


def test_legacy_dot_hidden(app_client):
    """The pre-V11.1 `.dot` element is kept as a no-op (display:none)
    so any historical markup paths don't break."""
    from src import design
    css = design.css()
    # The .dot rule sets display:none in V11.1
    idx = css.find(".demo-brand .dot")
    chunk = css[idx:idx + 250]
    assert "display: none" in chunk


# ── D. Drawer modernization ─────────────────────────────────────────


def test_drawer_title_uses_demo_controls(app_client):
    """V11.1 — drawer title is 'Demo controls' (action-oriented), not
    'Demo settings' (generic)."""
    r = app_client.get("/")
    body = r.text
    assert "Demo controls" in body
    assert ">Demo settings<" not in body  # pre-V11.1 title gone


def test_drawer_has_icon_mark_in_header(app_client):
    """V11.1 — drawer header carries a small clock-glyph in an
    accent-tinted square, matching the brand-mark visual register."""
    r = app_client.get("/")
    body = r.text
    assert "demo-drawer-mark" in body


def test_drawer_industry_row_has_hint(app_client):
    """V11.1 — Industry row gets a quiet one-line hint ('Pick the
    vertical you want the demo tuned to') for first-time clarity."""
    r = app_client.get("/")
    body = r.text
    assert 'class="dd-hint"' in body
    assert "Pick the vertical" in body


def test_drawer_has_divider_between_industry_and_session(app_client):
    """V11.1 — visual divider between the two drawer sections."""
    r = app_client.get("/")
    body = r.text
    assert 'class="dd-divider"' in body


def test_drawer_session_label_introduces_action_buttons(app_client):
    """V11.1 — Pause/Reset buttons get a 'Session' label for context."""
    r = app_client.get("/")
    body = r.text
    assert "Session</label>" in body or ">Session<" in body


def test_drawer_buttons_have_v11_1_radius(app_client):
    """V11.1 — drawer buttons go 8px → 9px radius and pick up a
    subtle active-state scale."""
    from src import design
    css = design.css()
    idx = css.find(".demo-drawer .dd-btn {")
    chunk = css[idx:idx + 400]
    assert "border-radius: 9px" in chunk
    # Active-state scale touch
    assert "transform: scale" in chunk or ":active" in css


# ── D. Typography ───────────────────────────────────────────────────


def test_section_caption_typography_refined():
    """V11.1 — section captions at 11.5px, tighter letter-spacing.
    Less prominent — quiets the eye between sections."""
    from src import design
    css = design.css()
    idx = css.find(".section-caption {")
    chunk = css[idx:idx + 250]
    assert "font-size: 11.5px" in chunk
    assert "0.07em" in chunk or "letter-spacing: 0.07" in chunk


def test_stat_label_typography_refined():
    """V11.1 — stat labels at 12.5px with tabular-nums on values."""
    from src import design
    css = design.css()
    idx_label = css.find(".stat .label {")
    chunk = css[idx_label:idx_label + 200]
    assert "font-size: 12.5px" in chunk
    idx_value = css.find(".stat .value {")
    chunk_v = css[idx_value:idx_value + 250]
    assert "tabular-nums" in chunk_v


# ── E. Integration / cross-commit sanity ────────────────────────────


def test_full_demo_page_renders_with_v11_1_changes(app_client):
    """Final integration: page renders 200, has the key V11.1
    surfaces present, no regressions on V11.0 structure."""
    r = app_client.get("/")
    assert r.status_code == 200
    body = r.text
    # V11.1 surfaces
    assert "Owner notifications" in body
    assert "sms-head" in body
    assert "sms-av" in body
    assert "brand-mark" in body
    assert "Demo controls" in body
    # V11.0 still intact
    assert 'value="hvac"' in body
    assert 'value="real_estate"' in body
    assert "data-suggestions=" in body


def test_demo_today_with_industry_still_works_after_v11_1(app_client):
    """V11.0 F portal-filter survives V11.1 changes."""
    from src import demo_seed
    demo_seed.seed_septic_pro()
    r = app_client.get("/demo/today?industry=real_estate")
    assert r.status_code == 200
    body = r.text
    import re
    phones = set(re.findall(r"\+1555010[0-9]{4}", body))
    if phones:
        for p in phones:
            assert p.startswith("+155501030"), (
                f"non-real-estate phone in industry-filtered portal: {p}")


def test_v11_personas_v11_1_continuity(app_client):
    """V11.0 personas + V11.1 customer_phone wiring → unified
    avatar across chat chip, portal card, owner-phone bubble for
    the same persona."""
    r = app_client.get("/")
    body = r.text
    # HVAC default: Marcus Reilly is the canonical seeded emergency
    # His phone is +15550102001 → seed '5550102001'
    # Pravatar URL with that seed should appear in body
    assert "i.pravatar.cc/150?u=5550102001" in body  # Marcus's avatar
