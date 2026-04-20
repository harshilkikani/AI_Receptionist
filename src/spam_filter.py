"""Spam and junk call filtering.

Two layers:
  1. Caller-ID blocklist — at /voice/incoming, before any LLM cost
  2. Phrase detection — during first N seconds of transcript

Both layers respect a critical override: if the caller has already said
anything that sounds like a real service request (address, plumbing,
emergency words), the filter is bypassed. False positives here = lost
revenue, so we lean conservative.

All rejections are logged to logs/rejected_calls.jsonl for weekly audit.

Feature flag: ENFORCE_SPAM_FILTER. When false, the filter LOGS what it
would reject but doesn't actually reject.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
_BLOCKLIST_PATH = _ROOT / "config" / "spam_blocklist.json"
_PHRASES_PATH = _ROOT / "config" / "spam_phrases.json"
_REJECT_LOG = _ROOT / "logs" / "rejected_calls.jsonl"

_blocklist_cache: Optional[dict] = None
_phrases_cache: Optional[dict] = None


def reload():
    global _blocklist_cache, _phrases_cache
    _blocklist_cache = None
    _phrases_cache = None


def _blocklist() -> dict:
    global _blocklist_cache
    if _blocklist_cache is None:
        try:
            _blocklist_cache = json.loads(_BLOCKLIST_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _blocklist_cache = {"numbers": [], "area_codes_high_risk": []}
    return _blocklist_cache


def _phrases() -> dict:
    global _phrases_cache
    if _phrases_cache is None:
        try:
            _phrases_cache = json.loads(_PHRASES_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _phrases_cache = {"spam_phrases": [], "override_keywords": []}
    return _phrases_cache


def _enforcement_active() -> bool:
    """Both global kill AND section flag must be on for actual enforcement."""
    global_on = os.environ.get("MARGIN_PROTECTION_ENABLED", "true").lower() != "false"
    filter_on = os.environ.get("ENFORCE_SPAM_FILTER", "false").lower() == "true"
    return global_on and filter_on


def _log_rejection(entry: dict):
    """Append one JSON-line to logs/rejected_calls.jsonl (creates dir if needed)."""
    _REJECT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {**entry, "ts": int(time.time()),
             "enforced": _enforcement_active()}
    with _REJECT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


# ── Public API ─────────────────────────────────────────────────────────

def check_number(from_phone: str, client_id: str = "", call_sid: str = "") -> dict:
    """Check the caller's number against the blocklist.

    Returns:
        {reject, reason, enforcement_active}

    `reject` is True only if enforcement is active AND number is blocked.
    If enforcement is inactive but number matches, we log and return
    reject=False so the call still goes through in shadow mode.
    """
    bl = _blocklist()
    target = _normalize_phone(from_phone)
    numbers = {_normalize_phone(n) for n in (bl.get("numbers") or [])}
    high_risk_ac = set(bl.get("area_codes_high_risk") or [])

    matched = False
    reason = ""
    if target in numbers:
        matched = True
        reason = "number_blocklisted"
    elif target[:3] in high_risk_ac:
        matched = True
        reason = "high_risk_area_code"

    enforce = _enforcement_active()
    if matched:
        _log_rejection({
            "layer": "number",
            "reason": reason,
            "from": from_phone,
            "client_id": client_id,
            "call_sid": call_sid,
        })

    return {
        "reject": matched and enforce,
        "reason": reason if matched else "",
        "enforcement_active": enforce,
    }


def check_silence(seconds_since_start: float, any_speech_received: bool,
                  client_id: str = "", call_sid: str = "",
                  from_phone: str = "") -> dict:
    """After ~5s with no speech → silence timeout."""
    if seconds_since_start >= 5 and not any_speech_received:
        _log_rejection({
            "layer": "silence",
            "reason": "silence_timeout",
            "from": from_phone,
            "client_id": client_id,
            "call_sid": call_sid,
            "seconds_elapsed": seconds_since_start,
        })
        return {"reject": _enforcement_active(), "reason": "silence_timeout",
                "enforcement_active": _enforcement_active()}
    return {"reject": False, "reason": "", "enforcement_active": _enforcement_active()}


def check_phrases(transcript: str, seconds_since_start: float,
                  client_id: str = "", call_sid: str = "",
                  from_phone: str = "") -> dict:
    """Scan first 15 seconds of transcript for spam phrases.

    Override: if the transcript ALSO contains any override keyword
    (address words, service words, emergency words), bypass filter.
    """
    if seconds_since_start > 15:
        return {"reject": False, "reason": "", "enforcement_active": _enforcement_active()}

    text = (transcript or "").lower()
    if not text.strip():
        return {"reject": False, "reason": "", "enforcement_active": _enforcement_active()}

    phr = _phrases()
    spam_phrases = phr.get("spam_phrases") or []
    override_keywords = phr.get("override_keywords") or []

    # Bypass: any real service/emergency/address keyword present
    for kw in override_keywords:
        if kw in text:
            return {"reject": False, "reason": "override_keyword",
                    "enforcement_active": _enforcement_active(),
                    "override_matched": kw}

    # Check for spam phrases
    for phrase in spam_phrases:
        if phrase in text:
            _log_rejection({
                "layer": "phrase",
                "reason": "spam_phrase_detected",
                "phrase": phrase,
                "from": from_phone,
                "client_id": client_id,
                "call_sid": call_sid,
                "transcript_first_200": text[:200],
            })
            enforce = _enforcement_active()
            return {
                "reject": enforce,
                "reason": "spam_phrase_detected",
                "phrase": phrase,
                "enforcement_active": enforce,
            }

    return {"reject": False, "reason": "", "enforcement_active": _enforcement_active()}
