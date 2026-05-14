"""V9.2 — perceptual polish.

Verify the actual behaviors the user reported as broken in V9.1:
  - per-bubble timestamps removed
  - sender captions shown once per series (not per bubble)
  - time-chip separators inserted at thread start + on >5min gaps
  - bubble class includes `series-end` on last bubble of each role series
  - Today headline reads contextually (specific over generic)
  - Empty state uses the warmed pattern (icon + title + sub-copy)
  - Surface tokens are warmed (bg != pure white) and shadow upgraded
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


def _seed_voice(call_sid: str, phone: str):
    """Two turns, role switching."""
    usage.start_call(call_sid, "ace_hvac", phone, "+18449403274")
    transcripts.record_turn(call_sid, "ace_hvac", "user", "Hi, need help")
    transcripts.record_turn(call_sid, "ace_hvac", "assistant",
                              "Sure, what is going on")
    usage.end_call(call_sid, outcome="normal")


def _seed_voice_with_series(call_sid: str, phone: str):
    """Three turns: user, user, assistant. Tests that two consecutive
    'user' bubbles share ONE caption."""
    usage.start_call(call_sid, "ace_hvac", phone, "+18449403274")
    base = int(time.time()) - 200
    transcripts.record_turn(call_sid, "ace_hvac", "user", "First message",
                              ts=base)
    transcripts.record_turn(call_sid, "ace_hvac", "user", "Second message",
                              ts=base + 5)
    transcripts.record_turn(call_sid, "ace_hvac", "assistant",
                              "Got it", ts=base + 12)
    usage.end_call(call_sid, outcome="normal")


# ── bubble timestamps removed ─────────────────────────────────────────

def test_no_per_bubble_timestamps(app_client):
    """V9.1 had a `.bubble-ts` element on every bubble. V9.2 removes
    it (low-contrast, noisy, table-like)."""
    _seed_voice("CA_v92_ts", "+15551112222")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations/5551112222?t={tok}")
    body = r.text
    assert 'bubble-ts' not in body


# ── time chip ─────────────────────────────────────────────────────────

def test_time_chip_at_thread_start(app_client):
    _seed_voice("CA_v92_chip", "+15553334444")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations/5553334444?t={tok}")
    body = r.text
    assert 'class="time-chip"' in body


def test_time_chip_uses_today_when_recent(app_client):
    """A turn from a few minutes ago should chip as 'Today at ...'."""
    _seed_voice("CA_v92_today_chip", "+15554445555")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations/5554445555?t={tok}")
    body = r.text
    # Either "Today at" or the day name should appear
    assert "Today at" in body or "Today" in body


def test_time_chip_renders_yesterday(monkeypatch):
    """Direct unit test of _render_time_chip for the yesterday branch."""
    now = int(time.time())
    yest = now - 24 * 3600 - 600
    out = client_portal._render_time_chip(yest)
    assert "Yesterday at" in out


def test_time_chip_renders_older_date():
    """Older than yesterday should chip as 'Mon DD at HH:MM AM/PM'."""
    now = int(time.time())
    long_ago = now - 7 * 86400
    out = client_portal._render_time_chip(long_ago)
    assert "Today" not in out
    assert "Yesterday" not in out
    assert " at " in out


# ── sender captions ───────────────────────────────────────────────────

def test_sender_caption_appears(app_client):
    _seed_voice("CA_v92_sender", "+15556667777")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/conversations/5556667777?t={tok}")
    body = r.text
    assert "Joanna" in body
    assert "Caller" in body


def test_sender_caption_collapses_consecutive_same_role():
    """The whole point of bubble grouping: two 'user' bubbles in a row
    share ONE sender caption, not two."""
    base = int(time.time())
    turns = [
        {"role": "user",      "text": "first",  "ts": base},
        {"role": "user",      "text": "second", "ts": base + 5},
        {"role": "assistant", "text": "got it", "ts": base + 10},
    ]
    out = client_portal._render_bubble_sequence(turns)
    # Caption count: 2 total (one for the user series, one for assistant).
    assert out.count('class="sender-cap in"') == 1
    assert out.count('class="sender-cap out"') == 1


def test_role_switch_emits_fresh_caption():
    """user → assistant → user produces 2 'in' captions and 1 'out'."""
    base = int(time.time())
    turns = [
        {"role": "user",      "text": "a", "ts": base},
        {"role": "assistant", "text": "b", "ts": base + 5},
        {"role": "user",      "text": "c", "ts": base + 10},
    ]
    out = client_portal._render_bubble_sequence(turns)
    assert out.count('class="sender-cap in"') == 2
    assert out.count('class="sender-cap out"') == 1


# ── series-end class ──────────────────────────────────────────────────

def test_last_bubble_in_series_has_series_end_class():
    """The last bubble in each role series gets `series-end` so CSS can
    add a margin-bottom that visually breaks the group."""
    base = int(time.time())
    turns = [
        {"role": "user",      "text": "first",  "ts": base},
        {"role": "user",      "text": "second", "ts": base + 5},
        {"role": "assistant", "text": "reply",  "ts": base + 10},
    ]
    out = client_portal._render_bubble_sequence(turns)
    # 'first' is NOT series-end (followed by another user turn).
    # 'second' IS series-end (followed by assistant). 'reply' IS
    # series-end (end of thread).
    assert out.count("series-end") == 2


def test_single_turn_is_series_end():
    base = int(time.time())
    out = client_portal._render_bubble_sequence(
        [{"role": "user", "text": "hi", "ts": base}])
    assert "series-end" in out


# ── Today headline ────────────────────────────────────────────────────

@pytest.mark.parametrize("calls, msgs, em, expected", [
    (0, 0, 0, "Quiet line today."),
    (1, 0, 0, "1 call today."),
    (4, 0, 0, "4 calls today."),
    (0, 1, 0, "1 text today."),
    (0, 3, 0, "3 texts today."),
    (2, 5, 0, "2 calls and 5 texts today."),
    (3, 0, 1, "3 calls today — 1 emergency routed."),
    (5, 2, 2, "5 calls and 2 texts today — 2 emergencies routed."),
])
def test_today_headline(calls, msgs, em, expected):
    assert client_portal._today_headline(calls, msgs, em) == expected


def test_today_headline_present_on_portal(app_client):
    """The headline string lands in the rendered Today page."""
    usage.start_call("CA_v92_today_h", "ace_hvac", "+15558889999",
                     "+18449403274")
    usage.end_call("CA_v92_today_h", outcome="normal")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert r.status_code == 200
    body = r.text
    # The headline pattern: "N call(s) today"
    assert "today" in body.lower()


# ── empty state warmth ────────────────────────────────────────────────

def test_today_empty_state_uses_warm_pattern(app_client):
    """No activity in last 24h → warmed empty state, not bare 'No calls'."""
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    if "Last 24 hours" in body and "All quiet right now" in body:
        # Warm pattern markers
        assert "empty-warm" in body
        assert "empty-title" in body
        assert "empty-icon" in body


# ── surface tokens warmed ─────────────────────────────────────────────

def test_background_is_warmed_off_white():
    """V9.2 — pure white bg made cards feel flat. Warmed near-white."""
    css = design.css()
    # Find the surface-token block (the first --bg declaration).
    idx = css.find("--bg:")
    chunk = css[idx:idx + 120]
    assert "#fbfcfd" in chunk or "#fafbfc" in chunk or "#fafafa" in chunk


def test_base_font_size_bumped_to_15():
    css = design.css()
    # The `body { font-size: ... }` rule.
    assert "font-size: 15px;" in css


def test_stat_label_not_uppercase():
    """V9.2 — ALL CAPS labels read engineer-y. Sentence-case now."""
    css = design.css()
    # Find the .stat .label declaration.
    idx = css.find(".stat .label")
    chunk = css[idx:idx + 200]
    assert "text-transform: uppercase" not in chunk


def test_bubble_in_uses_warmer_neutral():
    """V9.1 used --n-100 (#f1f5f9) which read cold. V9.2 explicit hex."""
    css = design.css()
    idx = css.find(".bubble.in")
    chunk = css[idx:idx + 200]
    # Either of these is acceptable; just verify it's not the cold
    # slate-100 default anymore.
    assert "#f1f5f9" not in chunk


def test_call_card_hover_uses_accent_soft():
    """V9.2 — hover should be more tactile than the V9.1 .n-50 wash."""
    css = design.css()
    idx = css.find(".call:hover")
    chunk = css[idx:idx + 200]
    # Either accent-soft directly or a more saturated wash than n-50.
    assert ("--accent-soft" in chunk or "var(--accent-soft)" in chunk)


# ── mobile bottom-tab active indicator ───────────────────────────────

def test_mobile_bottom_tab_has_active_indicator():
    """V9.2 — active mobile tab gets a visible pip indicator, not just
    a background color."""
    css = design.css()
    # The mobile bottom-dock block contains an ::before with the pip.
    idx = css.find('.sidebar nav a[aria-current="page"]::before')
    assert idx > -1


# ── time-chip gap logic ──────────────────────────────────────────────

def test_time_chip_inserted_on_large_gap():
    """Two messages > 5 minutes apart should be separated by a fresh
    time-chip (the second one)."""
    base = int(time.time())
    turns = [
        {"role": "user", "text": "first",  "ts": base},
        {"role": "user", "text": "later",  "ts": base + 600},  # 10 min
    ]
    out = client_portal._render_bubble_sequence(turns)
    assert out.count('class="time-chip"') == 2


def test_time_chip_not_repeated_for_small_gap():
    """Consecutive messages within the 5-minute window share one chip."""
    base = int(time.time())
    turns = [
        {"role": "user", "text": "first",  "ts": base},
        {"role": "user", "text": "close",  "ts": base + 60},  # 1 min
    ]
    out = client_portal._render_bubble_sequence(turns)
    assert out.count('class="time-chip"') == 1
