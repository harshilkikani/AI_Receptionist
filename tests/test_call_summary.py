"""V3.4 — per-call AI summary tests."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import call_summary, transcripts, usage


class _FakeLLM:
    """Mimics anthropic.Anthropic() with a canned messages.create response."""

    def __init__(self, text: str = "Scheduled pump-out for 42 Oak St.",
                 raise_exc: Exception = None):
        self._text = text
        self._raise = raise_exc
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        if self._raise:
            raise self._raise
        # Mimic Anthropic content-blocks response shape
        return SimpleNamespace(content=[SimpleNamespace(text=self._text)])


def _seed_call(sid: str, *, client_id="ace_hvac", outcome="normal",
               duration=120, turns=None):
    usage.start_call(sid, client_id, "+14155550142", "+18449403274")
    if turns:
        for role, text in turns:
            transcripts.record_turn(sid, client_id, role, text, intent="General")
    usage.end_call(sid, outcome=outcome)
    # duration set by end_call from real time; override for tests
    from src.usage import _connect, _init_schema, _db_lock
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        conn.execute("UPDATE calls SET duration_s=? WHERE call_sid=?",
                     (duration, sid))
        conn.close()


def test_generate_summary_happy_path():
    _seed_call("CA_sum_1", turns=[
        ("user", "hi, my toilet is backing up"),
        ("assistant", "Got it — what's your address?"),
        ("user", "42 Oak Street"),
        ("assistant", "Booked for tomorrow morning."),
    ])
    llm = _FakeLLM(text="Scheduled pump-out for 42 Oak St tomorrow AM.")
    result = call_summary.generate_summary("CA_sum_1", llm_client=llm)
    assert result == "Scheduled pump-out for 42 Oak St tomorrow AM."
    assert call_summary.get_summary("CA_sum_1") == result


def test_generate_summary_skips_short_call():
    _seed_call("CA_short", duration=15, turns=[
        ("user", "wrong number"),
    ])
    result = call_summary.generate_summary(
        "CA_short", llm_client=_FakeLLM())
    assert result is None


def test_generate_summary_skips_spam_outcome():
    _seed_call("CA_spam", outcome="spam_phrase", duration=120, turns=[
        ("user", "hi, we offer google business listings"),
    ])
    result = call_summary.generate_summary(
        "CA_spam", llm_client=_FakeLLM())
    assert result is None


def test_generate_summary_skips_no_transcript():
    _seed_call("CA_notrans", duration=60, turns=[])  # no transcript turns
    result = call_summary.generate_summary(
        "CA_notrans", llm_client=_FakeLLM())
    assert result is None


def test_generate_summary_handles_llm_failure():
    _seed_call("CA_fail", turns=[
        ("user", "hi"),
        ("assistant", "Hello."),
    ])
    llm = _FakeLLM(raise_exc=RuntimeError("rate limit"))
    result = call_summary.generate_summary("CA_fail", llm_client=llm)
    assert result is None
    assert call_summary.get_summary("CA_fail") is None


def test_generate_summary_strips_quotes_and_truncates():
    _seed_call("CA_trim", turns=[("user", "hi"), ("assistant", "Hello")])
    long = '"' + "x" * 200 + '"'
    llm = _FakeLLM(text=long)
    result = call_summary.generate_summary("CA_trim", llm_client=llm)
    # Quote-stripped + truncated
    assert result is not None
    assert not result.startswith('"')
    assert len(result) <= 140


def test_generate_summary_unknown_call():
    result = call_summary.generate_summary(
        "CA_does_not_exist", llm_client=_FakeLLM())
    assert result is None


def test_get_summary_empty_sid():
    assert call_summary.get_summary("") is None
    assert call_summary.get_summary(None) is None


def test_ensure_summary_column_idempotent():
    call_summary._ensure_summary_column()
    call_summary._ensure_summary_column()
    # No exception means idempotent


def test_summary_surfaces_in_call_meta():
    """get_call_meta should return the summary once stored."""
    _seed_call("CA_meta_sum", turns=[("user", "hi"), ("assistant", "Hello")])
    call_summary._store_summary("CA_meta_sum", "Quick hello call.")
    meta = transcripts.get_call_meta("CA_meta_sum")
    # meta is a dict from the 'calls' row — summary column should be present
    assert meta.get("summary") == "Quick hello call."
