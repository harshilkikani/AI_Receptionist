"""V4.7 — cross-call recall tests."""
from __future__ import annotations

import time

import pytest

from src import call_summary, recall, transcripts, usage


def _seed_call(call_sid: str, *, client_id="ace_hvac", from_number="+14155550100",
               outcome="normal", duration=60, ts_offset_s=-3600,
               summary=None, emergency=False):
    """Helper: insert a call row at `ts_offset_s` from now."""
    usage.start_call(call_sid, client_id, from_number, "+18449403274")
    usage.end_call(call_sid, outcome=outcome, emergency=emergency)
    from src.usage import _connect, _db_lock, _init_schema
    real_ts = int(time.time()) + ts_offset_s
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        conn.execute(
            "UPDATE calls SET start_ts=?, duration_s=? WHERE call_sid=?",
            (real_ts, duration, call_sid),
        )
        conn.close()
    if summary:
        call_summary._store_summary(call_sid, summary)


# ── _normalize_phone + match ─────────────────────────────────────────

def test_normalize_phone_strips_country_code():
    assert recall._normalize_phone("+14155550100") == "4155550100"
    assert recall._normalize_phone("(415) 555-0100") == "4155550100"
    assert recall._normalize_phone("4155550100") == "4155550100"


def test_phone_matches():
    assert recall._phone_matches("+14155550100", "4155550100")
    assert recall._phone_matches("(415) 555-0100", "4155550100")
    assert not recall._phone_matches("+14155550199", "4155550100")


# ── _humanize_when ───────────────────────────────────────────────────

def test_humanize_minutes():
    now = int(time.time())
    assert "minute" in recall._humanize_when(now - 600, now_ts=now)


def test_humanize_hours():
    now = int(time.time())
    assert "hour" in recall._humanize_when(now - 7200, now_ts=now)


def test_humanize_yesterday():
    now = int(time.time())
    out = recall._humanize_when(now - 86400 * 1 - 3600, now_ts=now)
    assert "yesterday" in out.lower()


def test_humanize_days_ago():
    now = int(time.time())
    out = recall._humanize_when(now - 86400 * 4, now_ts=now)
    assert "4 days ago" in out


def test_humanize_seconds_recent():
    now = int(time.time())
    out = recall._humanize_when(now - 30, now_ts=now)
    assert "moments" in out


# ── prior_calls ──────────────────────────────────────────────────────

def test_prior_calls_no_history():
    rows = recall.prior_calls("ace_hvac", "+14155559999")
    assert rows == []


def test_prior_calls_returns_recent():
    _seed_call("CA_pr_1", from_number="+14155550100", ts_offset_s=-3600,
               summary="Pump-out scheduled.")
    rows = recall.prior_calls("ace_hvac", "+14155550100")
    assert len(rows) == 1
    assert rows[0]["call_sid"] == "CA_pr_1"
    assert rows[0]["summary"] == "Pump-out scheduled."


def test_prior_calls_excludes_in_flight_sid():
    _seed_call("CA_now", from_number="+14155550100", ts_offset_s=-30)
    _seed_call("CA_old", from_number="+14155550100", ts_offset_s=-3600)
    rows = recall.prior_calls("ace_hvac", "+14155550100",
                               exclude_call_sid="CA_now")
    sids = {r["call_sid"] for r in rows}
    assert "CA_now" not in sids
    assert "CA_old" in sids


def test_prior_calls_excludes_spam_outcomes():
    _seed_call("CA_sp", from_number="+14155550100", ts_offset_s=-3600,
               outcome="spam_phrase")
    _seed_call("CA_ok", from_number="+14155550100", ts_offset_s=-7200,
               outcome="normal")
    rows = recall.prior_calls("ace_hvac", "+14155550100")
    sids = {r["call_sid"] for r in rows}
    assert "CA_sp" not in sids
    assert "CA_ok" in sids


def test_prior_calls_respects_max_days():
    _seed_call("CA_ancient", from_number="+14155550100",
               ts_offset_s=-86400 * 30)
    _seed_call("CA_recent", from_number="+14155550100",
               ts_offset_s=-86400 * 2)
    rows = recall.prior_calls("ace_hvac", "+14155550100", max_days=7)
    sids = {r["call_sid"] for r in rows}
    assert "CA_ancient" not in sids
    assert "CA_recent" in sids


