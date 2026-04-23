"""V3.8 — personality snippet tests."""
from __future__ import annotations

from src import personality


def test_available_covers_expected():
    names = personality.available()
    assert "warm" in names
    assert "formal" in names
    assert "brisk" in names
    assert "regional" in names


def test_snippet_none_client_returns_empty():
    assert personality.snippet(None) == ""


def test_snippet_no_field_returns_empty():
    assert personality.snippet({"name": "X"}) == ""


def test_snippet_empty_field_returns_empty():
    assert personality.snippet({"personality": ""}) == ""


def test_snippet_whitespace_returns_empty():
    assert personality.snippet({"personality": "   "}) == ""


def test_snippet_unknown_returns_empty():
    assert personality.snippet({"personality": "sarcastic"}) == ""


def test_snippet_warm_has_friendly_cue():
    s = personality.snippet({"personality": "warm"})
    assert "neighbor" in s.lower() or "warm" in s.lower() or "friendly" in s.lower()


def test_snippet_formal_no_contractions_rule():
    s = personality.snippet({"personality": "formal"})
    assert "professional" in s.lower()


def test_snippet_brisk_short_sentences_rule():
    s = personality.snippet({"personality": "brisk"})
    assert "short" in s.lower() or "direct" in s.lower()


def test_snippet_is_case_insensitive():
    s = personality.snippet({"personality": "WARM"})
    assert s != ""
    assert s == personality.snippet({"personality": "warm"})


def test_stable_prompt_includes_personality():
    """Verify the personality snippet appears in the cacheable block."""
    import llm
    client_warm = {
        "id": "x", "name": "X", "owner_name": "bob",
        "personality": "warm",
        "services": "a", "pricing_summary": "b", "service_area": "c",
        "hours": "d", "escalation_phone": "",
        "emergency_keywords": [],
    }
    client_no_personality = dict(client_warm)
    client_no_personality["personality"] = ""
    warm_text = llm._render_stable_text(client_warm)
    plain_text = llm._render_stable_text(client_no_personality)
    assert "neighbor" in warm_text.lower() or "## Personality" in warm_text
    assert len(warm_text) > len(plain_text)
