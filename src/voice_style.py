"""V3.3 — voice style + SSML wrapping for Twilio Polly.

Turns a plain reply like

    "Hey, what's going on?"

into a Polly-friendly prosody-wrapped SSML snippet that embeds natural
pauses and a slightly slower delivery cadence:

    '<prosody rate="95%">Hey, <break time="200ms"/> what\'s going on?</prosody>'

Opt-in per client via `voice_style` in their YAML:

    voice_style: warm     # (default — slower, gentle)
    voice_style: formal   # standard cadence, clipped breaks
    voice_style: brisk    # slightly faster, shorter breaks
    voice_style: ""       # disabled — pass through plain text

Twilio Polly Neural voices render SSML inline inside `<Say>`. We XML-
escape the caller-facing text FIRST, then insert the SSML tags, so a
transcript containing `<` or `&` can't break the payload.
"""
from __future__ import annotations

import html
import re
from typing import Optional


STYLE_SETTINGS: dict = {
    "warm": {
        "rate": "95%",
        "pitch": "-2%",
        "break_sentence": "350ms",
        "break_clause": "180ms",
    },
    "formal": {
        "rate": "100%",
        "pitch": "0%",
        "break_sentence": "250ms",
        "break_clause": "150ms",
    },
    "brisk": {
        "rate": "108%",
        "pitch": "+2%",
        "break_sentence": "180ms",
        "break_clause": "100ms",
    },
}

# Characters that naturally introduce a pause.
_SENTENCE_END_RE = re.compile(r"([.!?])\s+(?=[A-Z0-9])")
_CLAUSE_RE       = re.compile(r"([,;—–])\s+")


def available_styles() -> list:
    return list(STYLE_SETTINGS.keys())


def apply_ssml(text: str, style: Optional[str] = None) -> str:
    """Return the SSML-wrapped text. If `style` is falsy or unknown,
    return the plain-escaped text (still safe to feed to Twilio)."""
    if not text:
        return ""
    safe = html.escape(text, quote=False)
    if not style or style not in STYLE_SETTINGS:
        return safe

    s = STYLE_SETTINGS[style]
    # Inject breaks at sentence + clause boundaries
    with_breaks = _SENTENCE_END_RE.sub(
        lambda m: f'{m.group(1)}<break time="{s["break_sentence"]}"/> ',
        safe,
    )
    with_breaks = _CLAUSE_RE.sub(
        lambda m: f'{m.group(1)}<break time="{s["break_clause"]}"/> ',
        with_breaks,
    )
    return (
        f'<prosody rate="{s["rate"]}" pitch="{s["pitch"]}">'
        f"{with_breaks}"
        f"</prosody>"
    )


def style_for(client: Optional[dict], mode: str = "main") -> Optional[str]:
    """Resolve the style for this client + mode combination.

    Transactional phrases (goodbyes, confirmations) skip SSML by default —
    they should land fast and crisp. Main-mode replies get the configured
    style.
    """
    if mode == "transactional":
        return None
    if client is None:
        return None
    style = (client.get("voice_style") or "").strip()
    if style and style in STYLE_SETTINGS:
        return style
    return None
