"""V3.10 — white-label branding tests."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import client_portal, design


# ── hex soft tint ─────────────────────────────────────────────────────

def test_hex_soft_valid_hex():
    # Black → soft ~ light gray
    soft = design._hex_soft("#000000")
    assert soft.startswith("#")
    assert soft != "#000000"
    # The soft mix of black should be light
    assert soft > "#aaaaaa"  # lexicographic works for hex at this range


def test_hex_soft_white_stays_white():
    # White mixed with white stays white (or close)
    soft = design._hex_soft("#ffffff")
    assert soft == "#ffffff"


def test_hex_soft_invalid_returns_input():
    # Bogus hex → pass through
    assert design._hex_soft("not-a-hex") == "not-a-hex"
    assert design._hex_soft("#xyz123") == "#xyz123"


# ── page renders with custom accent ────────────────────────────────────

def test_page_with_custom_accent_injects_style():
    out = design.page(
        title="x", body="<p>hi</p>",
        custom_accent_hex="#ff6600",
    )
    assert 'body[data-accent="custom"]' in out
    assert "--accent: #ff6600" in out
    assert 'data-accent="custom"' in out


def test_page_with_invalid_hex_falls_back():
    out = design.page(
        title="x", body="<p>hi</p>",
        accent="client",
        custom_accent_hex="bogus",
    )
    # No custom accent injection
    assert 'body[data-accent="custom"]' not in out
    assert 'data-accent="client"' in out


def test_page_rejects_css_injection_via_hex():
    """A malicious value like '#ff0000; } body { display: none; }' must
    not inject a custom-accent CSS block (regex requires exact #rrggbb)."""
    bad = "#ff0000; } body { display: none; }"
    out = design.page(
        title="x", body="<p>hi</p>",
        custom_accent_hex=bad,
    )
    # Should be rejected as invalid hex → no custom-accent style block
    assert 'body[data-accent="custom"]' not in out
    assert 'data-accent="custom"' not in out
    # And the malicious payload itself should not appear anywhere
    assert bad not in out


def test_page_with_brand_logo():
    out = design.page(
        title="x", body="<p>hi</p>",
        brand="Bob",
        brand_logo_url="https://example.com/logo.png",
    )
    assert "https://example.com/logo.png" in out
    assert '<img src="https://example.com/logo.png"' in out


def test_page_escapes_logo_url():
    """Untrusted logo URLs must not inject HTML."""
    out = design.page(
        title="x", body="<p>hi</p>",
        brand_logo_url='"><script>alert(1)</script>',
    )
    assert "<script>alert(1)</script>" not in out


def test_page_escapes_brand_name():
    out = design.page(
        title="x", body="<p>hi</p>",
        brand='<script>alert(1)</script>',
    )
    assert "<script>alert(1)</script>" not in out


# ── client portal surfaces branding ──────────────────────────────────

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


def test_portal_applies_custom_accent(app_client, monkeypatch):
    from src import tenant
    tenant.reload()
    ace = tenant.load_client_by_id("ace_hvac")
    monkeypatch.setitem(ace, "brand_accent_color", "#ff6600")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert r.status_code == 200
    assert "--accent: #ff6600" in r.text


def test_portal_applies_brand_display_name(app_client, monkeypatch):
    from src import tenant
    tenant.reload()
    ace = tenant.load_client_by_id("ace_hvac")
    monkeypatch.setitem(ace, "brand_display_name", "Custom Brand Name")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert "Custom Brand Name" in r.text


def test_portal_applies_brand_logo(app_client, monkeypatch):
    from src import tenant
    tenant.reload()
    ace = tenant.load_client_by_id("ace_hvac")
    monkeypatch.setitem(ace, "brand_logo_url", "https://cdn.example.com/logo.svg")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert "https://cdn.example.com/logo.svg" in r.text


def test_portal_no_branding_fallback(app_client):
    """Without any brand fields set, portal still works with defaults."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert r.status_code == 200
    # No custom-accent hex override
    assert "--accent: #" not in r.text or 'data-accent="custom"' not in r.text
