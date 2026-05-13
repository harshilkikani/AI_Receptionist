"""V7.2 — natural disfluency injection.

V4.3's `anti_robot.scrub` correctly strips corporate-speak openers
("Certainly,", "I understand your concern,", "Let me help you with
that,"). Pipeline-correct, but it leaves replies a touch too polished
— real receptionists go "hmm, lemme see" without sounding scripted.
This module re-introduces a controlled vocabulary of natural fillers
at low frequency, downstream of anti_robot + grounding so they don't
fight.

Pipeline placement (in main._run_pipeline):
    LLM → anti_robot → grounding → DISFLUENCY → (return)
                          ↑                      ↓
                          V4.3+V4.4              ↓
                                          _respond → humanize → tts

Tenant opt-in via YAML:
    disfluency: true                 # default false (opt-in only)
    disfluency_intensity: 0.15       # fraction of replies that get
                                     # a filler (clamped 0.0-0.5)

Design choices:
 - Openers only (sentence-initial). Mid-sentence fillers ("y'know")
   work great in writing but sound forced when TTS renders them.
 - Idempotent. If the reply already opens with one of our fillers,
   don't double-prepend (would happen on a retry / pipeline rerun).
 - Vocabulary chosen so anti_robot's substitution rules don't fire:
   we deliberately avoid "Certainly," / "Absolutely," / "I apologize".
 - Deterministic by random seed. Tests can pass `rng=random.Random(0)`
   for stable assertions.
"""
from __future__ import annotations

import logging
import random
from typing import Optional

log = logging.getLogger("disfluency")


# Sentence-initial fillers. Short, conversational. Chosen to NOT
# trip anti_robot.scrub's _SUBSTITUTIONS (no "Certainly,", no
# "Absolutely,", no "I apologize"). Each ends in a comma or em-dash
# so TTS reads a natural micro-pause before the substance.
_OPENERS: tuple = (
    "Hmm,",
    "Yeah, so",
    "Right —",
    "Lemme see —",
    "Okay so",
    "Alright,",
    "Sure,",
    "So,",
)


# Lower-cased prefixes that already count as "starts with a filler".
# Used to skip double-prepending. Wider than _OPENERS — covers manual
# replies the LLM might emit naturally too.
_FILLER_PREFIXES: tuple = (
    "hmm", "yeah", "right", "lemme", "okay", "alright",
    "sure", "so,", "well,", "actually,",
)


# Words we DON'T lowercase when we paste the original reply after the
# opener (preserve grammatically correct capitalization).
_PROPER_OPENERS: frozenset = frozenset(
    {"I", "I'm", "I'll", "I'd", "I've", "We", "We're",
     "We'll", "We'd", "We've", "You", "Your", "It", "It's"}
)


DEFAULT_INTENSITY = 0.15
MAX_INTENSITY = 0.5     # safety cap — beyond this and every reply feels like a stutter


def is_enabled(client: Optional[dict]) -> bool:
    """Disfluency is OPT-IN: tenant must set `disfluency: true`."""
    if not client:
        return False
    return bool(client.get("disfluency", False))


def intensity_for(client: Optional[dict]) -> float:
    """Read `disfluency_intensity` from the client config. Clamped to
    [0.0, MAX_INTENSITY]. Garbage values fall back to DEFAULT_INTENSITY."""
    if not client:
        return DEFAULT_INTENSITY
    raw = client.get("disfluency_intensity", DEFAULT_INTENSITY)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTENSITY
    if v < 0.0:
        return 0.0
    if v > MAX_INTENSITY:
        return MAX_INTENSITY
    return v


def _already_has_filler(text: str) -> bool:
    lower = text.lstrip().lower()
    return any(lower.startswith(p) for p in _FILLER_PREFIXES)


def _maybe_decase_first_word(text: str) -> str:
    """If the reply starts with a sentence-initial capital that's NOT
    a proper noun, lowercase it so the result reads as one sentence
    when we paste it after an opener.

    "It's $475." → leave alone (proper-noun-ish opener)
    "Sure," → leave alone (already a filler-like word)
    "Yes," → "yes,"
    "The first option..." → "the first option..."
    """
    if not text:
        return text
    first_word = text.split(maxsplit=1)[0] if text.split() else ""
    if not first_word:
        return text
    # Strip trailing punctuation to compare cleanly
    bare = first_word.rstrip(".,!?:;")
    if bare in _PROPER_OPENERS:
        return text
    if not bare or not bare[0].isupper():
        return text
    # Lowercase only the first letter, leave the rest alone
    return text[0].lower() + text[1:]


def add_disfluency(text: str, client: Optional[dict] = None, *,
                   rng: Optional[random.Random] = None) -> str:
    """Maybe prepend a natural filler. Decision:
       - feature flag off → return unchanged
       - empty / whitespace → return unchanged
       - already starts with a filler → return unchanged
       - random roll above intensity → return unchanged
       - else → prepend a chosen filler + space

    `rng=random.Random(seed)` for deterministic tests.
    """
    if not is_enabled(client):
        return text
    if text is None:
        return ""
    stripped = text.strip()
    if not stripped:
        return text
    if _already_has_filler(stripped):
        return text
    r = rng if rng is not None else random
    if r.random() >= intensity_for(client):
        return text
    opener = r.choice(_OPENERS)
    body = _maybe_decase_first_word(stripped)
    return f"{opener} {body}"
