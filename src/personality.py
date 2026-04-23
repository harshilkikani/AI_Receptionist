"""V3.8 — Agent personality snippets.

Every tenant picks a tone knob via `personality` in their YAML.
The corresponding snippet is appended to the stable (cacheable)
portion of the system prompt. Keeping the snippets here rather than
in the YAML keeps the tenant configs concise and prevents agencies
from accidentally writing conflicting instructions.
"""
from __future__ import annotations

from typing import Optional


PERSONALITIES: dict = {
    "warm": (
        "## Personality\n"
        "Speak like a friendly neighbor who's busy but glad to help. "
        "Warm, not fake. Genuine interest in the caller's problem. "
        "Use contractions. Occasional soft acknowledgments like \"mhm\" or \"okay\" "
        "are fine, but not on every turn."
    ),
    "formal": (
        "## Personality\n"
        "Speak professionally and precisely. Full sentences. No slang, "
        "no abbreviations. Keep tone courteous but business-like. "
        "Do not use filler words. Address the caller with their name when known, "
        "otherwise use \"sir\" or \"ma'am\" only if the caller first volunteers theirs."
    ),
    "brisk": (
        "## Personality\n"
        "Fast and direct. Short sentences. No pleasantries past \"hey\". "
        "Cut every unnecessary word. The caller is busy; so are you. "
        "Confirm, collect, close."
    ),
    "regional": (
        "## Personality\n"
        "Small-town cadence — think local shop, not call center. "
        "Contractions everywhere. Drop articles when natural (\"pick it up Tuesday\" "
        "rather than \"pick it up on Tuesday\"). First names when possible. "
        "Warm and familiar even on a first call."
    ),
}

DEFAULT_PERSONALITY = "warm"


def available() -> list:
    return list(PERSONALITIES.keys())


def snippet(client: Optional[dict]) -> str:
    """Return the personality-prompt snippet for this tenant. Empty
    string when the client hasn't set one (preserves the prompt's
    default voice)."""
    if client is None:
        return ""
    name = ((client.get("personality") or "").strip().lower())
    if not name:
        return ""
    return PERSONALITIES.get(name, "")
