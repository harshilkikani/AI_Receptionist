"""V4 — call transcript storage + per-call detail page tests."""
from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient

from src import client_portal, transcripts, usage


# ── record + read path ────────────────────────────────────────────────

def test_record_and_retrieve():
    sid = "CA_tr_1"
    transcripts.record_turn(sid, "ace_hvac", "user", "hi", intent="General")
    transcripts.record_turn(sid, "ace_hvac", "assistant", "Hey — what's up?",
                            intent="General")
    rows = transcripts.get_transcript(sid)
    assert len(rows) == 2
    assert rows[0]["role"] == "user"
    assert rows[0]["text"] == "hi"
    assert rows[1]["role"] == "assistant"
    assert rows[1]["intent"] == "General"


def test_record_no_op_on_empty_sid():
    transcripts.record_turn("", "ace_hvac", "user", "no sid")
    transcripts.record_turn("   ", "ace_hvac", "user", "whitespace")
    # Nothing was recorded
    assert transcripts.get_transcript("") == []
    assert transcripts.get_transcript("   ") == []


def test_record_no_op_on_empty_text():
    transcripts.record_turn("CA_x", "ace_hvac", "user", "")
    assert transcripts.get_transcript("CA_x") == []


def test_get_transcript_ordered():
    sid = "CA_order"
    now = int(time.time())
    transcripts.record_turn(sid, "ace_hvac", "user", "first", ts=now - 10)
    transcripts.record_turn(sid, "ace_hvac", "user", "third", ts=now + 10)
    transcripts.record_turn(sid, "ace_hvac", "user", "second", ts=now)
    rows = transcripts.get_transcript(sid)
    assert [r["text"] for r in rows] == ["first", "second", "third"]


def test_get_call_meta_none_for_unknown():
    assert transcripts.get_call_meta("CA_does_not_exist") is None


def test_get_call_meta_returns_row():
    usage.start_call("CA_meta_1", "ace_hvac", "+14155550142", "+18449403274")
    usage.end_call("CA_meta_1", outcome="normal")
    meta = transcripts.get_call_meta("CA_meta_1")
    assert meta is not None
    assert meta["client_id"] == "ace_hvac"
    assert meta["outcome"] == "normal"


# ── admin detail page ────────────────────────────────────────────────

@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


def test_admin_call_detail_404_for_unknown(app_client):
    r = app_client.get("/admin/call/CA_not_there")
    assert r.status_code == 404


def test_admin_call_detail_renders_transcript(app_client):
    sid = "CA_admin_detail"
    usage.start_call(sid, "ace_hvac", "+14155550142", "+18449403274")
    transcripts.record_turn(sid, "ace_hvac", "user", "my furnace is broken")
    transcripts.record_turn(sid, "ace_hvac", "assistant", "Got it — address?")
    usage.end_call(sid, outcome="normal")

    r = app_client.get(f"/admin/call/{sid}")
    assert r.status_code == 200
    assert "my furnace is broken" in r.text
    assert "Got it" in r.text
    assert "ace_hvac" in r.text


def test_admin_call_detail_handles_no_transcript(app_client):
    sid = "CA_no_conv"
    usage.start_call(sid, "ace_hvac", "+14155550142", "+18449403274")
    usage.end_call(sid, outcome="normal")
    r = app_client.get(f"/admin/call/{sid}")
    assert r.status_code == 200
    assert "No transcript captured" in r.text


# ── client portal detail page ────────────────────────────────────────

def test_portal_call_detail_shows_own_call(app_client):
    sid = "CA_portal_detail"
    usage.start_call(sid, "ace_hvac", "+14155550199", "+18449403274")
    transcripts.record_turn(sid, "ace_hvac", "user", "pump-out please")
    transcripts.record_turn(sid, "ace_hvac", "assistant", "Sure, what day?")
    usage.end_call(sid, outcome="normal")

    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/call/{sid}?t={tok}")
    assert r.status_code == 200
    assert "pump-out please" in r.text


def test_portal_call_detail_rejects_wrong_tenant(app_client):
    """A client can't read another tenant's transcript even with valid own token."""
    sid = "CA_xtenant"
    usage.start_call(sid, "septic_pro", "+14155550100", "+18885551212")
    transcripts.record_turn(sid, "septic_pro", "user", "don't show this")
    transcripts.record_turn(sid, "septic_pro", "assistant", "sensitive reply")
    usage.end_call(sid, outcome="normal")

    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/call/{sid}?t={tok}")
    assert r.status_code == 404
    # And the sensitive text does not appear in the response
    assert "sensitive reply" not in r.text


def test_portal_call_detail_rejects_bad_token(app_client):
    sid = "CA_bad_tok"
    usage.start_call(sid, "ace_hvac", "+14155550142", "+18449403274")
    usage.end_call(sid, outcome="normal")
    r = app_client.get(f"/client/ace_hvac/call/{sid}?t=nope")
    assert r.status_code == 403


# ── pipeline integration (record_turn called during _run_pipeline) ───

def test_pipeline_captures_transcript(monkeypatch):
    """Verify _run_pipeline records both user and assistant turns."""
    # Monkey-patch llm.chat_with_usage so we don't hit the API
    import main
    import llm
    from llm import ChatResponse

    fake_reply = ChatResponse(reply="Sure, what's the address?",
                              intent="Scheduling", priority="low")
    monkeypatch.setattr(llm, "chat_with_usage",
                        lambda *a, **k: (fake_reply, (50, 10)))

    caller = {"id": "5555551111", "phone": "+15555551111", "type": "new",
              "history": [], "conversation": []}
    main._run_pipeline(
        caller, "I'd like to schedule a service",
        client={"id": "ace_hvac", "name": "Ace HVAC", "plan": {}},
        call_sid="CA_pipeline_1",
    )
    rows = transcripts.get_transcript("CA_pipeline_1")
    assert len(rows) == 2
    roles = [r["role"] for r in rows]
    assert roles == ["user", "assistant"]
    assert "schedule a service" in rows[0]["text"]
    assert "address" in rows[1]["text"]
