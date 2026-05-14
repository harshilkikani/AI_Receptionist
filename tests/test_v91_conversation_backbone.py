"""V9.1 — backend for the unified conversations view.

Covers:
  - feedback.maybe_send_followup persists its body to transcripts so the
    follow-up SMS shows up in the conversation timeline (not just as a
    segment count in the sms table).
  - transcripts.list_by_phone returns calls + SMS turns for one phone
    number, chronologically, regardless of how the phone is stored
    (raw / normalized / E.164-prefixed).
  - usage.list_conversation_partners groups voice + SMS activity per
    phone number with last_ts / last_channel / counts.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from src import feedback, transcripts, usage


def _backdate_call(call_sid: str, ts: int) -> None:
    """Rewrite start_ts after usage.start_call so tests can simulate
    calls from days/weeks ago. The since_ts filter on
    list_conversation_partners needs real chronology to exercise."""
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        conn.execute("UPDATE calls SET start_ts = ? WHERE call_sid = ?",
                     (ts, call_sid))
        conn.close()


# ── feedback persistence ───────────────────────────────────────────────

def test_feedback_followup_records_transcript(monkeypatch):
    """The follow-up SMS body must land in transcripts so it shows up
    in the unified conversations timeline."""
    monkeypatch.setenv("TWILIO_NUMBER", "+18449403274")
    monkeypatch.setattr(feedback, "_enforcement_active", lambda: True)

    twilio = MagicMock()
    twilio.messages.create.return_value = None

    client = {"id": "ace_hvac", "name": "Ace HVAC"}
    out = feedback.maybe_send_followup(
        "CA_v91_followup", client,
        caller_phone="+15551112222",
        outcome="normal", duration_s=60, emergency=False,
        twilio_client=twilio,
    )
    assert out["sent"]

    turns = transcripts.get_transcript("CA_v91_followup")
    assert any(t["role"] == "assistant" and t.get("intent") == "follow_up"
               for t in turns), f"follow-up turn not persisted: {turns}"


def test_feedback_followup_transcript_failure_does_not_unsend(monkeypatch):
    """If transcripts.record_turn raises, the SMS still counts as sent
    (we already shipped it). The result is OK; only a warning logged."""
    monkeypatch.setenv("TWILIO_NUMBER", "+18449403274")
    monkeypatch.setattr(feedback, "_enforcement_active", lambda: True)
    monkeypatch.setattr(transcripts, "record_turn",
                         lambda *a, **k: (_ for _ in ()).throw(
                             RuntimeError("db down")))

    twilio = MagicMock()
    client = {"id": "ace_hvac"}
    out = feedback.maybe_send_followup(
        "CA_v91_fail", client,
        caller_phone="+15551110000",
        outcome="normal", duration_s=60, emergency=False,
        twilio_client=twilio,
    )
    assert out["sent"] is True
    assert out["reason"] == "ok"


# ── transcripts.list_by_phone ──────────────────────────────────────────

def _seed_call(call_sid: str, client_id: str, from_number: str,
                start_offset_s: int = 0) -> None:
    """Helper: write a call row + a couple of transcript turns."""
    usage.start_call(call_sid, client_id, from_number, "+18449403274")
    transcripts.record_turn(call_sid, client_id, "user",
                              "Hi, I need a pump-out",
                              ts=int(time.time()) - 100 + start_offset_s)
    transcripts.record_turn(call_sid, client_id, "assistant",
                              "Sure, what's the address?",
                              ts=int(time.time()) - 90 + start_offset_s)


def _seed_sms_exchange(client_id: str, phone: str,
                        when: int, inbound: str, reply: str) -> None:
    """Helper: write transcripts for an SMS turn pair using the
    SMS_{digits} pseudo-SID the live path uses."""
    from memory import normalize_phone
    norm = normalize_phone(phone)
    sid = f"SMS_{norm}"
    transcripts.record_turn(sid, client_id, "user",  inbound, ts=when)
    transcripts.record_turn(sid, client_id, "assistant", reply,
                              ts=when + 1)
    usage.log_sms(sid, client_id, phone, inbound, direction="inbound")
    usage.log_sms(sid, client_id, phone, reply, direction="outbound")


def test_list_by_phone_returns_voice_turns():
    _seed_call("CA_v91_call1", "ace_hvac", "+15551234567")
    out = transcripts.list_by_phone("ace_hvac", "+15551234567")
    assert len(out) == 2
    assert all(t["channel"] == "voice" for t in out)
    assert any("pump-out" in t["text"] for t in out)


def test_list_by_phone_returns_sms_turns():
    when = int(time.time()) - 60
    _seed_sms_exchange("ace_hvac", "+15552223333", when,
                        "How much for a tune-up?",
                        "Hey — tune-ups start at $129.")
    out = transcripts.list_by_phone("ace_hvac", "+15552223333")
    assert len(out) == 2
    assert all(t["channel"] == "sms" for t in out)


def test_list_by_phone_unifies_voice_and_sms():
    """V9.1 contract: one phone number's full history across both
    channels, chronological."""
    base = int(time.time()) - 1000
    # 1) voice call yesterday
    _seed_call("CA_v91_unified", "ace_hvac", "+15553334444")
    # 2) SMS exchange later
    _seed_sms_exchange("ace_hvac", "+15553334444", base + 500,
                        "Forgot to ask about the price",
                        "No worries — $475 for a standard pump-out.")
    out = transcripts.list_by_phone("ace_hvac", "+15553334444")
    assert len(out) == 4
    # SMS turns appear AFTER the voice turns (later timestamps)
    channels = [t["channel"] for t in out]
    assert channels.count("voice") == 2
    assert channels.count("sms") == 2


def test_list_by_phone_isolates_tenants():
    """Phone 555-0001 has activity under both ace_hvac and septic_pro;
    the portal-side caller must NEVER see the other tenant's data."""
    _seed_call("CA_v91_ace", "ace_hvac", "+15550000001")
    _seed_call("CA_v91_septic", "septic_pro", "+15550000001")
    ace = transcripts.list_by_phone("ace_hvac", "+15550000001")
    septic = transcripts.list_by_phone("septic_pro", "+15550000001")
    # Each tenant sees only its own turns
    assert len(ace) == 2
    assert len(septic) == 2
    ace_sids = {t["call_sid"] for t in ace}
    septic_sids = {t["call_sid"] for t in septic}
    assert ace_sids.isdisjoint(septic_sids)


