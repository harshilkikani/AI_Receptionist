"""V3.7 — sentiment tracker + auto-escalation tests."""
from __future__ import annotations

import pytest

from src import sentiment_tracker as st


@pytest.fixture(autouse=True)
def _reset():
    st.reset_state()
    yield
    st.reset_state()


# ── ChatResponse schema ────────────────────────────────────────────────

def test_chat_response_has_sentiment_default():
    from llm import ChatResponse
    r = ChatResponse(reply="hey", intent="General", priority="low")
    assert r.sentiment == "neutral"


def test_chat_response_accepts_all_sentiments():
    from llm import ChatResponse
    for s in ("neutral", "positive", "frustrated", "angry"):
        r = ChatResponse(reply="hey", intent="General", priority="low", sentiment=s)
        assert r.sentiment == s


def test_chat_response_rejects_bad_sentiment():
    from llm import ChatResponse
    with pytest.raises(Exception):
        ChatResponse(reply="x", intent="General", priority="low", sentiment="happy")


# ── sentiment tracker ──────────────────────────────────────────────────

def test_neutral_never_escalates():
    for _ in range(10):
        r = st.record("CA_s_1", "neutral")
        assert r["should_escalate"] is False


def test_single_hot_turn_no_escalation():
    r = st.record("CA_s_2", "frustrated")
    assert r["consecutive"] == 1
    assert r["should_escalate"] is False


def test_two_consecutive_hot_turns_escalate(monkeypatch):
    monkeypatch.setenv("ENFORCE_SENTIMENT_ESCALATION", "true")
    st.record("CA_s_3", "frustrated")
    r = st.record("CA_s_3", "angry")
    assert r["consecutive"] == 2
    assert r["should_escalate"] is True
    assert r["escalated_now"] is True


def test_escalation_fires_only_once(monkeypatch):
    monkeypatch.setenv("ENFORCE_SENTIMENT_ESCALATION", "true")
    st.record("CA_s_4", "frustrated")
    r1 = st.record("CA_s_4", "angry")
    assert r1["escalated_now"] is True
    # Third hot turn doesn't re-escalate
    r2 = st.record("CA_s_4", "angry")
    assert r2["escalated_now"] is False


def test_neutral_resets_counter():
    st.record("CA_s_5", "frustrated")
    st.record("CA_s_5", "neutral")
    r = st.record("CA_s_5", "frustrated")
    assert r["consecutive"] == 1   # reset happened
    assert r["should_escalate"] is False


def test_shadow_mode_tracks_but_does_not_escalate(monkeypatch):
    monkeypatch.setenv("ENFORCE_SENTIMENT_ESCALATION", "false")
    st.record("CA_s_6", "frustrated")
    r = st.record("CA_s_6", "angry")
    assert r["consecutive"] == 2
    assert r["should_escalate"] is False


def test_kill_switch_disables(monkeypatch):
    monkeypatch.setenv("ENFORCE_SENTIMENT_ESCALATION", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "false")
    st.record("CA_s_7", "frustrated")
    r = st.record("CA_s_7", "angry")
    assert r["should_escalate"] is False


def test_empty_call_sid_noop():
    r = st.record("", "angry")
    assert r["should_escalate"] is False
    assert r["consecutive"] == 0


def test_threshold_env_override(monkeypatch):
    monkeypatch.setenv("ENFORCE_SENTIMENT_ESCALATION", "true")
    monkeypatch.setenv("SENTIMENT_ESCALATE_AFTER", "3")
    # Two hot turns is not enough now
    st.record("CA_s_t", "angry")
    r = st.record("CA_s_t", "angry")
    assert r["should_escalate"] is False
    # Third hot turn escalates
    r = st.record("CA_s_t", "angry")
    assert r["should_escalate"] is True


def test_record_end_clears_state():
    st.record("CA_s_8", "frustrated")
    assert "CA_s_8" in st.snapshot()
    st.record_end("CA_s_8")
    assert "CA_s_8" not in st.snapshot()


def test_positive_does_not_count_as_hot():
    st.record("CA_s_9", "positive")
    st.record("CA_s_9", "positive")
    r = st.record("CA_s_9", "positive")
    assert r["consecutive"] == 0
    assert r["should_escalate"] is False
