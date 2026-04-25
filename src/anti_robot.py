"""V4.3 — anti-robot reply scrubber.

Even with a tight prompt, Claude sometimes slips into customer-service
robot mode: "Certainly, I understand your concern about pricing. Let me
help you with that. Pricing for our services is...". A real receptionist
just says the price. This module post-processes every reply and either
strips the offending opener or rewrites it.

We never wholesale-rewrite content; we only:
  - delete pure stalling preambles
  - substitute corporate-speak interjections with crisp equivalents
  - log offenders so an operator can audit what the LLM still tries

Pure function. Returns the rewritten text + a list of rules that fired.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("anti_robot")


# ── stripping rules ─────────────────────────────────────────────────
# Patterns that should be DELETED ANYWHERE in the reply (not just at
# the start) — these are robot-speak preambles and the surrounding
# content carries the actual answer.

_STRIP_PHRASES = [
    # "So you're asking about X." (kills the robot restate)
    re.compile(
        r"\bso,?\s+(you're|you are|you'd like|you want|you wanted|you need)\s+[^.?!]*[.?!]\s*",
        flags=re.IGNORECASE,
    ),
    # "I understand you want X." / "I understand your concern."
    re.compile(
        r"\bi\s+(understand|hear|see)(\s+(your|that\s+you))[^.?!]*[.?!]\s*",
        flags=re.IGNORECASE,
    ),
    # "Thank you for calling/contacting us."
    re.compile(
        r"\bthank\s+you\s+for\s+(calling|contacting|reaching\s+out)[^.?!]*[.?!]\s*",
        flags=re.IGNORECASE,
    ),
    # "I'd be happy to help with that."
    re.compile(
        r"\b(i'd|i\s+would)\s+be\s+(happy|glad|delighted)[^.?!]*[.?!]\s*",
        flags=re.IGNORECASE,
    ),
    # "Let me help you with that."
    re.compile(
        r"\blet\s+me\s+(help|assist)\s+you[^.?!]*[.?!]\s*",
        flags=re.IGNORECASE,
    ),
    # "Of course! / Of course."  at start only (don't strip mid-sentence)
    re.compile(r"^\s*of\s+course[!.,]?\s*", flags=re.IGNORECASE),
]


# ── substitution rules ───────────────────────────────────────────────
# (regex, replacement) — fire anywhere in the text, case-insensitive,
# replacement preserves first-letter case.

def _case_preserving_sub(pattern: re.Pattern, replacement: str, text: str) -> str:
    def repl(m):
        original = m.group(0)
        if not original:
            return replacement
        if original[0].isupper():
            return replacement[:1].upper() + replacement[1:]
        return replacement
    return pattern.sub(repl, text)


_SUBSTITUTIONS = [
    # "Certainly, ..." / "Certainly! ..." → "Sure, ..."
    (re.compile(r"\bcertainly[,!.]?\s+", flags=re.IGNORECASE), "sure, "),
    # "Absolutely, ..." / "Absolutely!..." → "yeah, ..."
    (re.compile(r"\babsolutely[,!.]?\s+", flags=re.IGNORECASE), "yeah, "),
    # "I apologize for the inconvenience" → "sorry about that"
    (re.compile(r"\bi\s+apologize\s+for\s+the\s+inconvenience", flags=re.IGNORECASE),
     "sorry about that"),
    # "I apologize," at start → "Sorry,"
    (re.compile(r"\bi\s+apologize,?\s+", flags=re.IGNORECASE), "sorry, "),
    # "Please be advised that" / "Please note that" → "" (just say it)
    (re.compile(r"\bplease\s+(be\s+advised|note)\s+that\s+", flags=re.IGNORECASE), ""),
    # "How may I assist you today?" → "What can I do for you?"
    (re.compile(r"how\s+may\s+i\s+assist\s+you(\s+today)?\?", flags=re.IGNORECASE),
     "what's up?"),
    # "How can I assist you today?" → same
    (re.compile(r"how\s+can\s+i\s+assist\s+you(\s+today)?\?", flags=re.IGNORECASE),
     "what's up?"),
]


# ── entry point ──────────────────────────────────────────────────────

def scrub(reply: Optional[str]) -> tuple:
    """Return (cleaned_reply, fired_rules_list).

    Empty / None input is returned unchanged. Internal exceptions return
    the original text + an empty list — never raise.
    """
    if not reply:
        return reply or "", []

    fired = []
    out = reply

    try:
        # 1. Strip robotic phrases (anywhere in the text)
        changed = True
        while changed:
            changed = False
            for phrase in _STRIP_PHRASES:
                stripped = phrase.sub("", out, count=1)
                if stripped != out:
                    fired.append(f"strip:{phrase.pattern[:30]}")
                    out = stripped
                    changed = True
                    break

        # 2. Substitutions (case-preserving)
        for pattern, replacement in _SUBSTITUTIONS:
            new_out = _case_preserving_sub(pattern, replacement, out)
            if new_out != out:
                fired.append(f"sub:{pattern.pattern[:30]}")
                out = new_out

        # 3. Compact whitespace + capitalize first letter
        out = re.sub(r"\s+", " ", out).strip()
        if out and out[0].islower():
            out = out[0].upper() + out[1:]

        if not out:
            # If we stripped EVERYTHING, return a safe minimal reply
            return reply.strip(), fired
    except Exception as e:
        log.warning("anti_robot scrub raised: %s", e)
        return reply, []

    if fired:
        log.info("anti_robot scrubbed %d rule(s): orig_len=%d new_len=%d",
                 len(fired), len(reply), len(out))

    return out, fired


def is_enabled(client: Optional[dict]) -> bool:
    """Per-tenant toggle. Default ON for v4+."""
    if client is None:
        return True
    val = client.get("anti_robot_scrub")
    if val is None:
        return True
    return str(val).strip().lower() not in ("false", "0", "no")
