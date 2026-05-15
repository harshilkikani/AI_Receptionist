"""V10.4 — 15 high-impact upgrades across UX fixes + realism + polish.

  Commit A (fixes 1-5):
    · industry context propagation
    · owner-phone dynamic name
    · mobile layout
    · status pill icons
    · sessionStorage conversation persistence

  Commit B (realism 6-10):
    · incoming-call banner
    · live call timer
    · end-of-call summary card
    · owner-phone notification badge
    · recording progress bar

  Commit C (polish 11-15):
    · smart refresh cadence (fast post-activity, slow idle)
    · "Updated Xs ago" indicator
    · keyboard shortcuts overlay
    · floating demo control (reset + pause)
    · smooth chat autoscroll
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


# ── 1. Industry context propagation ────────────────────────────────

def test_chat_in_model_accepts_industry():
    """The ChatIn pydantic model carries an optional industry param."""
    import main
    body = main.ChatIn(caller_id="x", message="y", industry="hvac")
    assert body.industry == "hvac"


def test_chat_with_industry_prepends_context_to_message(app_client, monkeypatch):
    """When industry='hvac' arrives in /chat, the LLM sees the user
    message with a small [Context: ...] cue prepended. The reply
    pipeline is monkeypatched so we can inspect what _run_pipeline
    received."""
    import main
    captured = {}
    def fake_pipeline(caller, message, **kw):
        captured["message"] = message
        return {"reply": "ok", "intent": "General", "priority": "low",
                "sentiment": "neutral", "caller": caller}
    monkeypatch.setattr(main, "_run_pipeline", fake_pipeline)
    # Ensure the caller exists
    from src import demo_seed
    demo_seed.register_personas_in_memory()
    r = app_client.post("/chat", json={
        "caller_id": "marcus",
        "message": "my AC is dead",
        "client_id": "septic_pro",
        "industry": "hvac",
    })
    assert r.status_code == 200
    msg = captured.get("message", "")
    assert msg.startswith("[Context:")
    assert "HVAC" in msg
    assert "my AC is dead" in msg


def test_chat_without_industry_passes_message_verbatim(app_client, monkeypatch):
    """No industry param → no context override. Pure message
    forwarded to _run_pipeline."""
    import main
    captured = {}
    def fake_pipeline(caller, message, **kw):
        captured["message"] = message
        return {"reply": "ok", "intent": "General", "priority": "low",
                "sentiment": "neutral", "caller": caller}
    monkeypatch.setattr(main, "_run_pipeline", fake_pipeline)
    from src import demo_seed
    demo_seed.register_personas_in_memory()
    r = app_client.post("/chat", json={
        "caller_id": "marcus",
        "message": "test plain",
        "client_id": "septic_pro",
    })
    assert r.status_code == 200
    assert captured.get("message") == "test plain"


def test_chat_septic_industry_uses_registry_fragment(app_client, monkeypatch):
    """V10.4: `industry='septic'` previously meant 'no override' — the
    tenant system prompt was already septic.

    V11.0: every industry (including septic) flows through
    industries.prompt_fragment(slug) for consistency. Septic now gets
    a registry-driven context cue; the cue reinforces the tenant
    prompt's existing septic identity rather than adding a different
    persona, so behavior is unchanged in practice."""
    import main
    captured = {}
    def fake_pipeline(caller, message, **kw):
        captured["message"] = message
        return {"reply": "ok", "intent": "General", "priority": "low",
                "sentiment": "neutral", "caller": caller}
    monkeypatch.setattr(main, "_run_pipeline", fake_pipeline)
    from src import demo_seed
    demo_seed.register_personas_in_memory()
    app_client.post("/chat", json={
        "caller_id": "marcus", "message": "hi",
        "client_id": "septic_pro", "industry": "septic",
    })
    # V11.0 — context fragment wraps the message
    assert "[Context:" in captured["message"]
    assert "septic" in captured["message"].lower()
    # User's actual message is still appended after the context cue
    assert captured["message"].endswith("hi")


def test_chat_real_estate_industry_uses_realty_context(app_client, monkeypatch):
    import main
    captured = {}
    def fake_pipeline(caller, message, **kw):
        captured["message"] = message
        return {"reply": "ok", "intent": "General", "priority": "low",
                "sentiment": "neutral", "caller": caller}
    monkeypatch.setattr(main, "_run_pipeline", fake_pipeline)
    from src import demo_seed
    demo_seed.register_personas_in_memory()
    app_client.post("/chat", json={
        "caller_id": "marcus", "message": "tour?",
        "client_id": "septic_pro", "industry": "real-estate",
    })
    assert "[Context:" in captured["message"]
    assert "real-estate" in captured["message"].lower() or "agency" in captured["message"].lower()


# ── 2. Owner-phone dynamic name ────────────────────────────────────

def test_tenant_switcher_carries_owner_per_industry(app_client):
    """data-owner attribute lives on every <option> so the JS can
    swap the owner-phone label."""
    r = app_client.get("/")
    body = r.text
    assert 'data-owner="Bob"' in body       # Septic
    assert 'data-owner="Mike"' in body      # HVAC
    assert 'data-owner="Lauren"' in body    # Realty


def test_demo_page_js_updates_owner_phone_label(app_client):
    """The tenant-switcher JS updates the .owner-shell .phone-bar
    .biz on every change, not just the customer phone."""
    r = app_client.get("/")
    body = r.text
    assert "owner-shell" in body
    assert ".owner-shell .phone-bar .biz" in body


# ── 3. Mobile layout pass ──────────────────────────────────────────

def test_mobile_breakpoint_present_in_css():
    """V10.4 — under 480px the owner-shell gets a smaller height so
    two-phone stacking fits on a single thumb-scroll."""
    css = design.css()
    assert ".owner-shell .phone-screen" in css


# ── 4. Status pill SVG glyphs ──────────────────────────────────────

def test_status_pill_emergency_uses_glyph():
    out = design.status_pill("emergency")
    assert "pill-glyph" in out
    assert "<svg" in out


def test_status_pill_answered_uses_check_glyph():
    out = design.status_pill("answered")
    assert "pill-glyph" in out


def test_status_pill_unknown_falls_back_to_dot():
    """Statuses without a dedicated glyph still get a dot (default)."""
    out = design.status_pill("never_heard_of_this_status")
    assert "pill-glyph" not in out
    assert '<span class="dot">' in out


def test_pill_glyph_css_class_present():
    css = design.css()
    assert ".pill .pill-glyph" in css


# ── 5. Conversation persistence (sessionStorage) ───────────────────

def test_demo_page_persists_conversation_state(app_client):
    r = app_client.get("/")
    body = r.text
    assert "saveChatState" in body
    assert "restoreChatState" in body
    assert "sessionStorage" in body
    assert "aircept_chat_state" in body


# ── 6. Incoming-call banner — RETIRED in V10.5 ─────────────────────

def test_v105_incoming_call_banner_removed(app_client):
    """V10.5 — the V10.4 sliding 'Marcus is calling' banner was demo
    theater. Caller-select now populates the chat directly. The
    banner markup must not appear on the page."""
    r = app_client.get("/")
    body = r.text
    assert 'class="call-banner"' not in body
    assert 'id="call-banner"' not in body
    assert "showIncomingCallBanner" not in body


def test_v105_call_banner_css_gone():
    css = design.css()
    assert ".call-banner" not in css


# ── 7. Live call timer — RETIRED in V10.5 ──────────────────────────

def test_v105_call_timer_markup_removed(app_client):
    """V10.5 — the V10.4 ticking call timer was an attention-grabber.
    Thread duration is still surfaced once on the end-of-call summary
    card via the new `_threadStart` variable."""
    r = app_client.get("/")
    body = r.text
    assert 'class="call-timer"' not in body
    assert 'id="call-timer"' not in body
    assert "startCallTimer" not in body
    assert "_threadStart" in body   # the V10.5 replacement


def test_v105_call_timer_css_gone():
    css = design.css()
    assert ".phone-bar .call-timer" not in css


# ── 8. End-of-call summary card ────────────────────────────────────

def test_demo_page_has_end_of_call_summary(app_client):
    r = app_client.get("/")
    body = r.text
    assert "maybeShowCallSummary" in body
    assert "call-summary-card" in body


def test_summary_card_css_present():
    css = design.css()
    assert ".phone-conv .call-summary-card" in css
    assert "@keyframes cs-slide" in css


# ── 9. Owner-phone notification badge ──────────────────────────────

def test_owner_badge_in_markup(app_client):
    r = app_client.get("/")
    body = r.text
    assert 'class="biz-badge"' in body
    assert 'id="owner-badge"' in body
    assert "bumpOwnerBadge" in body


def test_biz_badge_css_present():
    css = design.css()
    assert ".biz-badge" in css


# ── 10. Recording progress bar ─────────────────────────────────────

def test_recording_progress_present_in_player(app_client):
    """Voice-channel call previews include a rec-progress bar."""
    from src import usage, transcripts, client_portal as cp
    usage.start_call("CA_v104_rec", "ace_hvac",
                      "+15554440250", "+18449403274")
    transcripts.record_turn("CA_v104_rec", "ace_hvac", "user", "test")
    usage.end_call("CA_v104_rec", outcome="normal")
    tok = cp.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    body = r.text
    assert "rec-progress" in body or "rec-progress-fill" in body


def test_rec_progress_css_animates_to_full():
    css = design.css()
    assert ".rec-progress-fill" in css
    assert ".rec-player.playing .rec-progress-fill" in css


# ── 11. Smart refresh cadence ──────────────────────────────────────

def test_demo_page_has_smart_polling_js(app_client):
    r = app_client.get("/")
    body = r.text
    assert "enterFastPoll" in body
    assert "_restartPoll" in body
    assert "1500" in body   # fast cadence
    assert "10000" in body  # idle cadence


# ── 12. "Updated Xs ago" refresh indicator ─────────────────────────

def test_refresh_indicator_wired(app_client):
    r = app_client.get("/")
    body = r.text
    assert "refresh-indicator" in body
    assert "_updateRefreshLabel" in body
    assert "Updated just now" in body


def test_refresh_indicator_css_present():
    css = design.css()
    assert ".refresh-indicator" in css


# ── 13. Keyboard shortcuts overlay ─────────────────────────────────

def test_shortcut_overlay_wired(app_client):
    r = app_client.get("/")
    body = r.text
    assert "shortcut-overlay" in body
    assert "Keyboard shortcuts" in body


def test_shortcut_handler_listens_for_question_key(app_client):
    r = app_client.get("/")
    body = r.text
    # `?` toggles the overlay
    assert 'e.key === "?"' in body or 'e.key==="?"' in body


def test_shortcut_handler_listens_for_cmd_k(app_client):
    r = app_client.get("/")
    body = r.text
    # Cmd/Ctrl + K focuses the chat input
    assert "metaKey" in body or "ctrlKey" in body


def test_shortcut_modal_css_present():
    css = design.css()
    assert ".shortcut-overlay" in css
    assert ".shortcut-modal" in css


# ── 14. Floating demo control + /demo/reset endpoint ───────────────

def test_demo_control_moved_to_drawer(app_client):
    """V10.5 — the V10.4 always-visible floating demo control bar
    was collapsed into a single ⋯ drawer toggle in the top bar.
    Pause + reset live in the drawer body now."""
    r = app_client.get("/")
    body = r.text
    # The drawer markup
    assert 'class="demo-drawer"' in body
    assert 'id="demo-drawer"' in body
    assert 'id="demo-drawer-toggle"' in body
    # Reset + pause are now drawer buttons
    assert 'id="dd-reset"' in body
    assert 'id="dd-pause"' in body
    # The V10.4 free-floating control bar must not appear anymore
    assert 'class="demo-control"' not in body


def test_demo_drawer_css_present():
    css = design.css()
    assert ".demo-drawer" in css
    assert ".demo-drawer-toggle" in css
    assert ".demo-control" not in css  # old class retired


def test_demo_reset_endpoint_exists(app_client):
    r = app_client.post("/demo/reset")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_demo_reset_purges_and_reseeds(app_client):
    """After /demo/reset, seeded scenarios are present again."""
    from src import usage, demo_seed
    # Seed first
    demo_seed.seed_septic_pro()
    before = len(usage.list_conversation_partners("septic_pro", limit=50))
    r = app_client.post("/demo/reset")
    assert r.status_code == 200
    after = len(usage.list_conversation_partners("septic_pro", limit=50))
    # Same partner count after purge+reseed
    assert before == after


# ── 15. Smooth autoscroll ──────────────────────────────────────────

def test_chat_autoscroll_uses_smooth_behavior(app_client):
    r = app_client.get("/")
    body = r.text
    assert "_smoothScrollToBottom" in body
    assert "behavior" in body
    assert '"smooth"' in body or "'smooth'" in body
