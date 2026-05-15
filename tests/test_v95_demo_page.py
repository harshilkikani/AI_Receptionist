"""V9.5 — combined demo at /.

The public-facing URL now shows the customer demo (left) and the
operator portal preview (right) side-by-side, using the same design
system as the real portal. No marketing copy. The product IS the demo.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import design


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


# ── layout shell ─────────────────────────────────────────────────────

def test_root_is_html_with_demo_stage(app_client):
    r = app_client.get("/")
    assert r.status_code == 200
    body = r.text
    assert 'class="demo-stage"' in body


def test_root_uses_design_system_palette(app_client):
    """V9.5 — the demo and portal share one visual system. Verify the
    slate-blue accent token is present in the inlined CSS."""
    r = app_client.get("/")
    assert "#1e3a8a" in r.text


def test_root_top_bar_has_brand_and_phone(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'class="demo-brand"' in body
    assert 'class="demo-phone-link"' in body
    assert "AI Receptionist" in body
    assert "+1 (844) 940-3274" in body
    assert 'href="tel:+18449403274"' in body


def test_root_split_into_two_panes(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'class="demo-pane demo-pane-customer"' in body
    assert 'class="demo-pane demo-pane-operator"' in body


def test_pane_labels_say_what_you_expect(app_client):
    r = app_client.get("/")
    body = r.text
    assert "What your customer sees" in body
    assert "What you see" in body


# ── customer pane (phone shell) ──────────────────────────────────────

def test_customer_pane_has_phone_shell(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'class="phone-shell"' in body
    assert 'class="phone-bar"' in body
    assert 'class="phone-screen"' in body


def test_customer_pane_includes_chat_widget(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'id="conv-body"' in body
    assert 'id="conv-input"' in body
    assert 'id="conv-form"' in body
    assert 'id="callers"' in body


def test_customer_pane_has_quick_suggestions(app_client):
    """V9.5: customer pane ships scripted demo prompts.

    V11.0: chip labels are industry-specific. The default render is
    HVAC, whose chips read 'AC out', 'No heat', 'Tune-up', + 'Wrong
    number' (the generic-final chip kept across industries). Assert
    on a shape that holds regardless of the default vertical: at
    least four phone-suggestion buttons plus the always-present
    'Wrong number' fallback."""
    r = app_client.get("/")
    body = r.text
    # At least 3 industry-specific chips + the generic Wrong number
    assert body.count('class="phone-suggestion"') >= 4
    # The wrong-number fallback survives across every industry
    assert "Wrong number" in body


def test_customer_pane_chat_widget_targets_septic_pro(app_client):
    """The demo uses the septic_pro marketing tenant, never ace_hvac."""
    r = app_client.get("/")
    assert 'DEMO_CLIENT_ID = "septic_pro"' in r.text


# ── operator pane (portal shell) ─────────────────────────────────────

def test_operator_pane_has_portal_shell(app_client):
    """V10.5 — the V9.5 .window-bar (red/amber/green fake browser
    dots) was removed for restraint. The portal-shell now opens
    straight into the body content."""
    r = app_client.get("/")
    body = r.text
    assert 'class="portal-shell"' in body
    assert 'class="portal-shell-body"' in body
    assert 'class="window-bar"' not in body


def test_operator_pane_renders_seeded_portal_content(app_client):
    """V9.5 contract: the portal pane embeds the REAL Today body from
    client_portal._today_body — not a static mockup. Verify the seeded
    septic_pro partners surface here."""
    r = app_client.get("/")
    body = r.text
    # At least one V9.4 portal pattern must be present
    assert 'class="today-hero"' in body
    assert 'class="today-headline"' in body
    assert 'class="section-caption"' in body


def test_operator_pane_uses_same_design_tokens_as_real_portal(app_client):
    """V9.5 — same design system on both surfaces. Verify some V9.4
    portal-only patterns are present (proves we're embedding the real
    components, not duplicating them)."""
    r = app_client.get("/")
    body = r.text
    # call card pattern from V9.0 / V9.3 (color-hashed avatar)
    assert "--av-h:" in body


def test_operator_pane_invoice_link_is_hidden_on_demo(app_client):
    """V9.5 — the invoice button is portal-only chrome. The demo page
    passes include_invoice_link=False so prospects don't see a
    'view invoice' affordance that goes nowhere."""
    r = app_client.get("/")
    body = r.text
    # The invoice button's distinctive marker:
    # `<a href="/client/.../invoice/...` should NOT appear in the demo.
    # (The portal preview is the embedded _today_body for septic_pro
    # with include_invoice_link=False.)
    assert "/invoice/" not in body


def test_operator_pane_call_cards_have_no_drill_in_links(app_client):
    """V9.5 — the operator-pane preview is showcase-only. The call cards
    must not have href= (which would jump to /client/.../conversations
    with no token and 403)."""
    r = app_client.get("/")
    body = r.text
    # Find the operator pane region and confirm there's no
    # /conversations/ link inside it.
    op_idx = body.find('class="demo-pane demo-pane-operator"')
    assert op_idx > -1
    op_chunk = body[op_idx:op_idx + 15000]
    assert "/client/septic_pro/conversations" not in op_chunk


# ── marketing strip ──────────────────────────────────────────────────

def test_root_has_zero_marketing_copy(app_client):
    """V9.5 explicit goal: no marketing on the demo page."""
    r = app_client.get("/")
    body = r.text
    # V9.0 marketing strings that should be gone
    assert "Never miss" not in body
    assert "How it works" not in body
    assert "Talk to us" not in body
    assert "Try the live demo" not in body
    assert "Let's talk" not in body
    # No contact form
    assert 'id="contact-form"' not in body
    assert 'name="business"' not in body


def test_root_has_no_features_section(app_client):
    r = app_client.get("/")
    body = r.text
    # V9.0 had a "features" section with 3 columns
    assert 'class="features"' not in body
    assert 'class="feature"' not in body


def test_root_has_no_footer_with_admin_link(app_client):
    """V9.0 footer linked to /admin and showed a copyright. V9.5 drops
    the footer entirely — the demo is the page."""
    r = app_client.get("/")
    body = r.text
    assert "© 2026 AI Receptionist" not in body


# ── real portal still works (un-regressed) ──────────────────────────

def test_real_portal_still_renders_today(app_client, monkeypatch):
    """V9.5 extracted _today_body but the real /client/{id}?t=... portal
    must still render exactly as before."""
    from src import client_portal
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert r.status_code == 200
    body = r.text
    assert 'class="today-hero"' in body
    assert 'class="today-headline"' in body


def test_real_portal_today_includes_invoice_link(app_client):
    """V9.5 — the real portal still shows the invoice button (the demo
    page is what hides it via include_invoice_link=False)."""
    from src import client_portal
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert "/invoice/" in r.text


# ── demo_page helper ─────────────────────────────────────────────────

def test_demo_page_helper_renders_minimal_shell():
    out = design.demo_page(title="Demo", body="<div>hi</div>")
    assert "<!doctype html>" in out
    assert "<title>Demo</title>" in out
    assert "demo-top" in out
    assert "demo-brand" in out
    assert "<div>hi</div>" in out


def test_demo_page_helper_escapes_title():
    out = design.demo_page(title="<script>alert(1)</script>", body="")
    # User-injected `<script>` payload must not appear unescaped.
    assert "<script>alert(1)" not in out
    assert "&lt;script&gt;alert" in out


def test_demo_page_helper_renders_phone_number():
    out = design.demo_page(title="x", body="",
                            phone_number="+1 (555) 123-4567",
                            tel_href="tel:+15551234567")
    assert "+1 (555) 123-4567" in out
    assert "tel:+15551234567" in out


def test_demo_page_default_phone():
    out = design.demo_page(title="x", body="")
    assert "+1 (844) 940-3274" in out
