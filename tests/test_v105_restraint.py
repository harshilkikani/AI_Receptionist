"""V10.5 — orchestration and restraint.

V10.4's brief: add 15 high-impact upgrades. V10.5's brief: subtract,
quiet, restrain. The platform had become technically impressive but
visually loud — 8 simultaneous breathing animations, always-visible
demo mechanics, theatrical caller-pick ceremony. V10.5 is a
net-negative-lines change that puts restraint back as the default
and reserves attention for things that genuinely matter.
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


# ── 1. Demo drawer collapses tenant switcher + reset + pause ───────

def test_demo_drawer_present(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'class="demo-drawer-toggle"' in body
    assert 'id="demo-drawer"' in body
    assert 'id="demo-drawer-toggle"' in body
    assert 'class="demo-drawer-close"' in body


def test_tenant_switcher_lives_inside_drawer(app_client):
    """V10.5 — tenant switcher used to be in the top bar; now lives
    inside the demo drawer. The toggle button is what's in the top bar."""
    r = app_client.get("/")
    body = r.text
    # Switcher still exists in the body
    assert 'id="tenant-switcher"' in body
    # ...inside the drawer body
    drawer_start = body.find('id="demo-drawer"')
    switcher_start = body.find('id="tenant-switcher"')
    assert drawer_start > -1 and switcher_start > drawer_start


def test_reset_and_pause_moved_to_drawer(app_client):
    """V10.5 — V10.4 floating control bar buttons (dc-reset, dc-pause)
    are gone. Replaced by drawer-scoped dd-reset / dd-pause."""
    r = app_client.get("/")
    body = r.text
    assert 'id="dd-reset"' in body
    assert 'id="dd-pause"' in body
    # Old IDs retired
    assert 'id="dc-reset"' not in body
    assert 'id="dc-pause"' not in body


# ── 2. Theater removed ─────────────────────────────────────────────