def test_list_by_phone_normalizes_input():
    """Caller may type '(555) 222-3333' or '+15552223333' — both
    must resolve to the same conversation."""
    when = int(time.time()) - 60
    _seed_sms_exchange("ace_hvac", "+15552223333", when, "Hi", "Hi back")
    out1 = transcripts.list_by_phone("ace_hvac", "(555) 222-3333")
    out2 = transcripts.list_by_phone("ace_hvac", "+15552223333")
    out3 = transcripts.list_by_phone("ace_hvac", "15552223333")
    assert len(out1) == len(out2) == len(out3) == 2


def test_list_by_phone_empty_inputs():
    assert transcripts.list_by_phone("", "+15551111111") == []
    assert transcripts.list_by_phone("ace_hvac", "") == []
    assert transcripts.list_by_phone("ace_hvac", "abc") == []


def test_list_by_phone_returns_chronological():
    base = int(time.time()) - 3600
    # Two SMS exchanges, second is later
    _seed_sms_exchange("ace_hvac", "+15554445555", base, "first", "ack1")
    _seed_sms_exchange("ace_hvac", "+15554445555", base + 100,
                        "second", "ack2")
    out = transcripts.list_by_phone("ace_hvac", "+15554445555")
    timestamps = [t["ts"] for t in out]
    assert timestamps == sorted(timestamps)


