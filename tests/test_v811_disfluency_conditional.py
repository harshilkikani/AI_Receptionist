"""V8.11.2 — disfluency injection conditional on endpointing fillers.

When V8.9b endpointing is on, V7.2 disfluency would cause two
consecutive fillers per turn ("Mhm —" endpointing + "Hmm, ..." V7.2).
V8.11.2 disables V7.2 for those tenants. Non-endpointing tenants
still get V7.2 variation.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import llm
import main
from llm import ChatResponse


def _caller():
    return {"id": "c1", "phone": "+15555550199", "type": "new",
            "history": [], "conversation": []}


def _mock_llm(monkeypatch, reply="Yeah, that works."):
    monkeypatch.setattr(
        llm, "chat_with_usage",
        lambda *a, **k: (ChatResponse(
            reply=reply, intent="General", priority="low",
        ), (10, 5)))


def test_disfluency_runs_when_endpointing_off(monkeypatch):
    """Baseline: tenant with `disfluency: true` and no endpointing →
    V7.2 still injects."""
    _mock_llm(monkeypatch, reply="That works for me.")
    called = []
    from src import disfluency
    real_add = disfluency.add_disfluency
    monkeypatch.setattr(
        disfluency, "add_disfluency",
        lambda text, client, **kw: called.append("hit") or real_add(text, client, **kw))

    out = main._run_pipeline(
        _caller(), "hi",
        client={"id": "x", "name": "Demo",
                "disfluency": True,
                "disfluency_intensity": 0.5,
                # NO endpointing_fillers
                "plan": {}},
        call_sid="CA_v811_1",
    )
    assert called == ["hit"], "V7.2 should run when endpointing is off"
    assert out["reply"]  # didn't break the pipeline


def test_disfluency_skipped_when_endpointing_on(monkeypatch):
    """V8.11.2 — tenant with BOTH `disfluency: true` AND
    `endpointing_fillers: true` → V7.2 must NOT run. Endpointing
    filler from V8.9b handles the conversational variance."""
    _mock_llm(monkeypatch, reply="Yeah, that works for me.")
    called = []
    from src import disfluency
    monkeypatch.setattr(
        disfluency, "add_disfluency",
        lambda text, client, **kw: called.append("hit") or text)

    out = main._run_pipeline(
        _caller(), "hi",
        client={"id": "x", "name": "Demo",
                "disfluency": True,
                "disfluency_intensity": 0.5,
                "endpointing_fillers": True,   # V8.9b on
                "plan": {}},
        call_sid="CA_v811_2",
    )
    assert called == [], (
        "V7.2 must NOT run when V8.9b endpointing is on (caller would "
        "hear two consecutive fillers otherwise)")
    assert out["reply"]


def test_disfluency_skipped_when_endpointing_on_even_if_intensity_high(monkeypatch):
    """Belt-and-suspenders: even with intensity at the max, V8.9b
    overrides V7.2."""
    _mock_llm(monkeypatch)
    called = []
    from src import disfluency
    monkeypatch.setattr(
        disfluency, "add_disfluency",
        lambda text, client, **kw: called.append("hit") or text)

    main._run_pipeline(
        _caller(), "hi",
        client={"id": "x", "name": "Demo",
                "disfluency": True,
                "disfluency_intensity": 0.99,
                "endpointing_fillers": True,
                "plan": {}},
        call_sid="CA_v811_3",
    )
    assert called == []


def test_disfluency_skipped_when_disabled_regardless_of_endpointing(monkeypatch):
    """Sanity: tenant with disfluency=false never invokes V7.2,
    independent of endpointing setting. (Pre-existing V7.2 behavior.)"""
    _mock_llm(monkeypatch)
    called = []
    from src import disfluency
    monkeypatch.setattr(
        disfluency, "add_disfluency",
        lambda text, client, **kw: called.append("hit") or text)

    main._run_pipeline(
        _caller(), "hi",
        client={"id": "x", "name": "Demo",
                "disfluency": False,
                "endpointing_fillers": True,
                "plan": {}},
        call_sid="CA_v811_4",
    )
    assert called == []


def test_disfluency_skipped_when_neither_flag_set(monkeypatch):
    _mock_llm(monkeypatch)
    called = []
    from src import disfluency
    monkeypatch.setattr(
        disfluency, "add_disfluency",
        lambda text, client, **kw: called.append("hit") or text)

    main._run_pipeline(
        _caller(), "hi",
        client={"id": "x", "name": "Demo", "plan": {}},
        call_sid="CA_v811_5",
    )
    assert called == []


def test_live_tenant_ace_hvac_has_endpointing_on():
    """Regression guard: the live tenant config has both endpointing
    and disfluency enabled (legacy). V8.11.2 means the endpointing
    flag wins and V7.2 is silently skipped for ace_hvac. Verify the
    yaml shape so a future config edit doesn't accidentally re-enable
    the double-filler artifact."""
    from src import tenant
    client = tenant.load_client_by_id("ace_hvac")
    assert client is not None
    assert client.get("endpointing_fillers") is True
    # disfluency may be True or False; doesn't matter — endpointing
    # takes precedence. This test exists so anyone removing the
    # endpointing flag has to consciously think about the consequence.
