"""Call duration tracking + wrap-up + hard cap.

The voice webhook calls `check(call_sid, client, caller_speech)` at the
start of every turn. The returned dict drives how the turn is handled:

  action='normal'      → proceed as usual
  action='soft_wrapup' → pass wrap_up_mode='soft' to llm.chat (3:00 cue)
  action='hard_wrapup' → pass wrap_up_mode='hard' to llm.chat (3:45 cue)
  action='force_end'   → skip LLM, play goodbye, hangup (240s/360s hit)

Emergencies: if priority was high on any prior turn, cap extends to 360s.

Grace period: if the last caller turn looks like critical info collection
(address, phone, name), allow 15s extension beyond hard cap.

State is in-memory (process-local). Restarts lose state — acceptable for
a single-box demo. For multi-instance, move to Redis/DB.
"""

from __future__ import annotations

import os
import re
import time
import threading

_state_lock = threading.Lock()
_calls: dict = {}  # call_sid -> {start_ts, client_id, emergency, grace_used}

# V5.1 — defensive bound. If something fails to call record_end (a bug,
# a Twilio status callback never arriving, etc.), we cap the dict size
# and evict oldest-by-start_ts so memory stays bounded indefinitely.
MAX_CONCURRENT_CALLS = 5000


def _evict_oldest_if_full():
    """Caller must already hold _state_lock."""
    if len(_calls) <= MAX_CONCURRENT_CALLS:
        return
    # Drop the entry with the smallest start_ts
    oldest_sid = min(_calls.items(), key=lambda kv: kv[1].get("start_ts", 0))[0]
    _calls.pop(oldest_sid, None)


def _now() -> float:
    return time.time()


def _flag_enabled() -> bool:
    """Global kill + section-specific feature flag. Both must allow for
    enforcement to actually end calls. If either is off, we log what we
    WOULD do but don't force-end."""
    global_on = os.environ.get("MARGIN_PROTECTION_ENABLED", "true").lower() != "false"
    cap_on = os.environ.get("ENFORCE_CALL_DURATION_CAP", "false").lower() == "true"
    return global_on and cap_on


def _critical_info_pending(speech: str) -> bool:
    """Heuristic: is the caller in the middle of giving address/phone/name?
    If so, give them a 15s grace window past the cap."""
    if not speech:
        return False
    low = speech.lower()
    # Numbers (address, phone, unit)
    if re.search(r"\d{3,}", low):
        return True
    patterns = (
        "my address", "my number", "phone number", "my name is",
        "it's on", "we're at", "i'm at", "i live", "zip", "apartment",
    )
    return any(p in low for p in patterns)


def record_start(call_sid: str, client_id: str):
    """Called from /voice/incoming. Idempotent — safe to call twice."""
    if not call_sid:
        return
    with _state_lock:
        if call_sid not in _calls:
            _calls[call_sid] = {
                "start_ts": _now(),
                "client_id": client_id,
                "emergency": False,
                "grace_used": False,
            }
            _evict_oldest_if_full()


def record_end(call_sid: str):
    """Called when call ends. Clears in-memory state — both call_timer's
    AND sentiment_tracker's, since they're co-keyed by call_sid and both
    need cleanup on EVERY terminal path. Centralizing here means
    callers only need one record_end() instead of two."""
    with _state_lock:
        _calls.pop(call_sid, None)
    # V5.1 — also evict sentiment state. Lazy-import avoids a cycle.
    try:
        from src import sentiment_tracker
        sentiment_tracker.record_end(call_sid)
    except Exception:
        pass


def mark_emergency(call_sid: str):
    """Flag a call as emergency so duration cap extends to 360s."""
    with _state_lock:
        if call_sid in _calls:
            _calls[call_sid]["emergency"] = True


def check(call_sid: str, client: dict, caller_speech: str = "") -> dict:
    """Called at the start of each /voice/gather turn. Returns:

      {action, elapsed, cap, wrap_up_mode, enforcement_active}

    action: 'normal' | 'soft_wrapup' | 'hard_wrapup' | 'force_end'
    wrap_up_mode: pass through to llm.chat_with_usage(wrap_up_mode=...)
    """
    if not call_sid:
        return {
            "action": "normal", "elapsed": 0, "cap": 240,
            "wrap_up_mode": None, "enforcement_active": False,
        }

    plan = (client or {}).get("plan") or {}
    cap = int(plan.get("max_call_duration_seconds", 240))
    emergency_cap = int(plan.get("max_call_duration_emergency", 360))

    with _state_lock:
        entry = _calls.get(call_sid)
        if entry is None:
            # Call was never recorded via /voice/incoming (reload? test?)
            record_start(call_sid, (client or {}).get("id", "_default"))
            entry = _calls[call_sid]

        elapsed = _now() - entry["start_ts"]
        is_emergency = entry["emergency"]
        effective_cap = emergency_cap if is_emergency else cap

        # Thresholds (in seconds)
        soft_threshold = max(effective_cap - 60, 0)   # 180 for 240, 300 for 360
        hard_threshold = max(effective_cap - 15, 0)   # 225 for 240, 345 for 360
        grace_cap = effective_cap + 15

        enforcement = _flag_enabled()

        # Past hard cap, no grace → end the call
        if elapsed >= effective_cap:
            # Allow one grace extension if actively collecting critical info
            if not entry["grace_used"] and _critical_info_pending(caller_speech):
                entry["grace_used"] = True
                return {
                    "action": "soft_wrapup",
                    "elapsed": elapsed,
                    "cap": effective_cap,
                    "wrap_up_mode": "hard",  # urgent close
                    "enforcement_active": enforcement,
                    "note": "grace_period_extended",
                }
            # Past grace or no pending info
            if elapsed >= grace_cap or not entry["grace_used"]:
                return {
                    "action": "force_end" if enforcement else "hard_wrapup",
                    "elapsed": elapsed,
                    "cap": effective_cap,
                    "wrap_up_mode": "hard",
                    "enforcement_active": enforcement,
                }

        # Approaching hard cap (225s/345s)
        if elapsed >= hard_threshold:
            return {
                "action": "hard_wrapup",
                "elapsed": elapsed,
                "cap": effective_cap,
                "wrap_up_mode": "hard",
                "enforcement_active": enforcement,
            }

        # Approaching soft threshold (180s/300s)
        if elapsed >= soft_threshold:
            return {
                "action": "soft_wrapup",
                "elapsed": elapsed,
                "cap": effective_cap,
                "wrap_up_mode": "soft",
                "enforcement_active": enforcement,
            }

        return {
            "action": "normal",
            "elapsed": elapsed,
            "cap": effective_cap,
            "wrap_up_mode": None,
            "enforcement_active": enforcement,
        }


def snapshot() -> dict:
    """For tests/admin — return current in-memory state."""
    with _state_lock:
        return {sid: dict(v) for sid, v in _calls.items()}
