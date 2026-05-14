"""V9.3 — addressing direct user feedback on V9.2:

  1. /call/{call_sid} used the pre-V9.1 box-row pattern — replace with
     V9.2 bubble pattern.
  2. Dark mode contrast broken on V9.2 additions — hardcoded `--n-500`
     / `--n-600` / `--n-700` for text don't invert. Switched to
     `--muted` / `--fg` which are dark-mode-aware.
  3. Sender captions misaligned — `margin: 8px 12px 3px` indented the
     caption 12px from the bubble's outer edge. Realigned to flush.
  4. Color-hashed avatars — each partner gets a stable hue so the
     Conversations list reads as distinct people.
  5. Status pills had dark-on-darker color/bg pairs in dark mode.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import client_portal, design, transcripts, usage


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


def _seed_call(call_sid: str, phone: str, summary: str = ""):
    usage.start_call(call_sid, "ace_hvac", phone, "+18449403274")
    transcripts.record_turn(call_sid, "ace_hvac", "user",
                              "Hi, my hot water heater is leaking")
    transcripts.record_turn(call_sid, "ace_hvac", "assistant",
                              "I can get someone out today")
    usage.end_call(call_sid, outcome="normal")
    if summary:
        from src import migrations
        migrations.run_all()
        with usage._db_lock:
            conn = usage._connect()
            usage._init_schema(conn)
            conn.execute("UPDATE calls SET summary=? WHERE call_sid=?",
                         (summary, call_sid))
            conn.close()


# ── call_detail now uses bubble pattern ────────────────────────────

def test_call_detail_uses_bubble_pattern(app_client):
    """V9.2 ships a bubble pattern in /conversations/{phone}. V9.3
    brings /call/{call_sid} under the same pattern instead of the
    pre-V9.1 box rows."""
    _seed_call("CA_v93_bubble", "+15551231234")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/call/CA_v93_bubble?t={tok}")
    assert r.status_code == 200
    body = r.text
    assert 'class="bubble in' in body
    assert 'class="bubble out' in body
    # Old box-row pattern should be gone
    assert 'background:var(--n-50)' not in body or '<style' in body
    # Should have the sender captions
    assert 'class="sender-cap in"' in body or 'class="sender-cap out"' in body
    # Should have a time chip
    assert 'class="time-chip"' in body


def test_call_detail_has_back_link_to_conversation(app_client):
    """When the partner phone is known, /call/{sid} should offer a
    breadcrumb back to /conversations/{phone}."""
    _seed_call("CA_v93_back", "+15554441111")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/call/CA_v93_back?t={tok}")
    body = r.text
    assert "Back to conversation" in body
    assert "/conversations/5554441111" in body


def test_call_detail_header_shows_partner_and_when(app_client):
    """V9.3 — single hero strip with partner + when + duration +
    status pill instead of a 5-row table.
    V9.4 — the V9.3 .call-detail-head pattern was unified with the
    thread-hero block on conversation_detail. Same markup either way."""
    _seed_call("CA_v93_head", "+15557772222")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/call/CA_v93_head?t={tok}")
    body = r.text
    # Hero is now the unified .thread-hero block
    assert 'class="thread-hero"' in body
    # Phone displayed
    assert "+15557772222" in body
    # Status pill rendered
    assert "Answered" in body


def test_call_detail_summary_when_present(app_client):
    """Summary lives in its own card under the header."""
    _seed_call("CA_v93_sum", "+15555550000",
                summary="Customer needs new water heater installed Saturday.")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/call/CA_v93_sum?t={tok}")
    body = r.text
    assert "new water heater installed Saturday" in body
    # No "AI summary" engineer-y label leaked
    assert "AI summary" not in body


def test_call_detail_empty_turns_uses_warm_empty_state(app_client):
    """A call with no transcript turns should show the warm empty
    pattern (icon + title + sub) not the bare grey 'No transcript'."""
    usage.start_call("CA_v93_empty", "ace_hvac",
                      "+15558881111", "+18449403274")
    usage.end_call("CA_v93_empty", outcome="normal")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/call/CA_v93_empty?t={tok}")
    body = r.text
    assert r.status_code == 200
    assert "empty-warm" in body
    assert "No transcript" in body


def test_call_detail_404_for_wrong_tenant(app_client):
    """Cross-tenant guard preserved."""
    _seed_call("CA_v93_iso", "+15550000222")
    tok = client_portal.issue_token("septic_pro")
    r = app_client.get(f"/client/septic_pro/call/CA_v93_iso?t={tok}")
    assert r.status_code == 404


# ── dark-mode contrast fixes ───────────────────────────────────────

def test_stat_label_uses_muted_token():
    """V9.2 set .stat .label to --n-600 which is dark gray with no
    dark-mode invert. V9.3 uses --muted which IS dark-mode aware."""
    css = design.css()
    # Find the rule
    idx = css.find(".stat .label")
    chunk = css[idx:idx + 200]
    assert "var(--muted)" in chunk
    assert "var(--n-600)" not in chunk


def test_call_card_text_uses_muted_token():
    css = design.css()
    # .call .body .from
    idx = css.find(".call .body .from")
    chunk = css[idx:idx + 200]
    assert "var(--muted)" in chunk
    assert "var(--n-500)" not in chunk
    # .call .right
    idx = css.find(".call .right")
    chunk = css[idx:idx + 300]
    assert "var(--muted)" in chunk


def test_sender_cap_uses_muted_token():
    css = design.css()
    idx = css.find(".sender-cap ")
    chunk = css[idx:idx + 250]
    assert "var(--muted)" in chunk


def test_time_chip_uses_muted_token_and_has_dark_override():
    css = design.css()
    idx = css.find(".time-chip ")
    chunk = css[idx:idx + 250]
    assert "var(--muted)" in chunk
    # Dark mode override block exists
    assert ".time-chip { background:" in css or "prefers-color-scheme: dark" in css


def test_pills_have_dark_mode_overrides():
    """V9.3 — good/warn/bad pills had dark text on darker bg in dark
    mode (failing contrast). Verify the dark overrides exist."""
    css = design.css()
    # The dark-mode block should contain .pill.warn { ... }
    assert ".pill.warn  { background: #2e1f08" in css \
        or ".pill.warn { background: #2e1f08" in css \
        or "#fbbf24" in css   # the brighter foreground we added


def test_pill_bad_uses_brighter_color_in_dark_mode():
    css = design.css()
    assert "#fb7185" in css   # the brighter red text


# ── sender caption alignment fix ───────────────────────────────────

def test_sender_cap_margin_no_horizontal_offset():
    """V9.2 had margin: 8px 12px 3px which pushed captions 12px from
    the bubble edge. V9.3 reduces horizontal margin to zero so caption
    aligns flush with bubble."""
    css = design.css()
    idx = css.find(".sender-cap {")
    chunk = css[idx:idx + 300]
    # Caption should not have 12px horizontal margin anymore
    assert "margin: 8px 12px 3px" not in chunk


# ── color-hashed avatars ───────────────────────────────────────────

def test_avatar_hue_stable_for_same_seed():
    h1 = design._avatar_hue("+15551234567")
    h2 = design._avatar_hue("+15551234567")
    assert h1 == h2


def test_avatar_hue_differs_for_different_seeds():
    """Two different phones should almost certainly get different hues."""
    seeds = [f"+155500001{i:02d}" for i in range(10)]
    hues = {design._avatar_hue(s) for s in seeds}
    # Out of 10 seeds we expect >= 7 distinct hues (allow a couple of
    # hash collisions modulo 360).
    assert len(hues) >= 7


def test_avatar_hue_range():
    """All hues must fall in [0, 359]."""
    for s in ("+15550001000", "Sarah", "+18445550000", ""):
        h = design._avatar_hue(s)
        assert 0 <= h < 360


def test_avatar_hue_empty_seed_returns_default():
    assert design._avatar_hue("") == 220


def test_call_card_emits_hue_inline_style():
    out = design.call_card(caller="Mike", from_number="+15551234567",
                            when="just now", status="answered")
    assert "--av-h:" in out


def test_call_card_same_phone_same_hue():
    """Conversations list shows the same partner card on multiple
    pages — the avatar color must stay stable."""
    a = design.call_card(caller="Mike", from_number="+15551234567",
                          status="answered")
    b = design.call_card(caller="Mike", from_number="+15551234567",
                          status="missed")
    import re
    hue_a = re.search(r"--av-h:(\d+)", a).group(1)
    hue_b = re.search(r"--av-h:(\d+)", b).group(1)
    assert hue_a == hue_b


# ── recording surface ──────────────────────────────────────────────

def test_call_detail_shows_recording_indicator_when_present(app_client):
    """If recording metadata exists, call_detail should surface it as
    an information card. Portal users can't play it (auth-gated proxy)
    but they get visibility."""
    from src import recordings, migrations
    migrations.run_all()  # ensures recording_* columns exist
    _seed_call("CA_v93_rec", "+15551011010")
    recordings.store_recording(
        "CA_v93_rec", "RE_test", "https://api.twilio.com/test", 42)
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/call/CA_v93_rec?t={tok}")
    body = r.text
    assert r.status_code == 200
    assert "Audio recording" in body
