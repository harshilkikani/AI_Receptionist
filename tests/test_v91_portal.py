"""V9.1 — portal IA + Conversations view.

Today is a communications-first feed (recent activity + follow-ups +
quiet stats). Conversations is the per-partner thread list. The thread
detail shows calls + SMS unified, with chat-style bubbles.
"""
from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient

from src import client_portal, transcripts, usage


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


def _seed_voice_call(call_sid: str, client_id: str, phone: str,
                      summary_text: str = ""):
    """Helper — write a call row + two turns + optional summary."""
    usage.start_call(call_sid, client_id, phone, "+18449403274")
    transcripts.record_turn(call_sid, client_id, "user", "Hi, need help")
    transcripts.record_turn(call_sid, client_id, "assistant", "Sure, what's going on?")
    usage.end_call(call_sid, outcome="normal")
    if summary_text:
        # write summary directly via DB (the migration would normally add this column)
        from src import migrations
        try:
            migrations.apply()  # ensures the summary column exists
        except Exception:
            pass
        with usage._db_lock:
            conn = usage._connect()
            usage._init_schema(conn)
            try:
                conn.execute("UPDATE calls SET summary=? WHERE call_sid=?",
                             (summary_text, call_sid))
            except Exception:
                pass
            conn.close()


def _seed_sms(client_id: str, phone: str, inbound: str, reply: str,
               when: int = None):
    from memory import normalize_phone
    sid = f"SMS_{normalize_phone(phone)}"
    t0 = when or int(time.time())
    transcripts.record_turn(sid, client_id, "user", inbound, ts=t0)
    transcripts.record_turn(sid, client_id, "assistant", reply, ts=t0 + 1)
    usage.log_sms(sid, client_id, phone, inbound, direction="inbound")
    usage.log_sms(sid, client_id, phone, reply, direction="outbound")


# ── Nav restructure ──────────────────────────────────────────────────

def test_today_page_loads(app_client):
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert r.status_code == 200
    assert "Today" in r.text


def test_nav_is_three_tabs(app_client):
    """V9.1 — Today / Conversations / Settings. No Recent calls. No
    Follow-ups."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    assert ">Today<" in body
    assert ">Conversations<" in body
    assert ">Settings<" in body
    assert ">Recent calls<" not in body
    assert ">Follow-ups<" not in body
    assert ">Invoice<" not in body


def test_legacy_calls_route_still_works(app_client):
    """V9.1 keeps /calls alive as an alias to /conversations so old
    bookmarks don't 404."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/calls?t={tok}")
    assert r.status_code == 200


def test_conversations_route_loads(app_client):
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations?t={tok}")
    assert r.status_code == 200
    assert "Conversations" in r.text


def test_followups_route_still_works_for_backward_compat(app_client):
    """V9.1 — the route lives; we just dropped it from the primary
    nav. Direct links shouldn't break."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/followups?t={tok}")
    assert r.status_code == 200


# ── Today communications feed ────────────────────────────────────────

def test_today_shows_recent_partner_in_feed(app_client):
    """Today's 'last 24 hours' section must show real partners."""
    _seed_voice_call("CA_v91_today1", "ace_hvac", "+15551112222")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert r.status_code == 200
    # Phone shows up (formatted)
    assert "555" in r.text and "1112222" in r.text or "(555)" in r.text


def test_today_has_followups_section_when_short_calls_exist(app_client):
    """A short, non-emergency, non-spam call surfaces in the
    'Worth a follow-up' card on Today."""
    _seed_voice_call("CA_v91_short", "ace_hvac", "+15553334444")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    assert "Worth a follow-up" in body or "+15553334444" in body


def test_today_does_not_mention_ai(app_client):
    """Brief: the product should NOT feel like 'AI startup demo'."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    lower = r.text.lower()
    assert ">ai<" not in lower
    assert "the ai " not in lower


def test_today_includes_invoice_link(app_client):
    """V9.0 contract preserved: invoice discoverable from Today."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert "/invoice/" in r.text


# ── Conversations list ───────────────────────────────────────────────

def test_conversations_list_shows_partners(app_client):
    _seed_voice_call("CA_v91_p1", "ace_hvac", "+15556667777")
    _seed_voice_call("CA_v91_p2", "ace_hvac", "+15558889999")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations?t={tok}")
    body = r.text
    assert "(555) 666-7777" in body or "5556667777" in body
    assert "(555) 888-9999" in body or "5558889999" in body


def test_conversations_list_links_into_detail(app_client):
    _seed_voice_call("CA_v91_link", "ace_hvac", "+15550001111")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations?t={tok}")
    body = r.text
    # The list links into /conversations/{normalized digits}
    assert "/conversations/5550001111" in body


def test_conversations_list_handles_empty(app_client):
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations?t={tok}")
    assert r.status_code == 200
    # Brief: empty state should be reassuring, not empty
    assert "No conversations yet" in r.text or "show up here" in r.text


