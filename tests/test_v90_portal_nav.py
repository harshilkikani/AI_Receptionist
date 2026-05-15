"""V9.0 — portal navigation + new pages.

After the V9.0 restructure the portal sidebar shows Today / Recent
calls / Follow-ups / Settings instead of Overview / Call log /
Invoice. Invoice is still reachable at its URL but no longer in nav.
This file covers the routing, copy, and the new pages.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import client_portal, usage


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


# ── nav restructure ───────────────────────────────────────────────────

def test_today_page_loads(app_client):
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert r.status_code == 200
    assert "Today" in r.text


def test_nav_includes_today_calls_followups_settings(app_client):
    """V9.1 — nav consolidated to Today / Conversations / Settings.
    Recent calls folded into Conversations; Follow-ups moved into Today
    as a section."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    assert ">Today<" in body
    assert ">Conversations<" in body
    assert ">Settings<" in body
    # V9.1 — Recent calls + Follow-ups no longer primary nav items
    assert ">Recent calls<" not in body
    assert ">Follow-ups<" not in body


def test_nav_does_not_include_invoice(app_client):
    """Invoice still works at its URL but isn't in the primary nav."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    # Should be no nav link literally labeled "Invoice"
    assert ">Invoice<" not in body


def test_invoice_url_still_reachable(app_client):
    """V9.0 doesn't break bookmarked invoice URLs."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/invoice/2026-05?t={tok}")
    assert r.status_code == 200


def test_today_page_links_to_current_invoice(app_client):
    """Invoice should be discoverable from Today even though it's not
    in the sidebar."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert "/invoice/" in r.text


# ── status vocabulary ─────────────────────────────────────────────────

def test_calls_page_uses_plain_english_status(app_client):
    """V9.0 contract: engineer-y outcome strings never leak.
    V9.1 — /calls now renders the per-partner Conversations list; one
    card per phone number. Spam calls don't get a card (they're filtered
    server-side from the partner roll-up), so we just verify no raw
    engineer strings appear anywhere."""
    usage.start_call("CA_v90_1", "ace_hvac", "+14155550199", "+18449403274")
    usage.end_call("CA_v90_1", outcome="normal")
    usage.start_call("CA_v90_2", "ace_hvac", "+14155550100", "+18449403274")
    usage.end_call("CA_v90_2", outcome="spam_phrase")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/calls?t={tok}")
    body = r.text
    assert r.status_code == 200
    # Plain English status shows up
    assert "Answered" in body
    # Engineer strings do NOT
    assert "spam_phrase" not in body
    assert "spam_number" not in body
    assert "duration_capped" not in body
    assert "no_answer" not in body or "No answer" in body


def test_calls_page_does_not_mention_ai(app_client):
    """V9.0 — the customer-facing portal should not advertise 'AI'.
    The receptionist is just 'the receptionist'.
    V10.3 — strip <style>/<script> blocks before checking (they hold
    historical comments referencing "the AI" that aren't user-visible)."""
    import re
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/calls?t={tok}")
    visible = re.sub(r"<(style|script)[^>]*>.*?</\1>", "",
                       r.text, flags=re.DOTALL | re.IGNORECASE)
    body = visible.lower()
    assert "the ai " not in body
    assert "ai hasn't" not in body


# ── follow-ups page ───────────────────────────────────────────────────

def test_followups_page_loads_empty(app_client):
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/followups?t={tok}")
    assert r.status_code == 200
    # Empty state copy
    assert "No follow-ups" in r.text or "handled cleanly" in r.text


def test_followups_lists_short_call_with_caller(app_client):
    """A short call (< 25s, not emergency, not spam) should surface as
    a follow-up candidate."""
    import time
    usage.start_call("CA_v90_short", "ace_hvac",
                     "+14155551234", "+18449403274")
    usage.end_call("CA_v90_short", outcome="normal")
    # The seeded call has duration_s = 0 by default → counts as short
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/followups?t={tok}")
    assert r.status_code == 200
    assert "+14155551234" in r.text


def test_followups_excludes_emergency(app_client):
    """Emergencies are already routed to the operator's cell — they're
    not a follow-up candidate."""
    import time
    from src.usage import _connect, _init_schema, _db_lock
    usage.start_call("CA_v90_em", "ace_hvac",
                     "+15550001000", "+18449403274")
    usage.end_call("CA_v90_em", outcome="emergency_transfer")
    # Flag emergency in DB
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        conn.execute("UPDATE calls SET emergency=1 WHERE call_sid=?",
                     ("CA_v90_em",))
        conn.commit()
        conn.close()
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/followups?t={tok}")
    body = r.text
    assert r.status_code == 200
    assert "+15550001000" not in body


def test_followups_excludes_spam(app_client):
    """Spam calls aren't worth following up on."""
    usage.start_call("CA_v90_spam", "ace_hvac",
                     "+15550002000", "+18449403274")
    usage.end_call("CA_v90_spam", outcome="spam_phrase")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/followups?t={tok}")
    body = r.text
    assert "+15550002000" not in body


# ── settings page ─────────────────────────────────────────────────────

def test_settings_page_loads(app_client):
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/settings?t={tok}")
    assert r.status_code == 200
    assert "Settings" in r.text


def test_settings_shows_business_name_and_hours(app_client):
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/settings?t={tok}")
    body = r.text
    # Tenant config has the business name
    assert "Ace HVAC" in body
    # Should display the Hours field label
    assert "Hours" in body
    # Should display transfer number heading
    assert "Transfer number" in body


def test_settings_page_is_not_editable(app_client):
    """V9.0 — opinionated defaults. No editable form fields, just a
    contact note pointing to the operator."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/settings?t={tok}")
    body = r.text
    # No <form> element on this page
    assert "<form" not in body
    # Should explain how to change settings instead
    assert "welcome email" in body.lower() or "contact" in body.lower()


def test_settings_unauthorized(app_client):
    """Settings inherits the same auth as the rest of the portal."""
    r = app_client.get("/client/ace_hvac/settings?t=garbage.token")
    assert r.status_code == 403


def test_followups_unauthorized(app_client):
    r = app_client.get("/client/ace_hvac/followups?t=garbage.token")
    assert r.status_code == 403


# ── duration formatter ────────────────────────────────────────────────

@pytest.mark.parametrize("seconds, expected", [
    (0,    "0s"),
    (45,   "45s"),
    (59,   "59s"),
    (60,   "1m 00s"),
    (72,   "1m 12s"),
    (125,  "2m 05s"),
    (723,  "12m 03s"),
    (3600, "60m 00s"),
])
def test_fmt_duration(seconds, expected):
    assert client_portal._fmt_duration(seconds) == expected


def test_fmt_duration_handles_none():
    assert client_portal._fmt_duration(None) == "0s"