def test_prior_calls_caps_at_limit():
    for i in range(5):
        _seed_call(f"CA_cap_{i}", from_number="+14155550100",
                   ts_offset_s=-3600 * (i + 1))
    rows = recall.prior_calls("ace_hvac", "+14155550100", limit=2)
    assert len(rows) == 2


def test_prior_calls_filters_by_client():
    _seed_call("CA_other", client_id="septic_pro",
               from_number="+14155550100")
    _seed_call("CA_mine", client_id="ace_hvac",
               from_number="+14155550100")
    rows = recall.prior_calls("ace_hvac", "+14155550100")
    sids = {r["call_sid"] for r in rows}
    assert "CA_other" not in sids
    assert "CA_mine" in sids


def test_prior_calls_normalizes_phone_format():
    _seed_call("CA_phone", from_number="+14155550100", ts_offset_s=-3600)
    rows = recall.prior_calls("ace_hvac", "(415) 555-0100")
    assert len(rows) == 1


def test_prior_calls_empty_phone_returns_empty():
    assert recall.prior_calls("ace_hvac", "") == []
    assert recall.prior_calls("ace_hvac", None) == []


# ── build_recall_block ───────────────────────────────────────────────

def test_recall_block_empty_when_no_history():
    block = recall.build_recall_block("ace_hvac", "+14155559999")
    assert block == ""


def test_recall_block_includes_calls():
    _seed_call("CA_b1", from_number="+14155550100", ts_offset_s=-3600,
               summary="Pump-out scheduled for Tuesday.")
    block = recall.build_recall_block("ace_hvac", "+14155550100")
    assert "Recent calls" in block
    assert "Pump-out scheduled" in block


def test_recall_block_marks_emergency():
    _seed_call("CA_em", from_number="+14155550100", ts_offset_s=-3600,
               outcome="emergency_transfer", emergency=True)
    block = recall.build_recall_block("ace_hvac", "+14155550100")
    assert "(emergency)" in block


def test_recall_block_includes_guidance_to_lead_with_callback():
    _seed_call("CA_g", from_number="+14155550100", ts_offset_s=-3600)
    block = recall.build_recall_block("ace_hvac", "+14155550100")
    # Should hint the LLM to lead with "calling back?"
    assert "calling back" in block.lower() or "follow" in block.lower()


# ── pipeline integration ─────────────────────────────────────────────

def test_system_blocks_include_recall_when_caller_has_history():
    import llm
    _seed_call("CA_pi_1", from_number="+14155550100", ts_offset_s=-3600,
               summary="Furnace tune-up scheduled.")
    caller = {"id": "x", "phone": "+14155550100", "type": "return",
              "history": [], "conversation": []}
    blocks = llm._render_system_blocks(
        caller=caller,
        client={"id": "ace_hvac", "name": "Ace HVAC"},
        user_message="hi",
        recall_block=recall.build_recall_block("ace_hvac", "+14155550100"),
    )
    volatile = blocks[1]["text"]
    assert "Recent calls" in volatile
    assert "Furnace tune-up" in volatile


def test_system_blocks_no_recall_block_when_clean():
    import llm
    caller = {"id": "x", "phone": "+14155550199", "type": "new"}
    blocks = llm._render_system_blocks(
        caller=caller,
        client={"id": "ace_hvac", "name": "Ace HVAC"},
        user_message="hi",
        # Pass nothing — chat_with_usage builds it; here we test
        # the absence behavior
    )
    volatile = blocks[1]["text"]
    assert "Recent calls" not in volatile


def test_chat_with_usage_auto_builds_recall(monkeypatch):
    """Smoke test: chat_with_usage should consult recall.build_recall_block
    when caller has a phone, without a real LLM call."""
    import llm
    _seed_call("CA_int", from_number="+14155550100", ts_offset_s=-3600)
    captured = {}

    def fake_render(caller, client, **kwargs):
        captured["recall"] = kwargs.get("recall_block", "")
        return [{"type": "text", "text": "fake"}]
    monkeypatch.setattr(llm, "_render_system_blocks", fake_render)
    # Stub out the API call
    class FakeResponse:
        parsed_output = llm.ChatResponse(reply="hi", intent="General",
                                          priority="low")
        usage = None
    monkeypatch.setattr(llm._anthropic.beta.messages, "parse",
                        lambda **kw: FakeResponse())
    caller = {"id": "x", "phone": "+14155550100", "history": [],
              "conversation": []}
    llm.chat_with_usage(caller, "hello",
                         client={"id": "ace_hvac", "name": "Ace HVAC"})
    assert "Recent calls" in captured.get("recall", "")
