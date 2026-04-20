"""SMS rate limiting + length caps.

Policy (from spec §D):
  - Hard max 3 SMS per conversation (configurable per-client via
    `plan.sms_max_per_call`)
  - Prefer 160 chars (1 segment); absolute max 320 (2 segments)
  - AI forbidden from back-and-forth texting — purpose restricted to
    booking confirmation / handoff / escalation
  - Track count in usage DB (via src.usage.sms_count_for_call)

Feature flag: ENFORCE_SMS_CAP. When false, caps are LOGGED but enforced.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from src import usage

log = logging.getLogger("sms_limiter")


def _enforcement_active() -> bool:
    global_on = os.environ.get("MARGIN_PROTECTION_ENABLED", "true").lower() != "false"
    cap_on = os.environ.get("ENFORCE_SMS_CAP", "false").lower() == "true"
    return global_on and cap_on


def cap_length(body: str, absolute_max: int = 320) -> str:
    """Truncate body to the 2-segment hard max. Preserves word boundaries
    when possible."""
    if len(body) <= absolute_max:
        return body
    # Truncate at last space before limit, fall back to hard cut
    cut = body.rfind(" ", 0, absolute_max - 1)
    if cut > absolute_max - 40:  # only use word boundary if close
        return body[:cut].rstrip() + "…"
    return body[: absolute_max - 1].rstrip() + "…"


def should_send(call_sid: str, client: dict,
                body: str) -> dict:
    """Decide whether an outbound SMS should be sent.

    Returns:
        {allow, reason, body (possibly truncated), count_before, cap,
         enforcement_active}

    If allow=False, caller should skip the send.
    If allow=True, caller should send `body` (which may be truncated).
    """
    plan = (client or {}).get("plan") or {}
    cap = int(plan.get("sms_max_per_call", 3))
    enforce = _enforcement_active()
    count = usage.sms_count_for_call(call_sid) if call_sid else 0
    truncated = cap_length(body or "")

    if count >= cap:
        log.info("sms_cap_reached call_sid=%s count=%d cap=%d enforce=%s",
                 call_sid, count, cap, enforce)
        return {
            "allow": not enforce,   # in shadow mode, still allow
            "reason": "sms_cap_reached",
            "body": truncated,
            "count_before": count,
            "cap": cap,
            "enforcement_active": enforce,
        }

    if len(body or "") > len(truncated):
        log.info("sms_truncated call_sid=%s orig_len=%d final_len=%d",
                 call_sid, len(body or ""), len(truncated))

    return {
        "allow": True,
        "reason": "ok",
        "body": truncated,
        "count_before": count,
        "cap": cap,
        "enforcement_active": enforce,
    }
