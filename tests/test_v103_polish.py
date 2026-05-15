"""V10.3 — 10 high-impact polish upgrades.

User asked for "real photos, more functionality and usability."
Self-prioritized 10 high-impact tasks across three commits:

  Commit A: real photos · phone status bar · typing dots · read receipts
  Commit B: owner-SMS preview phone · onboarding pointer
  Commit C: sparklines · recording mock · search filter · tenant switcher
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import design, transcripts, usage, client_portal


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


# ── 1. Real-photo avatars (Pravatar) ────────────────────────────────

def test_partner_photo_url_uses_pravatar():
    """V10.3 — real-photo URL is Pravatar, not DiceBear."""
    url = design.partner_photo_url("+15551234567")
    assert "pravatar.cc" in url


def test_partner_photo_fallback_is_dicebear():
    """The fallback chain in call_card cycles to DiceBear when the
    real photo fails to load."""
    url = design.partner_photo_fallback_url("+15551234567")
    assert "dicebear.com" in url


def test_partner_photo_urls_stable_for_same_seed():
    a = design.partner_photo_url("+15551234567")
    b = design.partner_photo_url("+15551234567")
    assert a == b


def test_partner_photo_urls_different_for_different_seeds():
    a = design.partner_photo_url("+15551234567")
    b = design.partner_photo_url("+15559998877")
    assert a != b


def test_call_card_has_fallback_onerror_chain():
    """<img onerror> retries the fallback URL once; on second failure
    hides the img so the initial disc shows."""
    out = design.call_card(
        caller="Marcus", from_number="+15551234567",
        status="answered",
        photo_url="https://i.pravatar.cc/150?u=15551234567",
    )
    assert "onerror=" in out
    # Two-step recovery: first tries the dicebear fallback, then hides
    assert "tried" in out
    assert "fallback" in out
    assert "dicebear" in out


# ── 2. Phone status bar ─────────────────────────────────────────────

def test_demo_page_has_phone_status_bar(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'class="phone-status"' in body
    assert ">9:41<" in body
    assert 'class="ps-battery"' in body


# ── 3. Typing dots animation ────────────────────────────────────────

def test_demo_page_has_typing_indicator(app_client):
    r = app_client.get("/")
    body = r.text
    assert "appendTyping" in body
    # CSS animation class present
    assert "pmsg ai typing" in body or "pmsg.typing" in body


def test_typing_animation_css_present():
    css = design.css()
    assert "typing-bounce" in css
    assert "@keyframes typing-bounce" in css


# ── 4. Read receipts ────────────────────────────────────────────────

def test_demo_page_has_read_receipt_helpers(app_client):
    r = app_client.get("/")
    body = r.text
    assert "appendReceipt" in body
    assert "Delivered" in body
    assert "Read" in body


def test_receipt_css_class_present():
    css = design.css()
    assert ".phone-conv .receipt" in css


# ── 5. Owner-SMS preview phone ──────────────────────────────────────

def test_demo_page_renders_owner_phone(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'class="phone-shell owner-shell"' in body
    assert "Bob's phone" in body
    assert 'id="owner-conv"' in body


def test_owner_phone_seeded_with_emergency_and_booking(app_client):
    """First-render state: two pre-baked SMS bubbles so the prospect
    sees what the owner phone looks like populated."""
    r = app_client.get("/")
    body = r.text
    assert "owner-sms urgent" in body
    assert "Marcus Reilly" in body
    assert "Booking" in body


def test_demo_page_has_push_owner_sms_function(app_client):
    """When a chat exchange flags emergency/booking, JS pushes a new
    SMS bubble into the owner phone preview."""
    r = app_client.get("/")
    body = r.text
    assert "pushOwnerSMS" in body
    assert "just-arrived" in body


def test_owner_sms_css_classes_present():
    css = design.css()
    assert ".owner-sms" in css
    assert ".owner-sms.urgent" in css


# ── 6. Onboarding pointer ───────────────────────────────────────────

def test_demo_page_has_onboarding_function(app_client):
    """V10.3 added a bobbing arrow tooltip. V10.5 replaced it with a
    quiet inline caption (.onboard-hint) — no animation, no arrow."""
    r = app_client.get("/")
    body = r.text
    assert "maybeShowOnboarding" in body
    assert "onboard-hint" in body
    assert "aircept_onboarded" in body  # localStorage key


def test_onboard_hint_css_present():
    css = design.css()
    assert ".onboard-hint" in css


# ── 7. Sparklines on Today stats ────────────────────────────────────

def test_stat_card_accepts_sparkline_values():
    out = design.stat_card("Calls", 5, sparkline_values=[1, 2, 3, 4, 5])
    assert 'class="stat-spark"' in out
    assert "<svg" in out


def test_stat_card_no_spark_when_all_zero():
    out = design.stat_card("Calls", 0, sparkline_values=[0] * 30)
    # No spark slot when there's no signal to plot
    assert 'class="stat-spark"' not in out


def test_stat_card_no_spark_when_values_not_passed():
    out = design.stat_card("Calls", 5)
    assert 'class="stat-spark"' not in out


def test_daily_call_counts_returns_n_buckets():
    out = usage.daily_call_counts("ace_hvac", days=30)
    assert isinstance(out, list)
    assert len(out) == 30


def test_daily_call_counts_zero_for_unknown_tenant():
    out = usage.daily_call_counts("no_such", days=14)
    assert out == [0] * 14


def test_today_renders_sparklines_when_data_present(app_client):
    """E2E: when there's call activity, the Today stat cards include
    sparklines under the values."""
    # Seed a call so daily_call_counts has signal
    usage.start_call("CA_v103_spark", "ace_hvac",
                      "+15554440100", "+18449403274")
    usage.end_call("CA_v103_spark", outcome="normal")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    assert 'class="stat-spark"' in body


# ── 8. Recording mock ───────────────────────────────────────────────

def test_partner_preview_includes_recording_player_for_voice(app_client):
    """When the partner has any voice-channel history, the inline
    preview shows a play button + waveform."""
    # Seed a voice call
    usage.start_call("CA_v103_rec", "ace_hvac",
                      "+15554440200", "+18449403274")
    transcripts.record_turn("CA_v103_rec", "ace_hvac", "user", "hello")
    transcripts.record_turn("CA_v103_rec", "ace_hvac", "assistant", "hi")
    usage.end_call("CA_v103_rec", outcome="normal")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    assert "rec-player" in body
    assert "rec-play-btn" in body
    assert "rec-waveform" in body


def test_partner_preview_omits_recording_player_for_sms_only(app_client):
    """SMS-only partners don't get a recording mock."""
    from memory import normalize_phone
    sid = f"SMS_{normalize_phone('+15554440300')}"
    transcripts.record_turn(sid, "ace_hvac", "user", "Hello via SMS")
    transcripts.record_turn(sid, "ace_hvac", "assistant", "Got it")
    usage.log_sms(sid, "ace_hvac", "+15554440300", "Hello via SMS",
                   direction="inbound")
    usage.log_sms(sid, "ace_hvac", "+15554440300", "Got it",
                   direction="outbound")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    # Find this partner's preview section
    idx = body.find("4440300")
    if idx > -1:
        chunk = body[max(0, idx - 2000):idx + 500]
        # No rec-player in this partner's expanded section
        # (note: another partner with voice may have one elsewhere)
        assert "rec-player" not in chunk or "rec-player" not in chunk


