"""V3.16 — eval response cache tests."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from evals import cache


@pytest.fixture
def tmp_cache(tmp_path):
    return tmp_path / "eval_cache.jsonl"


# ── key derivation ────────────────────────────────────────────────────

def test_key_deterministic():
    k1 = cache._key("sep1", "hi", "septic_pro")
    k2 = cache._key("sep1", "hi", "septic_pro")
    assert k1 == k2
    assert len(k1) == 16


def test_key_differs_on_case_id():
    assert cache._key("a", "msg", "c") != cache._key("b", "msg", "c")


def test_key_differs_on_message():
    assert cache._key("a", "msg1", "c") != cache._key("a", "msg2", "c")


def test_key_differs_on_client():
    assert cache._key("a", "m", "c1") != cache._key("a", "m", "c2")


# ── store + load ─────────────────────────────────────────────────────

def test_store_and_load_roundtrip(tmp_cache):
    cache.store("k1", "hi there", "General", "low", "neutral", path=tmp_cache)
    cache.store("k2", "emergency!", "Emergency", "high", "frustrated",
                path=tmp_cache)
    loaded = cache.load_cache(tmp_cache)
    assert len(loaded) == 2
    assert loaded["k1"]["reply"] == "hi there"
    assert loaded["k2"]["intent"] == "Emergency"


def test_load_cache_missing_file(tmp_path):
    assert cache.load_cache(tmp_path / "does_not_exist.jsonl") == {}


def test_load_cache_handles_bad_json(tmp_cache, monkeypatch):
    tmp_cache.parent.mkdir(parents=True, exist_ok=True)
    tmp_cache.write_text(
        '{"key":"ok","ts":9999999999,"reply":"r","intent":"General","priority":"low"}\n'
        'not valid json\n'
        '{"key":"ok2","ts":9999999999,"reply":"r2","intent":"General","priority":"low"}\n'
    )
    loaded = cache.load_cache(tmp_cache)
    assert len(loaded) == 2


def test_load_cache_age_gates_old_entries(tmp_cache):
    import time as _time
    old_ts = int(_time.time() - 60 * 86400)  # 60 days old
    tmp_cache.parent.mkdir(parents=True, exist_ok=True)
    tmp_cache.write_text(
        f'{{"key":"old","ts":{old_ts},"reply":"r","intent":"General","priority":"low"}}\n'
    )
    assert cache.load_cache(tmp_cache) == {}


def test_clear_removes_file(tmp_cache):
    cache.store("k", "x", "General", "low", path=tmp_cache)
    assert tmp_cache.exists()
    removed = cache.clear(tmp_cache)
    assert removed >= 1
    assert not tmp_cache.exists()


def test_clear_missing_file_returns_zero(tmp_path):
    assert cache.clear(tmp_path / "ghost.jsonl") == 0


# ── CachingChatFn ────────────────────────────────────────────────────

def test_caching_chat_fn_misses_then_hits(tmp_cache):
    hit_count = {"n": 0}
    def inner(caller, msg, conv=None, client=None):
        hit_count["n"] += 1
        return SimpleNamespace(reply="from llm", intent="Scheduling",
                                priority="low", sentiment="neutral")
    fn1 = cache.CachingChatFn(inner, case_id="c1",
                               cache_path=tmp_cache, use_cache=True)
    r1 = fn1(caller={}, msg="hi", conv=[], client={"id": "x"})
    assert r1.reply == "from llm"
    assert fn1.last_hit is False
    assert hit_count["n"] == 1

    # New instance re-reads cache from disk
    fn2 = cache.CachingChatFn(inner, case_id="c1",
                               cache_path=tmp_cache, use_cache=True)
    r2 = fn2(caller={}, msg="hi", conv=[], client={"id": "x"})
    assert r2.reply == "from llm"
    assert fn2.last_hit is True
    # Inner was NOT called a second time
    assert hit_count["n"] == 1


def test_caching_chat_fn_no_cache_always_calls_inner(tmp_cache):
    hit_count = {"n": 0}
    def inner(caller, msg, conv=None, client=None):
        hit_count["n"] += 1
        return SimpleNamespace(reply="x", intent="General", priority="low",
                                sentiment="neutral")
    fn = cache.CachingChatFn(inner, case_id="c1",
                              cache_path=tmp_cache, use_cache=False)
    fn(caller={}, msg="hi", conv=[], client={"id": "x"})
    fn(caller={}, msg="hi", conv=[], client={"id": "x"})
    assert hit_count["n"] == 2


def test_caching_chat_fn_different_messages_miss_separately(tmp_cache):
    hit_count = {"n": 0}
    def inner(caller, msg, conv=None, client=None):
        hit_count["n"] += 1
        return SimpleNamespace(reply=msg, intent="General", priority="low",
                                sentiment="neutral")
    fn = cache.CachingChatFn(inner, case_id="c1",
                              cache_path=tmp_cache, use_cache=True)
    fn(caller={}, msg="msg1", conv=[], client={"id": "x"})
    fn(caller={}, msg="msg2", conv=[], client={"id": "x"})
    # Both distinct messages → 2 misses
    assert hit_count["n"] == 2


# ── runner integration ───────────────────────────────────────────────

def test_runner_reports_cache_hit_flag(tmp_cache, monkeypatch):
    from evals import runner
    monkeypatch.setattr(cache, "CACHE_PATH", tmp_cache)

    cases = runner.load_cases()
    case = cases[0]

    def inner(caller, msg, conv=None, client=None):
        return SimpleNamespace(reply="canned", intent=case["expected_intent"],
                                priority=case["expected_priority"],
                                sentiment="neutral")

    r1 = runner.run_case(case, chat_fn=inner, use_cache=True)
    assert r1["cache_hit"] is False

    r2 = runner.run_case(case, chat_fn=inner, use_cache=True)
    assert r2["cache_hit"] is True


def test_runner_cli_has_no_cache_flag():
    from evals import runner
    # Just verify the CLI parses --no-cache without crashing — a dry-run
    # via capsys+runner would need network, so we just import argparse.
    parser_args = ["--no-cache", "--case", "does-not-exist"]
    # Running the CLI will load cases, skip, print 0/0, exit 0
    rc = runner._cli(parser_args)
    assert rc == 0
