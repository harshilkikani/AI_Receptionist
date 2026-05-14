"""V7.2 — disfluency injection tests.

Opt-in only. Vocabulary chosen so anti_robot.scrub doesn't fight it.
Idempotent (no double-prepend). Deterministic when an rng is supplied.
"""
from __future__ import annotations

import random

import pytest

from src import anti_robot, disfluency


# ── feature flag ───────────────────────────────────────────────────────

def test_disabled_by_default():
    assert disfluency.is_enabled({}) is False
    assert disfluency.is_enabled(None) is False


def test_explicit_true_enables():
    assert disfluency.is_enabled({"disfluency": True}) is True


def test_explicit_false_disabled():
    assert disfluency.is_enabled({"disfluency": False}) is False


# ── intensity bounds ───────────────────────────────────────────────────

def test_intensity_default():
    assert disfluency.intensity_for({}) == disfluency.DEFAULT_INTENSITY


def test_intensity_explicit():
    assert disfluency.intensity_for({"disfluency_intensity": 0.3}) == 0.3


def test_intensity_clamped_below_zero():
    assert disfluency.intensity_for({"disfluency_intensity": -0.5}) == 0.0


def test_intensity_clamped_above_max():
    assert disfluency.intensity_for(
        {"disfluency_intensity": 0.99}) == disfluency.MAX_INTENSITY


def test_intensity_garbage_falls_back_to_default():
    assert disfluency.intensity_for(
        {"disfluency_intensity": "high"}) == disfluency.DEFAULT_INTENSITY
    assert disfluency.intensity_for(
        {"disfluency_intensity": None}) == disfluency.DEFAULT_INTENSITY


# ── add_disfluency behavior ────────────────────────────────────────────

def test_no_op_when_disabled():
    out = disfluency.add_disfluency("Sure, that's twenty bucks.", {})
    assert out == "Sure, that's twenty bucks."


def test_no_op_on_empty():
    client = {"disfluency": True}
    assert disfluency.add_disfluency("", client) == ""
    assert disfluency.add_disfluency("   ", client).strip() == ""


def test_no_op_on_none():
    assert disfluency.add_disfluency(None, {"disfluency": True}) == ""


def test_injects_when_roll_succeeds():
    """rng.random() returns 0.0 → guaranteed below intensity → inject."""
    client = {"disfluency": True, "disfluency_intensity": 0.5}
    rng = random.Random()
    # Force the random.random() call to return 0.0 and choice to be deterministic
    rng.random = lambda: 0.0
    rng.choice = lambda seq: seq[0]   # always "Hmm,"
    out = disfluency.add_disfluency("That's a great question.", client, rng=rng)
    assert out.startswith("Hmm,")
    assert "great question" in out


def test_skips_when_roll_fails():
    """rng.random() above intensity → leave reply alone."""
    client = {"disfluency": True, "disfluency_intensity": 0.1}
    rng = random.Random()
    rng.random = lambda: 0.9   # well above 0.1
    out = disfluency.add_disfluency("That's a great question.", client, rng=rng)
    assert out == "That's a great question."


def test_intensity_zero_never_injects():
    client = {"disfluency": True, "disfluency_intensity": 0.0}
    out = disfluency.add_disfluency("hi.", client, rng=random.Random(0))
    assert out == "hi."


def test_does_not_double_prepend():
    """Reply already starts with a filler-like word — leave alone."""
    client = {"disfluency": True}
    rng = random.Random()
    rng.random = lambda: 0.0
    for prefix in ("Hmm, ok.", "Yeah, that works.", "Right — got it.",
                   "Lemme see — what was that?",
                   "Sure, we can do that.",
                   "Alright, here's the deal.",
                   "Well, it depends."):
        out = disfluency.add_disfluency(prefix, client, rng=rng)
        assert out == prefix, f"double-prepended on {prefix!r} -> {out!r}"


def test_decase_first_word():
    """When we prepend an opener, the original first word lowercases
    (unless it's a proper noun like 'I' or 'We')."""
    client = {"disfluency": True, "disfluency_intensity": 0.5}
    rng = random.Random()
    rng.random = lambda: 0.0
    rng.choice = lambda seq: "Hmm,"

    out = disfluency.add_disfluency("That's twenty bucks.", client, rng=rng)
    assert out == "Hmm, that's twenty bucks."


def test_proper_noun_capitalization_preserved():
    """Don't decase 'I'm', 'We', 'I', etc."""
    client = {"disfluency": True, "disfluency_intensity": 0.5}
    rng = random.Random()
    rng.random = lambda: 0.0
    rng.choice = lambda seq: "Hmm,"

    for original in ("I'm on it.", "I can help.", "We've got that.",
                     "We can do Tuesday.", "I'll check on that."):
        out = disfluency.add_disfluency(original, client, rng=rng)
        assert original.split()[0] in out, f"capitalization lost: {out!r}"