def test_conversations_list_isolates_tenants(app_client):
    """Critical: tenant A must never see tenant B's conversations."""
    _seed_voice_call("CA_v91_iso_ace", "ace_hvac", "+15551110000")
    # Septic_pro happens to also have a tenant config — seed there too
    try:
        _seed_voice_call("CA_v91_iso_septic", "septic_pro", "+15552220000")
    except Exception:
        pass
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations?t={tok}")
    body = r.text
    assert "+15552220000" not in body
    assert "5552220000" not in body or "5552220000" in body[:0]   # absent


def test_conversations_list_unauthorized(app_client):
    r = app_client.get("/client/ace_hvac/conversations?t=bad.token")
    assert r.status_code == 403


# ── Conversation detail (unified thread) ─────────────────────────────

def test_conversation_detail_shows_voice_thread(app_client):
    _seed_voice_call("CA_v91_thread", "ace_hvac", "+15554443333")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(
        f"/client/ace_hvac/conversations/5554443333?t={tok}")
    assert r.status_code == 200
    body = r.text
    assert "Voice call" in body
    assert "Hi, need help" in body
    # HTML-escaped apostrophe (&#x27;) is fine — verify the prefix
    assert "Sure, what" in body and "going on" in body


def test_conversation_detail_shows_sms_thread(app_client):
    _seed_sms("ace_hvac", "+15557776666",
               "What's your weekend rate?",
               "Weekends are $50 extra on emergency calls.")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(
        f"/client/ace_hvac/conversations/5557776666?t={tok}")
    body = r.text
    assert r.status_code == 200
    assert "Text message" in body
    assert "weekend rate" in body or "Weekend" in body


def test_conversation_detail_unifies_voice_and_sms(app_client):
    """Brief: 'SMS and phone interactions should feel unified into one
    conversation history where possible.'"""
    base = int(time.time()) - 1000
    _seed_voice_call("CA_v91_unified_v", "ace_hvac", "+15558887777")
    _seed_sms("ace_hvac", "+15558887777", "follow up", "got it",
               when=base + 500)
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(
        f"/client/ace_hvac/conversations/5558887777?t={tok}")
    body = r.text
    assert r.status_code == 200
    # Both channels rendered in the same thread
    assert "Voice call" in body
    assert "Text message" in body
    # Both texts present
    assert "follow up" in body
    assert "got it" in body


def test_conversation_detail_has_callback_button(app_client):
    """Customer-experience win: one-tap callback from the thread."""
    _seed_voice_call("CA_v91_cb", "ace_hvac", "+15559990000")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(
        f"/client/ace_hvac/conversations/5559990000?t={tok}")
    body = r.text
    assert 'href="tel:' in body
    assert "Call back" in body


def test_conversation_detail_uses_bubble_classes(app_client):
    """V9.1/V9.2 — communication-first means chat-style bubbles, not a
    table of turns. V9.2 appends `series-end` to the last bubble of a
    series so the class is `bubble in series-end` etc — match the
    prefix only."""
    _seed_voice_call("CA_v91_bubble", "ace_hvac", "+15556665555")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(
        f"/client/ace_hvac/conversations/5556665555?t={tok}")
    body = r.text
    assert 'class="bubble in' in body
    assert 'class="bubble out' in body


def test_conversation_detail_unknown_phone_404s(app_client):
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(
        f"/client/ace_hvac/conversations/abc?t={tok}")
    assert r.status_code == 404


def test_conversation_detail_unauthorized(app_client):
    r = app_client.get(
        "/client/ace_hvac/conversations/5551110000?t=bad")
    assert r.status_code == 403


def test_conversation_detail_no_history_renders_empty(app_client):
    """Valid phone but no transcripts → page renders gracefully."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(
        f"/client/ace_hvac/conversations/9991112222?t={tok}")
    assert r.status_code == 200
    assert "No history" in r.text or "No conversations" in r.text


# ── Helpers ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, formatted", [
    ("+15551234567", "(555) 123-4567"),
    ("15551234567",  "(555) 123-4567"),
    ("5551234567",   "(555) 123-4567"),
    ("(555) 123-4567", "(555) 123-4567"),
    ("",  "Unknown"),
    ("garbage", "garbage"),
])
def test_format_phone(raw, formatted):
    assert client_portal._format_phone(raw) == formatted


def test_phone_slug_normalizes():
    assert client_portal._phone_slug("+1 (555) 123-4567") == "5551234567"
    assert client_portal._phone_slug("5551234567") == "5551234567"


def test_human_when_relative():
    now = int(time.time())
    assert "just now" in client_portal._human_when(now - 5, now)
    assert "m ago" in client_portal._human_when(now - 300, now)
    assert "h ago" in client_portal._human_when(now - 3600 * 5, now)
    assert "yesterday" in client_portal._human_when(now - 86400 * 1 - 100, now)
