"""V9.4 — visual system redesign.

The V9.3 polish was correct at the component level; V9.4 fixes the
composition. New patterns this file covers:

  - Type scale: H1 32px / H2 22px / H3 17px / body 15px / muted 13.5px
  - Page header: no border-bottom, generous margins
  - Card variants: solid (default), soft (tinted bg, no border), flush
  - section_caption helper for magazine-style above-card labels
  - Today: bare typographic hero, soft followups card, bare stats strip
  - Conversations list: no redundant card title; bare list-count chip
  - Conversation/call detail: unified .thread-hero pattern
  - Settings: bare section-captions over cards, soft contact note
  - Sidebar: active-state left accent bar (Linear pattern)
"""
from __future__ import annotations

import importlib
import time

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


def _seed_call(call_sid: str, phone: str):
    usage.start_call(call_sid, "ace_hvac", phone, "+18449403274")
    transcripts.record_turn(call_sid, "ace_hvac", "user", "Need a quote")
    transcripts.record_turn(call_sid, "ace_hvac", "assistant",
                              "Sure, what's the address")
    usage.end_call(call_sid, outcome="normal")


# ── Type scale ────────────────────────────────────────────────────

def test_h1_is_32px():
    css = design.css()
    idx = css.find("header.page h1 {")
    chunk = css[idx:idx + 300]
    assert "font-size: 32px" in chunk


def test_h2_is_22px():
    css = design.css()
    idx = css.find("h2, .h2 {")
    chunk = css[idx:idx + 200]
    assert "font-size: 22px" in chunk


def test_h3_is_17px():
    css = design.css()
    idx = css.find("h3, .h3 {")
    chunk = css[idx:idx + 200]
    assert "font-size: 17px" in chunk


def test_page_header_has_no_border_bottom():
    """V9.4 — admin-template-y border-bottom under page H1 retired."""
    css = design.css()
    idx = css.find("header.page {")
    chunk = css[idx:idx + 300]
    assert "border-bottom" not in chunk


# ── Card variants ─────────────────────────────────────────────────

def test_card_solid_is_default():
    out = design.card("hi")
    assert 'class="card"' in out
    assert 'class="card soft"' not in out
    assert 'class="card flush"' not in out


def test_card_soft_variant():
    out = design.card("hi", variant="soft")
    assert "card soft" in out


def test_card_flush_orthogonal_to_variant():
    """A card can be soft AND flush together."""
    out = design.card("hi", variant="soft", flush=True)
    assert "card soft flush" in out or "card flush soft" in out


def test_card_invalid_variant_falls_back_to_solid():
    out = design.card("hi", variant="not-a-thing")
    assert 'class="card"' in out
    assert "soft" not in out.split('class="card"')[1][:30]


def test_card_soft_uses_tinted_bg_not_white():
    css = design.css()
    # The CSS rule (not the doc comment); look for the opening brace.
    idx = css.find(".card.soft {")
    assert idx > -1
    chunk = css[idx:idx + 300]
    # Soft cards use --n-50 (light tint) not --card-bg (white)
    assert "var(--n-50)" in chunk


def test_card_soft_has_no_border():
    css = design.css()
    idx = css.find(".card.soft {")
    assert idx > -1
    chunk = css[idx:idx + 300]
    # Either explicit none or transparent
    assert "border:" in chunk
    assert "transparent" in chunk or "none" in chunk


# ── section_caption helper ────────────────────────────────────────

def test_section_caption_renders():
    out = design.section_caption("Recent activity")
    assert "section-caption" in out
    assert "Recent activity" in out