# ── composition with anti_robot — the core safety property ─────────────

@pytest.mark.parametrize("opener", list(disfluency._OPENERS))
def test_disfluency_openers_survive_anti_robot(opener):
    """Every filler in our vocabulary must pass through anti_robot.scrub
    UNCHANGED. Otherwise we'd inject a filler and anti_robot would
    immediately strip it on the next turn."""
    sample = f"{opener} that's twenty bucks."
    scrubbed, _ = anti_robot.scrub(sample)
    # The opener phrase should still be present somewhere in scrubbed text
    head = opener.split(",")[0].split("—")[0].strip()
    assert head.lower() in scrubbed.lower(), (
        f"anti_robot stripped {head!r} from {sample!r} → {scrubbed!r}")


def test_disfluency_runs_after_anti_robot_strips_certainly():
    """Order check: anti_robot turns 'Certainly, ...' into 'sure, ...'
    via its substitution rule. If disfluency runs AFTER anti_robot,
    it sees the already-cleaned text and either leaves it alone (since
    'sure, ...' is already filler-like) or adds another opener. Both
    outcomes are safe — no infinite churn."""
    raw = "Certainly, that's twenty bucks."
    scrubbed, _ = anti_robot.scrub(raw)
    assert "sure" in scrubbed.lower()
    # Now disfluency on the scrubbed text: should leave alone (already starts with filler)
    client = {"disfluency": True}
    rng = random.Random()
    rng.random = lambda: 0.0
    rng.choice = lambda seq: "Hmm,"
    out = disfluency.add_disfluency(scrubbed, client, rng=rng)
    # Either unchanged (already has filler) or has a new opener — never both
    assert "certainly" not in out.lower()


def test_distribution_roughly_matches_intensity():
    """Sanity: with intensity=0.3 across 200 calls, we should see 30-90
    injections (loose tolerance). Catches outright wiring breakage."""
    client = {"disfluency": True, "disfluency_intensity": 0.3}
    rng = random.Random(42)
    injections = 0
    for _ in range(200):
        out = disfluency.add_disfluency("That's the price.", client, rng=rng)
        if out != "That's the price.":
            injections += 1
    assert 30 <= injections <= 90, f"expected 30-90 of 200, got {injections}"


# ── pipeline integration via main._run_pipeline ─────────────────────────

def test_pipeline_does_not_call_disfluency_after_v10_retirement(monkeypatch):
    """V10.0 — V7.2 disfluency was retired. The pipeline must no longer
    invoke add_disfluency regardless of tenant flags. The module is
    kept on disk for one-version rollback."""
    import main
    import llm
    from llm import ChatResponse

    monkeypatch.setattr(
        llm, "chat_with_usage",
        lambda *a, **k: (ChatResponse(
            reply="That's twenty bucks.",
            intent="General", priority="low"), (10, 5)),
    )

    called = []
    monkeypatch.setattr(
        disfluency, "add_disfluency",
        lambda text, client, **kw: called.append("hit") or text)

    caller = {"id": "c1", "phone": "+15551234567",
              "type": "new", "history": [], "conversation": []}
    result = main._run_pipeline(
        caller, "hi",
        client={"id": "demo", "name": "Demo", "disfluency": True},
        call_sid="CA_v10_no_call",
    )
    assert called == [], (
        "V10.0 retired V7.2 — disfluency.add_disfluency must not run "
        "in the pipeline anymore")
    assert result["reply"]


def test_pipeline_skips_disfluency_when_disabled(monkeypatch):
    import importlib, main
    import llm
    from llm import ChatResponse

    monkeypatch.setattr(
        llm, "chat_with_usage",
        lambda *a, **k: (ChatResponse(
            reply="That's twenty bucks.",
            intent="General", priority="low"), (10, 5)),
    )

    called = []
    monkeypatch.setattr(
        disfluency, "add_disfluency",
        lambda *a, **k: called.append("hit") or a[0])

    caller = {"id": "c2", "phone": "+15551234567",
              "type": "new", "history": [], "conversation": []}
    main._run_pipeline(
        caller, "hi",
        client={"id": "demo", "name": "Demo"},  # no disfluency flag
        call_sid="CA_v72_2",
    )
    # When is_enabled returns False, _run_pipeline shouldn't even call add_disfluency
    assert called == [], f"expected zero calls, got {called}"
