"""P8 — prompt caching structure + env toggle + backward-compat."""
from __future__ import annotations

import pytest

import llm


def test_system_blocks_have_cache_on_stable(client_ace, monkeypatch):
    monkeypatch.setenv("PROMPT_CACHE_ENABLED", "true")
    blocks = llm._render_system_blocks(
        caller={"id": "x", "phone": "+14155550142", "type": "new"},
        client=client_ace,
    )
    assert len(blocks) == 2
    assert blocks[0]["type"] == "text"
    assert "cache_control" in blocks[0]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "Ace HVAC" in blocks[0]["text"]
    # Memory block must NOT carry cache_control
    assert "cache_control" not in blocks[1]
    assert "Caller memory" in blocks[1]["text"]


def test_cache_disabled_returns_single_block(client_ace, monkeypatch):
    monkeypatch.setenv("PROMPT_CACHE_ENABLED", "false")
    blocks = llm._render_system_blocks(
        caller={"id": "x", "phone": "+14155550142"},
        client=client_ace,
    )
    assert len(blocks) == 1
    assert "cache_control" not in blocks[0]


def test_stable_text_does_not_include_memory(client_ace):
    stable = llm._render_stable_text(client_ace)
    assert "Caller memory" not in stable  # now in volatile block only
    assert "Ace HVAC" in stable


def test_render_system_prompt_still_returns_string(client_ace):
    """Backward-compat for existing tests + the few places that inspect
    the full prompt."""
    text = llm._render_system_prompt(
        caller={"id": "x", "phone": "+14155550142"},
        client=client_ace,
    )
    assert isinstance(text, str)
    # Full prompt contains BOTH the tenant details and memory section
    assert "Ace HVAC" in text
    assert "Caller memory" in text
    assert "{{memory}}" not in text     # no leftover placeholder
    assert "{{company_name}}" not in text


def test_wrap_up_suffix_sits_in_volatile_block(client_ace, monkeypatch):
    monkeypatch.setenv("PROMPT_CACHE_ENABLED", "true")
    blocks = llm._render_system_blocks(
        caller={"id": "x"}, client=client_ace,
        wrap_up_mode="hard",
    )
    assert len(blocks) == 2
    # Volatile block contains the wrap-up instruction
    assert "Call duration hard limit" in blocks[1]["text"]
    # Cacheable block does NOT contain the wrap-up (would bust caching)
    assert "Call duration hard limit" not in blocks[0]["text"]


def test_recover_suffix_in_volatile(client_ace, monkeypatch):
    monkeypatch.setenv("PROMPT_CACHE_ENABLED", "true")
    blocks = llm._render_system_blocks(
        caller={"id": "x"}, client=client_ace,
        recover_suffix="[SYSTEM: recover hint]",
    )
    assert "recover hint" in blocks[1]["text"]
    assert "recover hint" not in blocks[0]["text"]


def test_last_token_usage_returns_cache_read():
    class FakeUsage:
        input_tokens = 100
        output_tokens = 30
        cache_read_input_tokens = 80
    class FakeResponse:
        usage = FakeUsage()
    assert llm.last_token_usage(FakeResponse()) == (100, 30, 80)


def test_last_token_usage_handles_missing_cache_field():
    """Older SDK responses may not include cache_read_input_tokens."""
    class FakeUsage:
        input_tokens = 100
        output_tokens = 30
    class FakeResponse:
        usage = FakeUsage()
    assert llm.last_token_usage(FakeResponse()) == (100, 30, 0)


def test_cache_stats_reset_and_track(monkeypatch):
    llm.reset_cache_stats()
    s = llm.cache_stats()
    assert s == {"reads": 0, "writes": 0, "total_input": 0, "total_cache_read": 0}


def test_stable_block_is_identical_across_different_callers(client_ace):
    """Cache key stability — two different callers produce the same
    cacheable block for the same tenant."""
    a = llm._render_system_blocks(
        caller={"id": "caller_a", "phone": "+14155550101", "name": "Alice"},
        client=client_ace,
    )
    b = llm._render_system_blocks(
        caller={"id": "caller_b", "phone": "+14155550202", "name": "Bob"},
        client=client_ace,
    )
    assert a[0]["text"] == b[0]["text"]   # stable prefix matches
    assert a[1]["text"] != b[1]["text"]   # memory differs


def test_stable_block_differs_across_tenants(client_ace, client_default):
    """Different tenants produce different cacheable prefixes (correct —
    each tenant should have its own cache entry)."""
    a = llm._render_system_blocks(caller={"id": "x"}, client=client_ace)
    b = llm._render_system_blocks(caller={"id": "x"}, client=client_default)
    assert a[0]["text"] != b[0]["text"]
