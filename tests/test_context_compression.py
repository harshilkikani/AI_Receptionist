"""V3.2 — context compression for long conversations."""
from __future__ import annotations

import llm


def _turn(role: str, text: str) -> dict:
    return {"role": role, "text": text}


def test_short_conversation_no_compression():
    """<= COMPRESS_THRESHOLD turns → verbatim last-RECENT_TURNS path."""
    conv = [
        _turn("user", "hey"),
        _turn("assistant", "Hi — what's going on?"),
        _turn("user", "my tank is full"),
        _turn("assistant", "Got it. Where are you?"),
    ]
    msgs = llm._build_messages(conv, "123 Main St")
    # 4 conv turns + new user msg = 5; no recap
    assert len(msgs) == 5
    assert msgs[0]["role"] == "user"
    assert "context recap" not in (msgs[0]["content"] or "").lower()


def test_long_conversation_injects_recap():
    """> COMPRESS_THRESHOLD turns → inject recap prefix + last RECENT_TURNS."""
    conv = []
    for i in range(12):
        conv.append(_turn("user" if i % 2 == 0 else "assistant", f"turn {i}"))
    msgs = llm._build_messages(conv, "latest message")
    # Structure: recap pair (user + assistant Got it), RECENT_TURNS, new msg
    # So total = 2 + RECENT_TURNS + 1
    assert len(msgs) == 2 + llm.RECENT_TURNS + 1
    recap = msgs[0]["content"]
    assert "[context recap]" in recap
    # Older turns (0..7) compressed; newer turns (8..11) verbatim
    assert "turn 0" in recap
    assert "turn 7" in recap
    assert msgs[-1]["content"] == "latest message"


def test_compress_older_turns_truncates_long_text():
    """Individual old turns capped at ~80 chars."""
    long_text = "x" * 500
    older = [{"role": "user", "text": long_text}]
    recap = llm._compress_older_turns(older)
    # The per-turn line should have a truncation marker
    assert "..." in recap
    # Line should be well under 500 chars
    assert all(len(line) < 120 for line in recap.splitlines())


def test_compress_older_turns_empty():
    assert llm._compress_older_turns([]) == ""
    assert llm._compress_older_turns([{"role": "user", "text": ""}]) == ""


def test_compress_marks_roles():
    older = [
        {"role": "user", "text": "hi there"},
        {"role": "assistant", "text": "Hey"},
    ]
    recap = llm._compress_older_turns(older)
    assert "caller:" in recap
    assert "receptionist:" in recap


def test_long_conversation_recap_does_not_inflate_token_count_much():
    """Rough upper bound: recap for 8 old turns stays under 1.5KB.
    Replacing 8 turns × ~500 chars = 4KB with a ~1KB recap is the win."""
    conv = [_turn("user" if i % 2 == 0 else "assistant",
                  "a fairly long message about what's going on with my septic system " * 5)
            for i in range(12)]
    msgs = llm._build_messages(conv, "new msg")
    recap_content = msgs[0]["content"]
    assert len(recap_content) < 1500


def test_recap_preserves_conversation_flow_for_claude():
    """After recap injection, the alternating user/assistant pattern must
    still start with a user turn (Anthropic requires this)."""
    conv = [_turn("user" if i % 2 == 0 else "assistant", f"t{i}") for i in range(12)]
    msgs = llm._build_messages(conv, "latest")
    assert msgs[0]["role"] == "user"
    # After stripping-to-first-user, alternation should make sense
    # (not strictly required but good hygiene)
    assert any(m["role"] == "assistant" for m in msgs)
