"""V5.9 — final perf-sweep regression guards.

Two cheap wins from the v5 sweep:
  1. usage._init_schema is now one-shot per process. Subsequent calls
     are no-ops; the table-creating function (_create_tables) runs
     exactly once.
  2. recall.build_recall_block caches its result per
     (client_id, phone, max_days, exclude_sid) for 30 seconds, so a
     single phone call's 8+ LLM turns no longer fire 8 redundant
     SELECT queries against the calls table.

These tests guard the optimizations without depending on wall-clock
timing.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src import recall, usage


# ── one-shot schema init ───────────────────────────────────────────────

def test_schema_init_runs_only_once():
    """First _init_schema call invokes _create_tables; subsequent calls
    are no-ops thanks to the guard flag."""
    usage._reset_schema_cache()
    with patch.object(usage, "_create_tables") as creator:
        with usage._db_lock:
            conn = usage._connect()
            usage._init_schema(conn)
            usage._init_schema(conn)
            usage._init_schema(conn)
            conn.close()
    assert creator.call_count == 1


def test_reset_schema_cache_re_runs_creator():
    usage._reset_schema_cache()
    with patch.object(usage, "_create_tables") as creator:
        with usage._db_lock:
            conn = usage._connect()
            usage._init_schema(conn)
            usage._reset_schema_cache()
            usage._init_schema(conn)
            conn.close()
    assert creator.call_count == 2


def test_actual_schema_creation_still_works():
    """End-to-end sanity: tables really exist after one _init_schema."""
    usage._reset_schema_cache()
    usage.start_call("CA_v59_x", "ace", "+15555550100", "+18449403274")
    rows = usage.recent_calls(client_id="ace", limit=10)
    assert len(rows) == 1
    assert rows[0]["call_sid"] == "CA_v59_x"


# ── recall TTL cache ────────────────────────────────────────────────────

def test_recall_cache_serves_repeated_lookups_from_memory():
    """Two consecutive build_recall_block calls with the same key only
    hit prior_calls once."""
    recall.reset_recall_cache()
    with patch.object(recall, "prior_calls", return_value=[]) as m:
        a = recall.build_recall_block("ace", "+15555550100")
        b = recall.build_recall_block("ace", "+15555550100")
    assert a == b
    assert m.call_count == 1


def test_recall_cache_keyed_by_phone():
    """Different phones must NOT share a cache entry."""
    recall.reset_recall_cache()
    with patch.object(recall, "prior_calls", return_value=[]) as m:
        recall.build_recall_block("ace", "+15555550100")
        recall.build_recall_block("ace", "+15555550199")
    assert m.call_count == 2


def test_recall_cache_keyed_by_client():
    recall.reset_recall_cache()
    with patch.object(recall, "prior_calls", return_value=[]) as m:
        recall.build_recall_block("ace", "+15555550100")
        recall.build_recall_block("septic_pro", "+15555550100")
    assert m.call_count == 2


def test_recall_cache_keyed_by_max_days():
    recall.reset_recall_cache()
    with patch.object(recall, "prior_calls", return_value=[]) as m:
        recall.build_recall_block("ace", "+15555550100", max_days=7)
        recall.build_recall_block("ace", "+15555550100", max_days=30)
    assert m.call_count == 2


def test_recall_cache_keyed_by_exclude_sid():
    recall.reset_recall_cache()
    with patch.object(recall, "prior_calls", return_value=[]) as m:
        recall.build_recall_block("ace", "+15555550100", exclude_call_sid="A")
        recall.build_recall_block("ace", "+15555550100", exclude_call_sid="B")
    assert m.call_count == 2


def test_recall_cache_normalizes_phone():
    """`+15555550100`, `15555550100`, and `5555550100` should all hit
    the same cache entry — they're the same number."""
    recall.reset_recall_cache()
    with patch.object(recall, "prior_calls", return_value=[]) as m:
        recall.build_recall_block("ace", "+15555550100")
        recall.build_recall_block("ace", "5555550100")
        recall.build_recall_block("ace", "1-555-555-0100")
    assert m.call_count == 1


def test_recall_cache_expires_on_ttl():
    """Manually shove an expired entry into the cache; next read should
    re-fetch."""
    recall.reset_recall_cache()
    key = ("ace", "5555550100", 7, "")
    # Set TTL fake-expired in the past
    recall._recall_cache[key] = ("stale block", time.time() - 1)
    with patch.object(recall, "prior_calls", return_value=[]) as m:
        out = recall.build_recall_block("ace", "5555550100")
    assert out == ""             # fresh fetch returned no rows
    assert m.call_count == 1     # the stale entry was bypassed


def test_recall_cache_bypass_on_now_ts_override():
    """Tests that pass an explicit now_ts always re-fetch — needed for
    deterministic time-window assertions."""
    recall.reset_recall_cache()
    fixed = int(time.time())
    with patch.object(recall, "prior_calls", return_value=[]) as m:
        recall.build_recall_block("ace", "+15555550100", now_ts=fixed)
        recall.build_recall_block("ace", "+15555550100", now_ts=fixed)
    assert m.call_count == 2


def test_recall_empty_inputs_skip_cache():
    recall.reset_recall_cache()
    with patch.object(recall, "prior_calls") as m:
        assert recall.build_recall_block("", "+15555550100") == ""
        assert recall.build_recall_block("ace", "") == ""
    # Empty inputs short-circuit BEFORE prior_calls or cache touch
    assert m.call_count == 0


def test_recall_cache_bounded():
    """When the cache exceeds 5000 entries, we drop the oldest 100."""
    recall.reset_recall_cache()
    now = time.time()
    # Fill past the cap
    for i in range(5100):
        recall._recall_cache[(f"c{i}", "x", 7, "")] = ("", now + i)
    # Trigger an eviction by inserting one more
    recall._recall_cache_put(("trigger", "x", 7, ""), "hi", now + 99999)
    assert len(recall._recall_cache) <= 5001