def test_recording_mock_play_button_wiring_in_page_shell():
    """The page() shell ships JS that toggles .playing on click."""
    out = design.page(title="X", body="hi")
    assert "rec-play-btn" in out
    assert "playing" in out


def test_recording_player_css_signals_playback():
    """V10.5 — the V10.3 wave-pulse keyframe was retired (one of
    eight simultaneous animations on the page). The waveform now
    statically signals playback via background-color swap; the
    progress bar carries the playback animation."""
    css = design.css()
    assert ".rec-player.playing" in css
    assert ".rec-player.playing .rec-waveform span" in css


# ── 9. Conversations-list search ────────────────────────────────────

def test_conversations_list_has_search_input(app_client):
    """V10.3 — inline search filter at the top of conversations."""
    # Seed a partner so the list renders
    usage.start_call("CA_v103_search", "ace_hvac",
                      "+15554440400", "+18449403274")
    usage.end_call("CA_v103_search", outcome="normal")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations?t={tok}")
    body = r.text
    assert 'class="conv-search"' in body
    assert 'id="conv-filter"' in body


def test_conv_search_css_present():
    css = design.css()
    assert ".conv-search" in css


# ── 10. Tenant switcher ─────────────────────────────────────────────

def test_demo_page_has_tenant_switcher(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'class="tenant-switcher"' in body
    assert 'id="tenant-switcher"' in body


def test_tenant_switcher_offers_multiple_industries(app_client):
    r = app_client.get("/")
    body = r.text
    # Three industry options visible to the prospect
    assert "Septic Pro" in body
    assert "HVAC" in body
    assert ("Realty" in body or "Real estate" in body
            or "Real Estate" in body)


def test_tenant_switcher_swaps_suggestions(app_client):
    """The select options carry data-emergency/book/price strings that
    the JS uses to swap the chat suggestion buttons clientside."""
    r = app_client.get("/")
    body = r.text
    assert "data-emergency=" in body
    assert "data-book=" in body
    assert "data-price=" in body


def test_tenant_switcher_css_present():
    css = design.css()
    assert ".tenant-switcher" in css
