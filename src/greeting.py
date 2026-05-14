"""V7.3 — time-of-day + recall-aware greeting.

`main._greeting_for` previously emitted one static string per language:
    "Hey, this is Joanna from {company}— what's going on?"

That lands flat after the third call. v7.3 layers context the AI already
has but never used in the opener:
  - Time of day (morning / afternoon / evening / late-night)
  - Caller's name (when returning + memory has it)
  - Cross-call recall ("calling back about yesterday?") when V4.7 finds
    a prior call within the recall window

All in the tenant's local timezone (`clients/<id>.yaml::timezone`,
defaulting to America/New_York when absent or malformed).

Languages: en / es / hi / gu — same shape, localized. The Hindi and
Gujarati strings keep transliterated Hindi/Gujarati to match the
existing tone established in v1.

This function is pure: same inputs → same outputs. Pass `now=` for
deterministic tests.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:                                      # pragma: no cover
    ZoneInfo = None                                       # type: ignore

log = logging.getLogger("greeting")

DEFAULT_TZ = "America/New_York"


def _resolve_tz(tz_name: Optional[str]):
    """Return a tzinfo. Falls back to America/New_York on bad input.
    On Python builds without zoneinfo, falls back to UTC."""
    if ZoneInfo is None:
        return timezone.utc
    candidates = [tz_name, DEFAULT_TZ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except Exception:
            continue
    return timezone.utc


def _time_bucket(dt: datetime) -> str:
    """Map an hour-of-day to a greeting bucket.
      5-10:59  → morning
      11-16:59 → afternoon
      17-20:59 → evening
      21-04:59 → late_night
    """
    h = dt.hour
    if 5 <= h < 11:
        return "morning"
    if 11 <= h < 17:
        return "afternoon"
    if 17 <= h < 21:
        return "evening"
    return "late_night"


# ── New-caller greetings — same shape, per-language ────────────────────
#
# Each bucket has 1-2 variations so repeat callers from the same tenant
# don't get the exact same opener every time. Choice is deterministic
# (rotated by date) to avoid "stutter" mid-shift.

# V8.12.4 — tightened to "local business" cadence. Previous templates
# read like a hospitality script ("this is Joanna from {company} — what
# can I do for you?"); a real busy receptionist answers shorter and
# more direct ("{company}, this is Joanna."). Shorter greetings also
# render less performatively in TTS — fewer chances for a stretched
# vowel to leak into the cadence.
_TEMPLATES_EN = {
    "morning": [
        "{company}, this is Joanna. What's going on?",
        "Morning — Joanna at {company}. How can I help?",
    ],
    "afternoon": [
        "{company}, Joanna speaking.",
        "Hi, {company}. This is Joanna.",
    ],
    "evening": [
        "{company}, this is Joanna. What's up?",
        "Hi — Joanna at {company}.",
    ],
    "late_night": [
        "{company} after hours, Joanna here.",
        "Hey — {company} on-call. What's going on?",
    ],
}

_TEMPLATES_ES = {
    "morning": [
        "Buenos dias, habla Joanna de {company}— en que te puedo ayudar?",
    ],
    "afternoon": [
        "Hola, habla Joanna de {company}— en que te puedo ayudar?",
    ],
    "evening": [
        "Buenas tardes, habla Joanna de {company}— en que te puedo ayudar?",
    ],
    "late_night": [
        "Hola, habla Joanna de {company}, atencion despues de horas— en que te puedo ayudar?",
    ],
}

_TEMPLATES_HI = {
    "morning": ["Namaste, main Joanna, {company} se— subah subah kya hua?"],
    "afternoon": ["Hey, main Joanna, {company} se— kya hua batao?"],
    "evening": ["Hi, main Joanna, {company} se— shaam ko kya baat hai?"],
    "late_night": ["Namaste, main Joanna, {company} se after-hours line— emergency hai kya?"],
}

_TEMPLATES_GU = {
    "morning": ["Hello, hu Joanna, {company} thi— savar ma shu thayum?"],
    "afternoon": ["Hey, hu Joanna, {company} thi— shu thayum kahejo?"],
    "evening": ["Hi, hu Joanna, {company} thi— saanj ne kahejo shu thayum?"],
    "late_night": ["Hello, hu Joanna, {company} thi after-hours line— emergency che ke?"],
}

_TEMPLATES_BY_LANG = {
    "en": _TEMPLATES_EN,
    "es": _TEMPLATES_ES,
    "hi": _TEMPLATES_HI,
    "gu": _TEMPLATES_GU,
}


# Named-return-caller templates (English only — the named variant is
# only fired for callers with a name on file, which today is English-only
# memory shape). Localize when memory.name is multilingual.
_NAMED_RETURN_EN = (
    "Hey {first}! Joanna from {company}, what's up?",
    "Hi {first}— Joanna from {company}, what's going on today?",
)

# Recall-aware template (used when the caller has prior calls in the
# last N days under the same tenant — see V4.7 recall.prior_calls).
_RECALL_EN = (
    "Hey, calling back about yesterday? Joanna from {company}.",
    "Hi! Joanna from {company}— I see you called earlier, what's up?",
)


def _pick(templates: list, dt: datetime) -> str:
    """Deterministic rotation: same date → same template. Avoids
    stutter mid-shift but gives variety day-to-day."""
    if not templates:
        return ""
    idx = (dt.toordinal()) % len(templates)
    return templates[idx]


def _first_name(caller: Optional[dict]) -> str:
    if not caller:
        return ""
    name = (caller.get("name") or "").strip()
    if not name or name.lower().startswith("unknown"):
        return ""
    return name.split()[0]


def _is_returning(caller: Optional[dict]) -> bool:
    if not caller:
        return False
    if caller.get("type") == "return":
        return True
    # Memory may not have set 'type' but conversation history exists
    history = caller.get("history") or []
    conv = caller.get("conversation") or []
    return bool(history) or len(conv) >= 2


def greeting_for(client: dict, lang: str,
                 *,
                 caller: Optional[dict] = None,
                 recall_block: Optional[str] = None,
                 now: Optional[datetime] = None) -> str:
    """Render a contextual greeting.

    Priority order (highest first):
      1. Recall-aware ("calling back about yesterday?") — when V4.7
         supplies a non-empty recall_block AND the lang is English
         (Hindi/Gujarati/Spanish fall through to plain bucket — those
         locales' callers can still benefit from time-of-day variation).
      2. Named returning caller — when memory has a first name AND
         the caller is flagged as returning AND lang is English.
      3. Plain time-of-day bucket per language.

    `now=` overrides current time for deterministic tests.
    """
    client = client or {}
    company = client.get("name") or "the office"
    tz = _resolve_tz(client.get("timezone"))
    if now is None:
        now = datetime.now(tz)
    else:
        # If now is naive, attach the tenant tz so bucket math is correct
        if now.tzinfo is None:
            now = now.replace(tzinfo=tz)
        else:
            now = now.astimezone(tz)
    bucket = _time_bucket(now)

    # 1. Recall-aware (English only)
    if lang == "en" and recall_block and recall_block.strip():
        template = _pick(list(_RECALL_EN), now)
        return template.format(company=company, first=_first_name(caller))

    # 2. Named returning caller (English only)
    if lang == "en":
        first = _first_name(caller)
        if first and _is_returning(caller):
            template = _pick(list(_NAMED_RETURN_EN), now)
            return template.format(company=company, first=first)

    # 3. Plain time-of-day bucket
    templates = _TEMPLATES_BY_LANG.get(lang, _TEMPLATES_EN).get(
        bucket, _TEMPLATES_EN[bucket])
    template = _pick(list(templates), now)
    return template.format(company=company)
