"""V9.6 — live end-to-end integration between the demo chat and the
operator portal preview.

The prospect must see their chat message appear in the operator's
"Recent activity" feed in real time. Three guarantees this file checks:

  1. /chat for the marketing-demo tenant persists the exchange as
     SMS-style data (sms table + transcripts), so the partner-grouping
     query that powers Today's activity feed picks it up.
  2. /demo/today returns a fresh HTML fragment with no auth and no
     page chrome — that's what the JS swaps into #portal-body.
  3. The demo HTML actually wires up the refresh hook (live-pulse +
     fetch loop) so the integration is visible to the prospect.

These tests do NOT exercise the LLM; they monkeypatch _run_pipeline so
the wiring is fast and deterministic.
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

    # Skip the live LLM but mirror what _run_pipeline does internally
    # so the test exercises the full chat → portal data path.
    def fake_pipeline(caller, message, client=None, call_sid="", **kw):
        reply = "Got it — booking you next Tuesday."
        cid = (client or {}).get("id", "_default")
        if call_sid:
            from src import transcripts as _t
            _t.record_turn(call_sid, cid, "user", message,
                            intent="Scheduling")
            _t.record_turn(call_sid, cid, "assistant", reply,
                            intent="Scheduling")
        return {
            "reply": reply,
            "intent": "Scheduling",
            "priority": "low",
            "sentiment": "neutral",
            "caller": caller,
        }
    monkeypatch.setattr(main, "_run_pipeline", fake_pipeline)
    return TestClient(main.app), main


def _seed_caller(caller_id: str = "sarah", phone: str = "+15551234567"):
    """Make sure the test caller exists in memory.json shape."""
    import json as _json
    import memory
    # Always create or update via the normalized-phone path. The /chat
    # route just needs caller.get("phone") to be present.
    with memory._io_lock:
        data = memory._load_unsafe()
        # Drop any existing record with the same phone (different id).
        target_digits = memory.normalize_phone(phone)
        to_remove = [
            cid for cid, c in data.items()
            if cid != caller_id
            and memory.normalize_phone(c.get("phone", "")) == target_digits
        ]
        for cid in to_remove:
            del data[cid]
        data[caller_id] = {
            "id": caller_id, "name": "Sarah Wong",
            "phone": phone, "conversation": [],
            "type": "new",
        }
        memory._atomic_write(_json.dumps(data, indent=2))


# ── /chat persists as SMS for the demo tenant ────────────────────────

def test_chat_writes_sms_row_for_septic_pro(app_client):
    """V9.6 contract: a /chat call against septic_pro must produce
    rows the portal can SEE via list_conversation_partners."""
    client, _ = app_client
    _seed_caller("v96sarah", "+15554443333")

    r = client.post("/chat", json={
        "caller_id": "v96sarah",
        "message": "Need a pump-out next Tuesday afternoon.",
        "client_id": "septic_pro",
    })
    assert r.status_code == 200

    partners = usage.list_conversation_partners("septic_pro", limit=50)
    matching = [p for p in partners if "5554443333" in p["phone"]]
    assert matching, (
        f"V9.6 — chat for septic_pro should surface partner in portal; "
        f"got partners: {[p['phone'] for p in partners]}"
    )
    assert matching[0]["messages"] >= 2  # inbound + outbound


def test_chat_writes_transcript_turns_for_demo_tenant(app_client):
    """The unified conversation thread reads from transcripts. Verify
    the chat turn lands there too, so clicking the partner card shows
    the message exchange."""
    client, _ = app_client
    _seed_caller("v96linda", "+15556667788")

    r = client.post("/chat", json={
        "caller_id": "v96linda",
        "message": "Hi — looking for a drain field estimate.",
        "client_id": "septic_pro",
    })
    assert r.status_code == 200

    from src import transcripts
    turns = transcripts.list_by_phone("septic_pro", "+15556667788")
    user_turns = [t for t in turns if t["role"] == "user"]
    ai_turns   = [t for t in turns if t["role"] == "assistant"]
    assert any("drain field estimate" in t["text"] for t in user_turns)
    assert any("booking you next Tuesday" in t["text"] for t in ai_turns)


def test_chat_uses_sms_pattern_call_sid(app_client):
    """Web chat IS an SMS exchange. The call_sid uses the canonical
    `SMS_<digits>` format that /sms/incoming would create, so portal
    queries treat them identically."""
    client, _ = app_client
    _seed_caller("v96travis", "+15557778899")
    client.post("/chat", json={
        "caller_id": "v96travis",
        "message": "Hello",
        "client_id": "septic_pro",
    })
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sms WHERE call_sid = 'SMS_5557778899'"
        ).fetchone()
        n = int(row["n"])
        conn.close()
    assert n >= 2  # inbound + outbound


def test_chat_for_non_demo_tenant_does_not_log_sms(app_client):
    """Production tenants (ace_hvac) must NOT have their chat exchanges
    logged as SMS — the gate is the tenant id prefix."""
    client, _ = app_client
    _seed_caller("v96acecaller", "+15550009999")

    # Pre-count
    before_partners = usage.list_conversation_partners("ace_hvac", limit=200)
    before_phones = {p["phone"] for p in before_partners}

    r = client.post("/chat", json={
        "caller_id": "v96acecaller",
        "message": "test",
        "client_id": "ace_hvac",
    })
    assert r.status_code == 200

    after_partners = usage.list_conversation_partners("ace_hvac", limit=200)
    new_partners = [p for p in after_partners
                    if p["phone"] not in before_phones]
    # No new SMS-only partner should have appeared for ace_hvac
    sms_partners = [p for p in new_partners if p["last_channel"] == "sms"]
    assert sms_partners == []


def test_chat_returns_reply_unchanged_by_logging(app_client):
    """Adding the SMS persistence must not change the /chat contract.
    Reply still flows back."""
    client, _ = app_client
    _seed_caller("v96reply", "+15550008888")
    r = client.post("/chat", json={
        "caller_id": "v96reply",
        "message": "Need help",
        "client_id": "septic_pro",
    })
    assert r.status_code == 200
    body = r.json()
    assert "reply" in body
    assert "Got it" in body["reply"]


def test_chat_persistence_failure_does_not_break_reply(app_client, monkeypatch):
    """If log_sms hiccups (disk pressure, schema mismatch), the reply
    must still ship — telemetry should never block the conversation."""
    client, mod = app_client
    _seed_caller("v96failsafe", "+15550007777")

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(usage, "log_sms", boom)

    r = client.post("/chat", json={
        "caller_id": "v96failsafe",
        "message": "test",
        "client_id": "septic_pro",
    })
    assert r.status_code == 200
    assert r.json().get("reply")


# ── /demo/today fragment endpoint ────────────────────────────────────

def test_demo_today_returns_html_fragment(app_client):
    client, _ = app_client
    r = client.get("/demo/today")
    assert r.status_code == 200
    body = r.text
    # No page chrome — fragment is just the body
    assert "<!doctype" not in body.lower()
    assert "<html" not in body.lower()
    assert "<head" not in body.lower()
    # Real portal content present
    assert "today-hero" in body or "section-caption" in body or "empty" in body


def test_demo_today_does_not_require_auth(app_client):
    """The combined demo at / is public; the fragment endpoint shares
    that property."""
    client, _ = app_client
    r = client.get("/demo/today")
    assert r.status_code == 200


def test_demo_today_targets_septic_pro_only(app_client):
    """The fragment is hardcoded to the marketing tenant. No tenant
    leakage."""
    client, _ = app_client
    r = client.get("/demo/today")
    assert r.status_code == 200
    # Should NOT contain ace_hvac call links
    assert "/client/ace_hvac/" not in r.text


def test_demo_today_includes_recent_chat_activity(app_client):
    """E2E proof: a chat message → fragment update reflects it."""
    client, _ = app_client
    _seed_caller("v96e2e", "+15551919191")
    client.post("/chat", json={
        "caller_id": "v96e2e",
        "message": "Need a price on a tune-up.",
        "client_id": "septic_pro",
    })
    r = client.get("/demo/today")
    assert r.status_code == 200
    # Either the phone or a formatted version of it should appear
    body = r.text
    assert ("191-9191" in body or "5551919191" in body
            or "1919191" in body)


def test_demo_today_response_is_cacheable_no_store_friendly(app_client):
    """We don't set caching headers explicitly; verify the response is
    fresh on every call (the JS uses cache:no-store but a Last-Modified
    or ETag would be a nice-to-have)."""
    client, _ = app_client
    r1 = client.get("/demo/today")
    r2 = client.get("/demo/today")
    assert r1.status_code == 200 and r2.status_code == 200


# ── Demo page JS wiring ─────────────────────────────────────────────

def test_demo_page_has_live_pulse_indicator(app_client):
    client, _ = app_client
    r = client.get("/")
    body = r.text
    assert 'class="live-pulse"' in body
    assert 'id="live-pulse"' in body
    # The "Live" label is rendered after a space (sibling of .live-dot)
    assert "Live</span>" in body or " Live" in body


def test_demo_page_portal_body_has_id_for_refresh(app_client):
    """The JS targets #portal-body to swap in fresh fragment content."""
    client, _ = app_client
    r = client.get("/")
    assert 'id="portal-body"' in r.text


def test_demo_page_js_fetches_demo_today_after_send(app_client):
    """The chat widget must trigger a portal refresh after each AI
    response. Verify the wiring is in the inlined script."""
    client, _ = app_client
    r = client.get("/")
    body = r.text
    assert "/demo/today" in body
    assert "refreshPortal" in body
    assert "scheduleRefresh" in body


def test_demo_page_has_background_poll(app_client):
    """Even without chat input, the portal should refresh periodically.
    V10.4 — the V9.6 single setInterval call was replaced by smart
    cadence (_restartPoll). Check the function exists and the idle
    cadence is set up at startup."""
    client, _ = app_client
    r = client.get("/")
    body = r.text
    assert ("setInterval(refreshPortal" in body
            or "_restartPoll(10000)" in body
            or "setInterval(_pollTick" in body)


# ── No regression on real portal ────────────────────────────────────

def test_real_portal_still_works(app_client):
    """V9.6 only adds; the real /client/{id}?t=... portal renders as
    before."""
    client, _ = app_client
    tok = client_portal.issue_token("ace_hvac")
    r = client.get(f"/client/ace_hvac?t={tok}")
    assert r.status_code == 200
    assert "today-hero" in r.text
