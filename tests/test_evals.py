"""P7 — eval harness: runner + regression detector + admin view."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from evals import regression_detector, runner


# ── loading ────────────────────────────────────────────────────────────

def test_cases_load_as_jsonl():
    cases = runner.load_cases()
    assert len(cases) >= 20
    for c in cases:
        assert c.get("id")
        assert c.get("client_id")
        assert c.get("expected_intent")
        assert c.get("expected_priority") in ("low", "high")
        assert isinstance(c.get("turns"), list) and c["turns"]


# ── scoring ────────────────────────────────────────────────────────────

def test_score_pass():
    case = {"id": "x", "expected_intent": "Scheduling",
            "expected_priority": "low",
            "must_contain": ["appointment"],
            "must_not_contain": ["Certainly"]}
    reply = SimpleNamespace(intent="Scheduling", priority="low",
                            reply="Sure, what day works for the appointment?")
    r = runner.score(case, reply)
    assert r["pass"] is True
    assert r["reasons"] == []


def test_score_fails_on_intent_mismatch():
    case = {"id": "x", "expected_intent": "Emergency",
            "expected_priority": "high",
            "must_contain": [], "must_not_contain": []}
    reply = SimpleNamespace(intent="Scheduling", priority="low",
                            reply="Sure, let me book that.")
    r = runner.score(case, reply)
    assert r["pass"] is False
    assert any("intent=" in x for x in r["reasons"])
    assert any("priority=" in x for x in r["reasons"])


def test_score_fails_on_forbidden_keyword():
    case = {"id": "x", "expected_intent": "General",
            "expected_priority": "low",
            "must_contain": [], "must_not_contain": ["Certainly"]}
    reply = SimpleNamespace(intent="General", priority="low",
                            reply="Certainly, happy to help.")
    r = runner.score(case, reply)
    assert r["pass"] is False
    assert any("forbidden_keyword" in x for x in r["reasons"])


def test_score_fails_on_missing_keyword():
    case = {"id": "x", "expected_intent": "Quote",
            "expected_priority": "low",
            "must_contain": ["$129"], "must_not_contain": []}
    reply = SimpleNamespace(intent="Quote", priority="low",
                            reply="Service calls start at $150.")
    r = runner.score(case, reply)
    assert r["pass"] is False
    assert any("missing_keyword" in x for x in r["reasons"])


# ── runner ─────────────────────────────────────────────────────────────

def test_run_case_uses_injected_chat_fn():
    case = {"id": "x", "client_id": "ace_hvac",
            "caller_phone": "+15550000000",
            "turns": [{"role": "user", "text": "hi"}],
            "expected_intent": "General", "expected_priority": "low",
            "must_contain": [], "must_not_contain": []}

    def fake_chat(caller, msg, conv, client=None):
        assert caller["phone"] == "+15550000000"
        assert msg == "hi"
        return SimpleNamespace(intent="General", priority="low", reply="Hi.")

    r = runner.run_case(case, chat_fn=fake_chat)
    assert r["pass"] is True
    assert r["latency_ms"] >= 0


def test_run_case_catches_exception():
    case = {"id": "x", "client_id": "ace_hvac",
            "caller_phone": "+15550000000",
            "turns": [{"role": "user", "text": "hi"}],
            "expected_intent": "General", "expected_priority": "low"}
    def bad_chat(*a, **k):
        raise RuntimeError("llm down")
    r = runner.run_case(case, chat_fn=bad_chat)
    assert r["pass"] is False
    assert any("exception" in x for x in r["reasons"])


def test_run_cases_summary_shape():
    def fake_chat(caller, msg, conv, client=None):
        # Always return General+low — most cases won't match
        return SimpleNamespace(intent="General", priority="low", reply="x")
    s = runner.run_cases(chat_fn=fake_chat)
    assert "passed" in s and "total" in s and "pass_rate" in s
    assert s["total"] == len(runner.load_cases())


# ── history ────────────────────────────────────────────────────────────

def test_history_roundtrip(tmp_path, monkeypatch):
    hist = tmp_path / "eval_history.jsonl"
    monkeypatch.setattr(runner, "HISTORY_PATH", hist)
    s = {"ts": 1000, "total": 20, "passed": 18, "failed": 2,
         "pass_rate": 0.9, "avg_latency_ms": 150, "results": [{"id": "x"}]}
    runner.append_history(s)
    loaded = runner.load_history()
    assert len(loaded) == 1
    assert loaded[0]["pass_rate"] == 0.9
    # results are stripped from history (slim row)
    assert "results" not in loaded[0]


# ── regression detector ────────────────────────────────────────────────

def test_detect_no_regression_when_improving():
    prev = {"pass_rate": 0.8}
    cur = {"pass_rate": 0.9}
    r = regression_detector.detect(cur, prev)
    assert r["regressed"] is False


def test_detect_regression_beyond_threshold():
    prev = {"pass_rate": 0.95}
    cur = {"pass_rate": 0.85}  # 10pp drop, threshold is 5pp
    r = regression_detector.detect(cur, prev)
    assert r["regressed"] is True


def test_detect_no_regression_within_threshold():
    prev = {"pass_rate": 0.95}
    cur = {"pass_rate": 0.93}  # 2pp drop, within threshold
    r = regression_detector.detect(cur, prev)
    assert r["regressed"] is False


def test_detect_no_baseline():
    r = regression_detector.detect({"pass_rate": 0.9}, None)
    assert r["regressed"] is False
    assert r["reason"] == "no_baseline"


def test_regression_run_integration(tmp_path, monkeypatch):
    """Full run with injected chat_fn, history path redirected."""
    # Point runner.HISTORY_PATH at tmp_path
    hist = tmp_path / "eval_history.jsonl"
    monkeypatch.setattr(runner, "HISTORY_PATH", hist)

    # First run — all passing thanks to a cooperative chat function
    def good_chat(caller, msg, conv, client=None):
        # Echo back expected intent/priority. Find the case by matching
        # the last user message across all cases.
        cases = runner.load_cases()
        for c in cases:
            if c["turns"][-1]["text"] == msg:
                return SimpleNamespace(
                    intent=c["expected_intent"],
                    priority=c["expected_priority"],
                    reply="Got it — $129. ",
                )
        return SimpleNamespace(intent="General", priority="low", reply="ok")

    # First run: use good_chat, save history
    s1 = runner.run_cases(chat_fn=good_chat)
    runner.append_history(s1)
    # Second run: half the cases fail due to bad chat
    def bad_chat(caller, msg, conv, client=None):
        return SimpleNamespace(intent="General", priority="low", reply="???")
    s2 = runner.run_cases(chat_fn=bad_chat)
    diff = regression_detector.detect(s2, s1)
    assert diff["regressed"] is True


# ── admin view ─────────────────────────────────────────────────────────

def test_admin_evals_view_empty(monkeypatch):
    # Point HISTORY_PATH to a non-existent file
    hist = Path("/tmp/does_not_exist_xyz.jsonl") if False else None
    monkeypatch.setattr(runner, "HISTORY_PATH", Path("/nonexistent_path_xyz.jsonl"))
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    c = TestClient(main.app)
    r = c.get("/admin/evals")
    assert r.status_code == 200
    assert "No eval runs recorded" in r.text


def test_admin_evals_view_with_history(monkeypatch, tmp_path):
    hist = tmp_path / "eval_history.jsonl"
    hist.write_text(json.dumps({
        "ts": 1744000000, "total": 20, "passed": 19,
        "failed": 1, "pass_rate": 0.95, "avg_latency_ms": 210,
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(runner, "HISTORY_PATH", hist)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    c = TestClient(main.app)
    r = c.get("/admin/evals")
    assert r.status_code == 200
    assert "95.0%" in r.text or "95%" in r.text
    assert "19" in r.text
