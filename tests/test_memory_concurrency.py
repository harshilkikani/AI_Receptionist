"""V8.9b — memory.json concurrency + corruption recovery.

The original memory.py wasn't safe for concurrent writers. V8.9b's
background-thread pipeline exposed the race: a corrupt memory.json
appeared in production after two threads wrote simultaneously. These
tests cover:

  - Atomic write (no partial file on disk)
  - Lock-serialized read/modify/write
  - Auto-recover when memory.json IS corrupt (back up + rebuild from SEED)
  - Concurrent appenders don't lose any turns
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_memory(monkeypatch, tmp_path):
    """Each test gets its own memory.json."""
    import memory
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "memory_test.json")
    yield


# ── atomic write ──────────────────────────────────────────────────────

def test_atomic_write_writes_full_file():
    import memory
    memory._atomic_write('{"foo":"bar"}')
    assert memory.MEMORY_FILE.read_text() == '{"foo":"bar"}'


def test_atomic_write_replaces_temp_file():
    import memory
    memory._atomic_write('{"a":1}')
    # No leftover .tmp file
    assert not memory.MEMORY_FILE.with_suffix(
        memory.MEMORY_FILE.suffix + ".tmp").exists()
    memory._atomic_write('{"a":2}')
    # Second write completely replaces first
    assert json.loads(memory.MEMORY_FILE.read_text()) == {"a": 2}


# ── corruption recovery ──────────────────────────────────────────────

def test_load_recovers_from_corrupt_file():
    """If memory.json is valid-JSON-followed-by-garbage (the exact
    race we hit in V8.9b production), _load() backs it up and
    rebuilds from SEED instead of crashing."""
    import memory
    corrupt = '{"valid":"json"}\nEXTRA GARBAGE'
    memory.MEMORY_FILE.write_text(corrupt)
    out = memory._load()
    # Recovered to SEED — the seed names should appear
    assert "sarah" in out or "mike" in out, (
        f"recover didn't rebuild from SEED: {list(out.keys())}")
    # Backup file should exist
    backups = list(memory.MEMORY_FILE.parent.glob("memory_test.corrupt.*.json"))
    assert len(backups) == 1
    assert "EXTRA GARBAGE" in backups[0].read_text()


def test_load_recovers_from_truncated_file():
    """File cut off mid-write also recovers."""
    import memory
    memory.MEMORY_FILE.write_text('{"sarah":{"id":"sa')   # truncated mid-string
    out = memory._load()
    assert isinstance(out, dict)
    assert "sarah" in out  # rebuilt from SEED, which has sarah


def test_load_creates_seed_when_file_missing():
    import memory
    # File doesn't exist yet — _load should create it
    assert not memory.MEMORY_FILE.exists()
    out = memory._load()
    assert memory.MEMORY_FILE.exists()
    assert isinstance(out, dict)
    assert "sarah" in out


# ── concurrent writers ──────────────────────────────────────────────

def test_concurrent_append_turn_no_loss():
    """100 threads each appending one turn — final conversation
    list should have all 100 entries, no lost writes."""
    import memory
    # Seed a caller first
    memory.get_or_create_by_phone("+15555550100")
    caller_id = memory.normalize_phone("+15555550100")

    def append(i):
        memory.append_turn(caller_id, "user", f"msg {i}")

    threads = [threading.Thread(target=append, args=(i,))
               for i in range(100)]
    for t in threads: t.start()
    for t in threads: t.join()

    caller = memory.get_caller(caller_id)
    assert caller is not None
    conv = caller.get("conversation") or []
    assert len(conv) == 100, f"expected 100 turns, got {len(conv)}"
    # All messages preserved (order doesn't matter)
    texts = {t["text"] for t in conv}
    assert texts == {f"msg {i}" for i in range(100)}


def test_concurrent_get_or_create_same_phone_returns_same_record():
    """When 50 threads simultaneously call get_or_create_by_phone
    for the same number, they should all get the SAME record (not
    create 50 duplicate entries)."""
    import memory
    phone = "+15555550199"
    results = []
    lock = threading.Lock()

    def call():
        c = memory.get_or_create_by_phone(phone)
        with lock:
            results.append(c["id"])

    threads = [threading.Thread(target=call) for _ in range(50)]
    for t in threads: t.start()
    for t in threads: t.join()

    # All 50 should return the same id
    assert len(set(results)) == 1
    # The on-disk store should have exactly ONE record for that digit
    digits = memory.normalize_phone(phone)
    data = memory._load()
    matching = [k for k in data if k == digits]
    assert len(matching) == 1, (
        f"expected 1 record for {digits}, got {len(matching)}: {matching}")


def test_concurrent_mixed_ops_dont_corrupt():
    """Hammer the file with mixed reads/writes — file should remain
    parseable JSON throughout (no race-induced corruption)."""
    import memory
    memory.get_or_create_by_phone("+15555550101")
    caller_id = memory.normalize_phone("+15555550101")

    def reader(_):
        for _ in range(20):
            memory.get_caller(caller_id)

    def writer(i):
        for j in range(10):
            memory.append_turn(caller_id, "user", f"t-{i}-{j}")

    threads = ([threading.Thread(target=reader, args=(i,)) for i in range(5)]
               + [threading.Thread(target=writer, args=(i,)) for i in range(5)])
    for t in threads: t.start()
    for t in threads: t.join()

    # File must still parse
    parsed = json.loads(memory.MEMORY_FILE.read_text())
    assert isinstance(parsed, dict)
    # All 50 writes should be present (5 writers * 10 each)
    assert len(parsed[caller_id]["conversation"]) == 50
