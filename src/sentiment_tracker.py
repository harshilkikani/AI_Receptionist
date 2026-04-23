"""V3.7 — per-call sentiment tracking + auto-escalation trigger.

Claude returns `sentiment` on every turn. If the CALLER is frustrated or
angry for N consecutive turns (default 2), we treat it as an implicit
emergency: route them to escalation_phone without waiting for a keyword
match. The caller gets a human faster; the owner gets a real chance to
save the relationship.

State is process-local, keyed by call_sid (same shape as src.call_timer).
Restarts lose it; acceptable at demo scale.

Env:
  ENFORCE_SENTIMENT_ESCALATION   default 'true'
  SENTIMENT_ESCALATE_AFTER       default '2' (consecutive hot turns)
"""
from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("sentiment_tracker")

HOT_SENTIMENTS = {"frustrated", "angry"}

_state_lock = threading.Lock()
_state: dict = {}  # call_sid -> {"consecutive": int, "last": str, "escalated": bool}


def _enforcement_active() -> bool:
    global_on = os.environ.get("MARGIN_PROTECTION_ENABLED", "true").lower() != "false"
    feature_on = os.environ.get("ENFORCE_SENTIMENT_ESCALATION", "true").lower() == "true"
    return global_on and feature_on


def _threshold() -> int:
    try:
        return max(1, int(os.environ.get("SENTIMENT_ESCALATE_AFTER", "2")))
    except ValueError:
        return 2


def reset_state():
    """Test hook."""
    with _state_lock:
        _state.clear()


def record(call_sid: str, sentiment: str) -> dict:
    """Record one turn's sentiment. Returns
    `{consecutive, should_escalate, escalated_now, enforcement_active}`.

    should_escalate=True when the consecutive count of hot sentiments
    meets the threshold AND we haven't already escalated this call AND
    the feature flag is on.
    """
    sentiment = (sentiment or "neutral").lower()
    enforce = _enforcement_active()
    if not call_sid:
        return {"consecutive": 0, "should_escalate": False,
                "escalated_now": False, "enforcement_active": enforce}

    with _state_lock:
        entry = _state.setdefault(
            call_sid, {"consecutive": 0, "last": "neutral", "escalated": False})
        if sentiment in HOT_SENTIMENTS:
            entry["consecutive"] += 1
        else:
            entry["consecutive"] = 0
        entry["last"] = sentiment

        threshold = _threshold()
        should = (entry["consecutive"] >= threshold and not entry["escalated"])
        if should and enforce:
            entry["escalated"] = True
            escalated_now = True
            log.warning(
                "sentiment_escalation call_sid=%s consecutive=%d sentiment=%s",
                call_sid, entry["consecutive"], sentiment,
            )
        else:
            escalated_now = False

        return {
            "consecutive": entry["consecutive"],
            "should_escalate": should and enforce,
            "escalated_now": escalated_now,
            "enforcement_active": enforce,
            "last": sentiment,
        }


def record_end(call_sid: str):
    """Clear state on call end."""
    with _state_lock:
        _state.pop(call_sid, None)


def snapshot() -> dict:
    with _state_lock:
        return {sid: dict(v) for sid, v in _state.items()}