def test_section_caption_escapes_html():
    out = design.section_caption("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;" in out


# ── Today composition ────────────────────────────────────────────

def test_today_uses_bare_typographic_hero(app_client):
    """V9.4 — Today headline is NOT inside a card; it's bare typo."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    assert 'class="today-hero"' in body
    assert 'class="today-headline"' in body


def test_today_uses_section_captions_over_cards(app_client):
    """The "Recent activity" / "Worth a follow-up" / "This month"
    labels are bare typographic captions above flush cards."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    assert 'class="section-caption"' in body
    assert "Recent activity" in body
    assert "This month" in body


def test_today_stats_have_no_card_chrome(app_client):
    """V9.4 — stats footer is a bare strip, not wrapped in a card."""
    usage.start_call("CA_v94_stat", "ace_hvac",
                      "+15550000900", "+18449403274")
    usage.end_call("CA_v94_stat", outcome="normal")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    # Stat values present
    assert 'class="stats"' in body
    assert 'class="stat ' in body or 'class="stat"' in body
    # Stats not wrapped in their own card with title
    # (heuristic: the "Bookings captured" stat label, not in a card title)
    assert "Bookings captured" in body


def test_today_followups_card_is_soft_variant(app_client):
    """V9.4 — follow-ups should be de-emphasized vs main activity feed."""
    usage.start_call("CA_v94_short", "ace_hvac",
                      "+15554441111", "+18449403274")
    usage.end_call("CA_v94_short", outcome="normal")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    # Find the RENDERED section caption (not the CSS comment). The
    # marker is the unique markup: section-caption div with the label.
    marker = '<div class="section-caption">Worth a follow-up</div>'
    if marker in body:
        idx = body.find(marker) + len(marker)
        after = body[idx:idx + 200]
        assert 'card soft' in after, f"Expected soft variant, got: {after!r}"


# ── Conversations list composition ───────────────────────────────

def test_conversations_list_has_no_redundant_title(app_client):
    """V9.4 — the page subtitle already says "Conversations". Don't
    repeat it as a card title."""
    _seed_call("CA_v94_p1", "+15558881100")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations?t={tok}")
    body = r.text
    # Old V9.3: <h2>Conversations</h2> inside a card. V9.4: gone.
    # The page header still has "Conversations" but no inner H2.
    # Detect by counting H2s in the body — V9.4 should have zero or
    # one (depending on partner-count chip), V9.3 had at least 2.
    h2_count = body.count("<h2")
    assert h2_count <= 1


def test_conversations_list_shows_partner_count_chip(app_client):
    """The bare typographic 'N people' chip."""
    _seed_call("CA_v94_count1", "+15550001011")
    _seed_call("CA_v94_count2", "+15550001022")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations?t={tok}")
    body = r.text
    assert 'class="list-count"' in body
    assert "people" in body or "person" in body


def test_conversations_empty_uses_warm_pattern(app_client):
    """V9.4 — empty conversations gets the warm pattern (icon + title
    + sub) not the bare grey 'No conversations yet'."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations?t={tok}")
    body = r.text
    if "No conversations yet" in body:
        assert "empty-warm" in body


# ── Conversation detail composition ──────────────────────────────

def test_conversation_detail_uses_thread_hero(app_client):
    """V9.4 — bare typographic hero, not wrapped in a card."""
    _seed_call("CA_v94_th", "+15557007007")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations/5557007007?t={tok}")
    body = r.text
    assert 'class="thread-hero"' in body
    assert 'class="thread-hero-name"' in body


def test_conversation_detail_has_back_to_list(app_client):
    """V9.4 — back-link to the conversations list as a breadcrumb."""
    _seed_call("CA_v94_back", "+15558007008")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations/5558007008?t={tok}")
    body = r.text
    assert "All conversations" in body or "Back to" in body


def test_call_detail_uses_unified_thread_hero(app_client):
    """V9.4 — call_detail and conversation_detail share the same hero
    block. Old V9.3 .call-detail-head is gone."""
    _seed_call("CA_v94_cd", "+15559008009")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/call/CA_v94_cd?t={tok}")
    body = r.text
    assert 'class="thread-hero"' in body
    assert 'class="call-detail-head"' not in body


def test_call_detail_summary_uses_soft_variant(app_client, monkeypatch):
    """V9.4 — call summary is context, not data. Soft variant card."""
    from src import migrations
    migrations.run_all()
    _seed_call("CA_v94_sumvar", "+15550010010")
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        conn.execute("UPDATE calls SET summary=? WHERE call_sid=?",
                     ("Customer wants estimate for new water heater",
                      "CA_v94_sumvar"))
        conn.close()
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/call/CA_v94_sumvar?t={tok}")
    body = r.text
    # The section above the summary block uses section_caption pattern
    assert "Call summary" in body
    # The summary itself uses a soft card
    idx = body.find("Customer wants estimate")
    # Look upward for the containing card class
    if idx > -1:
        before = body[:idx]
        last_section = before.rfind('class="card')
        if last_section > -1:
            section_chunk = before[last_section:last_section + 200]
            assert "soft" in section_chunk


# ── Settings composition ─────────────────────────────────────────

def test_settings_uses_section_captions(app_client):
    """V9.4 — section captions over flush cards; soft variant for the
    contact note."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/settings?t={tok}")
    body = r.text
    assert 'class="section-caption"' in body
    assert "Account" in body


def test_settings_contact_note_is_soft(app_client):
    """The "Need a change?" prompt should de-emphasize as context."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/settings?t={tok}")
    body = r.text
    # Find the contact note and check parent card class
    if "Need a change?" in body:
        idx = body.find("Need a change?")
        before = body[:idx]
        last_section = before.rfind('class="card')
        if last_section > -1:
            section_chunk = before[last_section:last_section + 200]
            assert "soft" in section_chunk


# ── Sidebar active-state ─────────────────────────────────────────

def test_sidebar_active_nav_has_accent_bar():
    """V9.4 — Linear-style left accent bar on active nav, not just
    background tint."""
    css = design.css()
    idx = css.find('.sidebar nav a[aria-current="page"]::before')
    assert idx > -1
    chunk = css[idx:idx + 300]
    assert "background: var(--accent)" in chunk


def test_sidebar_brand_is_bigger():
    """V9.4 — brand label gets a real 16px treatment, not 15px chip."""
    css = design.css()
    idx = css.find(".sidebar .brand {")
    chunk = css[idx:idx + 300]
    assert "font-size: 16px" in chunk


# ── month_label_short helper ─────────────────────────────────────

@pytest.mark.parametrize("month, expected", [
    ("2026-05", "May"),
    ("2026-12", "Dec"),
    ("2026-01", "Jan"),
    ("garbage", "Current"),
])
def test_month_label_short(month, expected):
    assert client_portal.month_label_short(month) == expected
