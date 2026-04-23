"""V3.6 — booking capture tests."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import bookings, transcripts, usage
from src.bookings import BookingExtraction


# ── record_booking + list ─────────────────────────────────────────────

def test_record_and_list():
    b = bookings.record_booking(
        client_id="ace_hvac",
        caller_phone="+14155550142",
        caller_name="Sarah",
        address="42 Oak St",
        requested_when="Tuesday morning",
        service="furnace tune-up",
        notes="prefers morning",
        call_sid="CA_book_1",
    )
    assert b["id"].startswith("bk_")
    assert b["status"] == "pending"
    listed = bookings.list_bookings(client_id="ace_hvac")
    ids = {r["id"] for r in listed}
    assert b["id"] in ids


def test_get_booking_by_id():
    b = bookings.record_booking(client_id="ace_hvac", caller_phone="+1",
                                caller_name="test")
    retrieved = bookings.get_booking(b["id"])
    assert retrieved is not None
    assert retrieved["caller_name"] == "test"


def test_get_booking_unknown():
    assert bookings.get_booking("nope") is None


def test_list_bookings_filter_by_client():
    bookings.record_booking(client_id="ace_hvac", caller_phone="+1")
    bookings.record_booking(client_id="septic_pro", caller_phone="+1")
    ace = bookings.list_bookings(client_id="ace_hvac")
    assert all(b["client_id"] == "ace_hvac" for b in ace)


# ── extraction pipeline ───────────────────────────────────────────────

class _FakeLLM:
    def __init__(self, parsed_output, raise_exc: Exception = None):
        self._out = parsed_output
        self._raise = raise_exc
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw):
        if self._raise:
            raise self._raise
        return SimpleNamespace(parsed_output=self._out)


def _seed_scheduling_call(sid, *, client_id="ace_hvac",
                           outcome="normal", duration=60,
                           turns=None, intent="Scheduling"):
    """Seeds BOTH the transcripts table (for call_summary / booking
    extraction prompt) AND the turns table (for intent lookup)."""
    usage.start_call(sid, client_id, "+14155550142", "+18449403274")
    if turns:
        for role, text in turns:
            transcripts.record_turn(sid, client_id, role, text, intent=intent)
            if role == "assistant":
                usage.log_turn(sid, client_id, role,
                               input_tokens=50, output_tokens=10,
                               tts_chars=len(text), intent=intent)
    usage.end_call(sid, outcome=outcome)
    from src.usage import _connect, _init_schema, _db_lock
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        conn.execute("UPDATE calls SET duration_s=? WHERE call_sid=?",
                     (duration, sid))
        conn.close()


def test_extract_from_call_happy_path():
    _seed_scheduling_call("CA_ex_1", turns=[
        ("user", "hi, need my furnace tuned up"),
        ("assistant", "Sure — what's your name and address?"),
        ("user", "Sarah Mitchell, 42 Oak Street"),
        ("assistant", "Got it — how about Tuesday morning?"),
        ("user", "Perfect, Tuesday morning works"),
    ])
    extraction = BookingExtraction(
        should_book=True,
        caller_name="Sarah Mitchell",
        address="42 Oak Street",
        phone="+14155550142",
        when="Tuesday morning",
        service="furnace tune-up",
        notes=None,
    )
    result = bookings.maybe_extract_from_call(
        "CA_ex_1", llm_client=_FakeLLM(extraction))
    assert result is not None
    assert result["caller_name"] == "Sarah Mitchell"
    assert result["address"] == "42 Oak Street"
    assert result["service"] == "furnace tune-up"


def test_extract_skipped_when_should_book_false():
    _seed_scheduling_call("CA_ex_2", turns=[
        ("user", "just getting quotes"),
        ("assistant", "Sure — what kind of service?"),
    ])
    extraction = BookingExtraction(should_book=False)
    assert bookings.maybe_extract_from_call(
        "CA_ex_2", llm_client=_FakeLLM(extraction)) is None


def test_extract_skipped_on_non_normal_outcome():
    _seed_scheduling_call("CA_ex_3", outcome="spam_phrase",
                          turns=[("user", "hi")])
    assert bookings.maybe_extract_from_call(
        "CA_ex_3", llm_client=_FakeLLM(BookingExtraction(should_book=True))) is None


def test_extract_skipped_on_too_short():
    _seed_scheduling_call("CA_ex_4", duration=10,
                          turns=[("user", "book me")])
    assert bookings.maybe_extract_from_call(
        "CA_ex_4", llm_client=_FakeLLM(BookingExtraction(should_book=True))) is None


def test_extract_skipped_on_no_scheduling_intent():
    # Seed with intent=General (no scheduling)
    _seed_scheduling_call("CA_ex_5", intent="General",
                          turns=[("user", "hello"), ("assistant", "Hey")])
    assert bookings.maybe_extract_from_call(
        "CA_ex_5", llm_client=_FakeLLM(BookingExtraction(should_book=True))) is None


def test_extract_unknown_call():
    assert bookings.maybe_extract_from_call("CA_unknown") is None


def test_extract_handles_llm_failure():
    _seed_scheduling_call("CA_ex_fail", turns=[
        ("user", "book me"), ("assistant", "OK what address?"),
    ])
    result = bookings.maybe_extract_from_call(
        "CA_ex_fail",
        llm_client=_FakeLLM(None, raise_exc=RuntimeError("boom")),
    )
    assert result is None


# ── ICS generation ─────────────────────────────────────────────────────

def test_generate_ics_minimal():
    b = {"id": "bk_test_1", "caller_name": "Sarah",
         "caller_phone": "+14155550142", "address": "42 Oak",
         "service": "Furnace tune-up"}
    ics = bookings.generate_ics(b)
    assert "BEGIN:VCALENDAR" in ics
    assert "BEGIN:VEVENT" in ics
    assert "SUMMARY:Furnace tune-up" in ics
    assert "bk_test_1@ai-receptionist" in ics


def test_generate_ics_no_service_default_summary():
    ics = bookings.generate_ics({"id": "bk_x"})
    assert "SUMMARY:Service appointment" in ics


# ── admin route ────────────────────────────────────────────────────────

def test_admin_bookings_renders(monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()

    bookings.record_booking(
        client_id="ace_hvac", caller_phone="+14155550142",
        caller_name="Sarah", address="42 Oak", service="tune-up")
    import main
    importlib.reload(main)
    c = TestClient(main.app)
    r = c.get("/admin/bookings")
    assert r.status_code == 200
    assert "Sarah" in r.text
    assert "42 Oak" in r.text
    assert "tune-up" in r.text


def test_admin_bookings_empty_state(monkeypatch, tmp_path):
    import importlib
    from fastapi.testclient import TestClient
    from src import usage
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "fresh.db")
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    c = TestClient(main.app)
    r = c.get("/admin/bookings")
    assert r.status_code == 200
    assert "No bookings yet" in r.text