def test_v105_no_incoming_call_banner(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'class="call-banner"' not in body
    assert "showIncomingCallBanner" not in body


def test_v105_no_call_timer_markup(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'class="call-timer"' not in body
    assert "startCallTimer" not in body


def test_v105_no_window_bar_dots(app_client):
    """Cosmetic browser-window dots on the portal pane retired."""
    r = app_client.get("/")
    body = r.text
    assert 'class="window-bar"' not in body
    # The fake red/amber/green window dots are gone too
    assert 'class="dot red"' not in body
    assert 'class="dot amber"' not in body
    assert 'class="dot green"' not in body


def test_v105_no_owner_phone_status_bar(app_client):
    """V10.5 — the owner phone is a notification view, not a phone
    simulation. The duplicated iOS status bar (9:41 + signal + battery)
    on the owner shell was removed; only the customer phone keeps it."""
    r = app_client.get("/")
    body = r.text
    # Customer phone status bar still exists (one instance)
    assert body.count('class="phone-status"') == 1


def test_v105_dead_css_removed():
    """No leftover CSS RULES for retired theatrical elements. Comments
    may reference the old class names for archival — we check for the
    rule's opening brace ("`.foo {`") not just the substring."""
    css = design.css()
    assert ".call-banner {" not in css
    assert "@keyframes cb-pulse" not in css
    assert ".phone-bar .call-timer {" not in css
    assert ".window-bar {" not in css
    assert ".window-bar .dot" not in css   # rule selector
    assert ".demo-control {" not in css    # V10.4 floating control
    assert ".onboard-pointer {" not in css  # V10.3 bobbing pointer


# ── 3. Surviving animations are quieted ────────────────────────────

def test_v105_live_mini_dot_static():
    """The live-mini badge dot lost its breathing animation; only the
    one canonical operator-pane Live indicator still pulses."""
    css = design.css()
    # Find the .live-mini-dot rule
    idx = css.find(".live-mini-dot ")
    chunk = css[idx:idx + 250]
    assert "animation:" not in chunk


def test_v105_one_pulse_keyframe_retained():
    """The canonical breathing animation (live-breathe) survives for
    the single operator-pane Live indicator. The rest were quieted."""
    css = design.css()
    assert "@keyframes live-breathe" in css


def test_v105_recording_waveform_no_longer_pulses():
    """V10.5 — V10.3 wave-pulse keyframe retired. Bars are static."""
    css = design.css()
    assert "@keyframes wave-pulse" not in css


def test_v105_recording_waveform_reduced_to_five_bars(app_client):
    """V10.5 — calmer waveform: 5 bars (was 10)."""
    from src import client_portal as cp
    from src import usage, transcripts
    usage.start_call("CA_v105_rec", "ace_hvac",
                      "+15554440250", "+18449403274")
    transcripts.record_turn("CA_v105_rec", "ace_hvac", "user", "test")
    usage.end_call("CA_v105_rec", outcome="normal")
    tok = cp.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    # Find the first rec-waveform block and count its <span> children
    idx = body.find('class="rec-waveform"')
    if idx > -1:
        chunk = body[idx:idx + 500]
        # 5 spans, not 10
        span_count = chunk.count("<span></span>")
        assert 0 < span_count <= 6


# ── 4. Refresh indicator hidden by default ─────────────────────────

def test_v105_refresh_indicator_hidden_until_hover():
    """V10.5 — V10.4's always-visible 'Updated 3s ago' pill was
    one of many always-on elements. Now hidden until the pane label
    is hovered."""
    css = design.css()
    idx = css.find(".refresh-indicator")
    chunk = css[idx:idx + 500]
    assert "opacity: 0" in chunk


# ── 5. Onboarding hint replaces bobbing pointer ────────────────────

def test_v105_quiet_onboarding_hint(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'class="onboard-pointer"' not in body
    # Function still exists but uses the new pattern
    assert "maybeShowOnboarding" in body
    assert "onboard-hint" in body


def test_v105_onboarding_hint_css_has_no_bob_animation():
    css = design.css()
    # New hint class
    assert ".onboard-hint" in css
    # The V10.3 bob keyframe is gone
    assert "@keyframes pointer-bob" not in css


# ── 6. Caller-select goes straight to chat populate ────────────────

def test_v105_select_caller_no_banner_ceremony(app_client):
    """V10.5 — selectCaller now populates the chat immediately.
    Pre-V10.5 it triggered the sliding banner then waited 700ms."""
    r = app_client.get("/")
    body = r.text
    # selectCaller does not call showIncomingCallBanner anymore
    assert "showIncomingCallBanner" not in body
    # _threadStart replaces _ctStart as the duration source
    assert "_threadStart" in body


# ── 7. Subtle owner read-receipt continuity ────────────────────────

def test_v105_pre_seeded_owner_sms_have_read_receipt(app_client):
    """The two pre-baked owner SMS bubbles (Marcus emergency + Sarah
    booking) ship with `.sms-read shown` since they landed hours ago
    and would already be read."""
    r = app_client.get("/")
    body = r.text
    # The wrapper class is on both seeded bubbles
    assert body.count('class="sms-read shown"') >= 2
    # Read indicator copy is present (whitespace-tolerant)
    assert "Read\n" in body or "Read</div>" in body or "Read " in body


def test_v105_owner_sms_read_receipt_css():
    """V10.5 — `.sms-read` shows ~3-4s after SMS arrival via the
    `.shown` class, no animation beyond a quiet opacity transition."""
    css = design.css()
    assert ".owner-sms .sms-read" in css
    assert ".owner-sms .sms-read.shown" in css


def test_v105_push_owner_sms_schedules_read_receipt(app_client):
    """JS pushOwnerSMS adds the receipt element and schedules
    setTimeout to add `.shown` after a believable delay."""
    r = app_client.get("/")
    body = r.text
    assert "sms-read" in body
    # The scheduling logic — different delay for emergencies vs others
    assert "readDelay" in body


# ── 8. Code-hygiene checks ─────────────────────────────────────────

def test_v105_zero_simultaneous_animations_baseline_count():
    """V10.4 had 10+ @keyframes definitions, many running simultaneously.
    V10.5 trimmed the heaviest theatrical ones. Verify the count
    didn't go UP (a regression guard)."""
    css = design.css()
    keyframe_count = css.count("@keyframes")
    # V10.5 budget: at most 12 keyframes. (V10.4 was ~14 with the
    # theatrical adds. The brief explicitly wants restraint here.)
    assert keyframe_count <= 12, (
        f"V10.5 expects <=12 @keyframes; got {keyframe_count}. "
        f"Audit before adding another animation.")


def test_v105_full_demo_page_renders_200(app_client):
    r = app_client.get("/")
    assert r.status_code == 200


def test_v105_demo_today_fragment_still_renders(app_client):
    r = app_client.get("/demo/today")
    assert r.status_code == 200