# ── usage.list_conversation_partners ──────────────────────────────────

def test_list_partners_empty_tenant():
    assert usage.list_conversation_partners("nobody_here") == []


def test_list_partners_groups_voice_calls():
    _seed_call("CA_v91_p1", "ace_hvac", "+15556660001")
    _seed_call("CA_v91_p2", "ace_hvac", "+15556660001")
    _seed_call("CA_v91_p3", "ace_hvac", "+15556660002")
    out = usage.list_conversation_partners("ace_hvac")
    assert len(out) == 2
    # First partner has 2 calls
    by_phone = {p["phone"]: p for p in out}
    assert by_phone["+15556660001"]["calls"] == 2
    assert by_phone["+15556660002"]["calls"] == 1


def test_list_partners_includes_sms_only():
    """A partner with only SMS (no voice call) still appears."""
    when = int(time.time()) - 60
    _seed_sms_exchange("ace_hvac", "+15557770001", when, "hi", "hi back")
    out = usage.list_conversation_partners("ace_hvac")
    phones = {p["phone"] for p in out}
    # Stored canonicalized as +15557770001 once it goes through normalize
    assert any("5557770001" in p for p in phones)


def test_list_partners_ordered_by_recency():
    base = int(time.time()) - 1000
    usage.start_call("CA_old", "ace_hvac", "+15550000010", "+18449403274")
    _backdate_call("CA_old", base)
    usage.start_call("CA_new", "ace_hvac", "+15550000020", "+18449403274")
    _backdate_call("CA_new", base + 500)
    out = usage.list_conversation_partners("ace_hvac")
    assert out[0]["phone"] == "+15550000020"


def test_list_partners_combines_voice_and_sms_for_same_phone():
    """The same phone number with both voice and SMS history shows up
    as ONE partner with counts for both channels."""
    base = int(time.time()) - 1000
    _seed_call("CA_combo", "ace_hvac", "+15558880001")
    _seed_sms_exchange("ace_hvac", "+15558880001", base + 500,
                        "follow up", "noted")
    out = usage.list_conversation_partners("ace_hvac")
    # Should have ONE partner, not two
    matching = [p for p in out if "5558880001" in p["phone"]]
    assert len(matching) == 1
    assert matching[0]["calls"] >= 1
    assert matching[0]["messages"] >= 1


def test_list_partners_isolates_tenants():
    _seed_call("CA_p_ace", "ace_hvac", "+15559990001")
    _seed_call("CA_p_septic", "septic_pro", "+15559990002")
    ace = usage.list_conversation_partners("ace_hvac")
    septic = usage.list_conversation_partners("septic_pro")
    assert any("+15559990001" in p["phone"] for p in ace)
    assert not any("+15559990002" in p["phone"] for p in ace)
    assert any("+15559990002" in p["phone"] for p in septic)
    assert not any("+15559990001" in p["phone"] for p in septic)


def test_list_partners_respects_limit():
    for i in range(7):
        usage.start_call(f"CA_lim_{i}", "ace_hvac",
                          f"+155500001{i:02d}", "+18449403274")
    out = usage.list_conversation_partners("ace_hvac", limit=3)
    assert len(out) == 3


def test_list_partners_since_ts_filter():
    """`since_ts` should drop older activity from voice AND SMS sides."""
    base = int(time.time()) - 7 * 86400
    usage.start_call("CA_old", "ace_hvac", "+15551110001", "+18449403274")
    _backdate_call("CA_old", base)
    usage.start_call("CA_new", "ace_hvac", "+15552220002", "+18449403274")
    _backdate_call("CA_new", base + 6 * 86400)
    cutoff = base + 3 * 86400
    out = usage.list_conversation_partners("ace_hvac", since_ts=cutoff)
    phones = {p["phone"] for p in out}
    assert "+15552220002" in phones
    assert "+15551110001" not in phones
