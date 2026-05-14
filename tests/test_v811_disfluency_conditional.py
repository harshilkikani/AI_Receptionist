"""V8.11.2 — disfluency injection conditional on endpointing fillers.

V10.0 — V7.2 disfluency injection was RETIRED across the board. The
conversation_audit showed it stacked on top of the prompt's natural-
filler permission and V8.9b's cached endpointing fillers, producing the
templated "Hmm, … / Yeah, so … / Lemme see —" patterning the brief
specifically called out. This file is kept as a regression guard so a
future revert can't silently reactivate disfluency on the pipeline.
"""
from __future__ import annotations

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


def test_disfluency_module_not_called_from_pipeline(monkeypatch):
    """V10.0 contract: regardless of tenant flags, _run_pipeline must
    not invoke disfluency.add_disfluency. The module is kept on disk
    for one-version rollback, but the pipeline doesn't import it."""
    _mock_llm(monkeypatch, reply="That works for me.")
    called = []
    from src import disfluency
    monkeypatch.setattr(
        disfluency, "add_disfluency",
        lambda text, client, **kw: called.append("hit") or text)

    main._run_pipeline(
        _caller(), "hi",
        client={"id": "x", "name": "Demo",
                "disfluency": True,
                "disfluency_intensity": 0.5,
                "plan": {}},
        call_sid="CA_v10_no_disfluency",
    )
    assert called == [], (
        "V10.0 retired V7.2 — disfluency.add_disfluency must not run "
        "from the pipeline anymore")


def test_disfluency_module_not_called_even_with_endpointing(monkeypatch):
    """Same guard, both flags set. Pre-V10 V8.11.2 turned V7.2 off when
    endpointing was on; post-V10 it's off always."""
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
        call_sid="CA_v10_belt_suspenders",
    )
    assert called == []


def test_pipeline_does_not_import_disfluency_at_module_level():
    """V10.0 cleanup: main no longer carries a `_disfluency` import.
    The module sits on disk for rollback; nothing in the live path
    references it."""
    import main as _main
    assert not hasattr(_main, "_disfluency"), (
        "V10.0 removed the disfluency import; if you're seeing this,"
        " the retirement was reverted — re-run conversation_audit"
        " before re-enabling.")


def test_live_tenant_ace_hvac_has_endpointing_on():
    """Regression guard: the live tenant still has endpointing on.
    V8.9b's cached fillers ARE still load-bearing (perceived latency
    relies on them). V10.0 retired V7.2 only — endpointing stays."""
    from src import tenant
    client = tenant.load_client_by_id("ace_hvac")
    assert client is not None
    assert client.get("endpointing_fillers") is True
