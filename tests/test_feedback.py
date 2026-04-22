"""P11 — post-call feedback SMS + response capture."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import feedback, usage


# ── classify ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    ("yes", "yes"),
    ("YES", "yes"),
    ("yeah thanks", "yes"),
    ("yup", "yes"),
    ("Sure", "yes"),
    ("absolutely", "yes"),
    ("no", "no"),
    ("NO", "no"),
    ("nope", "no"),
    ("nah, not really", "no"),
    ("kinda", None),
    ("", None),
    (None, None),
])
def test_classify(body, expected):
    assert feedback.classify(body) == expected


# ── maybe_send_followup guards ────────────────────────────────────────

def _fake_tw(capture: list):
    return SimpleNamespace(messages=SimpleNamespace(
        create=lambda to, from_, body: capture.append((to, body)) or None))


def test_skip_non_normal_outcome(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_FEEDBACK_SMS", "true")
    cap = []
    r = feedback.maybe_send_followup(
        "CA_f_skip_1", client_ace,
        caller_phone="+14155550142", outcome="spam_phrase",
        duration_s=120, emergency=False,
        twilio_client=_fake_tw(cap), twilio_from="+18449403274",
    )
    assert r["sent"] is False
    assert r["reason"] == "outcome_not_normal"
    assert cap == []


def test_skip_emergency(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_FEEDBACK_SMS", "true")
    cap = []
    r = feedback.maybe_send_followup(
        "CA_f_em_1", client_ace,
        caller_phone="+14155550142", outcome="normal",
        duration_s=120, emergency=True,
        twilio_client=_fake_tw(cap), twilio_from="+18449403274",
    )
    assert r["sent"] is False
    assert r["reason"] == "emergency_call"


def test_skip_short_call(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_FEEDBACK_SMS", "true")
    cap = []
    r = feedback.maybe_send_followup(
        "CA_f_short", client_ace,
        caller_phone="+14155550142", outcome="normal",
        duration_s=15, emergency=False,
        twilio_client=_fake_tw(cap), twilio_from="+18449403274",
    )
    assert r["sent"] is False
    assert r["reason"] == "too_short"


def test_flag_off_shadow(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_FEEDBACK_SMS", "false")
    cap = []
    r = feedback.maybe_send_followup(
        "CA_f_sh", client_ace,
        caller_phone="+14155550142", outcome="normal",
        duration_s=120, emergency=False,
        twilio_client=_fake_tw(cap), twilio_from="+18449403274",
    )
    assert r["sent"] is False
    assert r["reason"] == "flag_off"
    assert cap == []


def test_sends_and_stores_pending(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_FEEDBACK_SMS", "true")
    cap = []
    r = feedback.maybe_send_followup(
        "CA_f_send_1", client_ace,
        caller_phone="+14155550142", outcome="normal",
        duration_s=60, emergency=False,
        twilio_client=_fake_tw(cap), twilio_from="+18449403274",
        conversation=[{"role": "user", "text": "schedule tune-up"},
                      {"role": "assistant", "text": "Sure, what day?"}],
    )
    assert r["sent"] is True
    assert cap[0][0] == "+14155550142"
    # Pending row present
    from src.usage import _connect, _db_lock
    with _db_lock:
        conn = _connect()
        feedback._init_feedback_schema(conn)
        row = conn.execute(
            "SELECT response, transcript FROM feedback WHERE call_sid=?",
            ("CA_f_send_1",),
        ).fetchone()
        conn.close()
    assert row["response"] is None
    transcript = json.loads(row["transcript"])
    assert transcript[0]["text"] == "schedule tune-up"


# ── record_response ───────────────────────────────────────────────────

def test_record_response_matches_yes(client_ace, monkeypatch):
    monkeypatch.setenv("ENFORCE_FEEDBACK_SMS", "true")
    feedback.maybe_send_followup(
        "CA_resp_yes", client_ace,
        caller_phone="+14155550199", outcome="normal",
        duration_s=60, emergency=False,
        twilio_client=_fake_tw([]), twilio_from="+18449403274",
    )
    r = feedback.record_response("+14155550199", "YES it worked")
    assert r["matched"] is True
    assert r["response"] == "yes"


def test_record_response_matches_no_writes_negative_log(client_ace, monkeypatch, tmp_path):
    monkeypatch.setenv("ENFORCE_FEEDBACK_SMS", "true")
    log_path = tmp_path / "negative_feedback.jsonl"
    monkeypatch.setattr(feedback, "_NEGATIVE_LOG", log_path)
    feedback.maybe_send_followup(
        "CA_resp_no", client_ace,
        caller_phone="+14155550200", outcome="normal",
        duration_s=60, emergency=False,
        twilio_client=_fake_tw([]), twilio_from="+18449403274",
        conversation=[{"role": "user", "text": "hi"},
                      {"role": "assistant", "text": "Hello!"}],
    )
    r = feedback.record_response("+14155550200", "no")
    assert r["matched"] is True
    assert r["response"] == "no"
    assert log_path.exists()
    entry = json.loads(log_path.read_text().strip())
    assert entry["caller_phone"] == "+14155550200"
    assert entry["transcript"][0]["text"] == "hi"


def test_record_response_unparseable(client_ace, monkeypatch):
    r = feedback.record_response("+14155550201", "meh")
    assert r["matched"] is False
    assert r["reason"] == "unparseable"


def test_record_response_no_pending(client_ace):
    r = feedback.record_response("+14155550202", "yes")
    assert r["matched"] is False
    assert r["reason"] == "no_pending_feedback"


def test_record_response_outside_window(client_ace, monkeypatch):
    """Feedback >48h old should NOT match."""
    monkeypatch.setenv("ENFORCE_FEEDBACK_SMS", "true")
    # Seed a pending row with a very old sent_ts
    from src.usage import _connect, _db_lock
    with _db_lock:
        conn = _connect()
        feedback._init_feedback_schema(conn)
        conn.execute("""
            INSERT INTO feedback
              (call_sid, client_id, caller_phone, sent_ts, transcript, month)
            VALUES (?,?,?,?,?,?)
        """, ("CA_old", "ace_hvac", "+14155550203", 1_700_000_000,
              "[]", "2023-11"))
        conn.close()
    # "now" at 1_800_000_000 → >100M seconds later → well outside 48h
    r = feedback.record_response("+14155550203", "yes", now_ts=1_800_000_000)
    assert r["matched"] is False
    assert r["reason"] == "no_pending_feedback"
