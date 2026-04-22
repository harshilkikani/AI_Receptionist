"""Owner notifications — emergency SMS push.

When a call is transferred to escalation_phone for an emergency, also
SMS the owner's cell so they see WHO called and the gist before their
phone rings. The owner cell comes from `client.owner_cell`, with a
fallback to `client.escalation_phone` when unset.

Design:
  - Best-effort. If Twilio creds are missing or send fails, the call
    still transfers. An owner_alert failure never disrupts the caller.
  - Direction='owner_alert' in the usage DB — excluded from the
    per-call sms cap (src.sms_limiter counts only direction='outbound')
    but included in billable SMS totals (src.usage.monthly_summary).
  - Body capped at 320 chars via src.sms_limiter.cap_length.

Env:
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_NUMBER — used for the
  outbound send. If any are missing the send is skipped with reason
  'twilio_unavailable'.

Feature-flag gate:
  ENFORCE_OWNER_EMERGENCY_SMS (default 'true') — set 'false' to suppress
  the send (shadow mode). Global kill switch MARGIN_PROTECTION_ENABLED
  is respected.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from src import sms_limiter, usage

log = logging.getLogger("owner_notify")


def _enforcement_active() -> bool:
    global_on = os.environ.get("MARGIN_PROTECTION_ENABLED", "true").lower() != "false"
    feature_on = os.environ.get("ENFORCE_OWNER_EMERGENCY_SMS", "true").lower() == "true"
    return global_on and feature_on


def _resolve_owner_number(client: dict) -> str:
    cell = (client or {}).get("owner_cell") or ""
    if cell:
        return cell
    # Fallback to escalation_phone (the on-call). Gives operator a single
    # number to update if they don't want to split the two roles yet.
    return (client or {}).get("escalation_phone") or ""


def build_body(*, caller_phone: str, summary: str,
               address: Optional[str] = None, client_name: str = "") -> str:
    """Produce the SMS body. Length-capped via sms_limiter."""
    head = f"Emergency {client_name}: {caller_phone}"
    bits = [head.strip(" :")]
    if summary:
        bits.append(f'"{summary.strip()}"')
    if address:
        bits.append(f"Addr on file: {address}")
    body = " — ".join(bits)
    return sms_limiter.cap_length(body)


def notify_emergency(client: dict, *,
                     caller_phone: str,
                     summary: str,
                     address: Optional[str] = None,
                     call_sid: str = "",
                     twilio_client=None,
                     twilio_from: Optional[str] = None) -> dict:
    """Send the owner emergency SMS. Returns {sent, reason, to, body}.

    Never raises — even a misconfiguration returns sent=False.
    """
    to_number = _resolve_owner_number(client)
    if not to_number:
        return {"sent": False, "reason": "no_owner_number",
                "to": "", "body": ""}

    body = build_body(
        caller_phone=caller_phone,
        summary=summary or "",
        address=address,
        client_name=client.get("name") or "",
    )

    if not _enforcement_active():
        log.info("owner_alert suppressed by flag: client=%s", client.get("id"))
        # Log shadow event for analytics but skip the real send
        usage.log_sms(call_sid, client.get("id", ""), to_number, body,
                      direction="owner_alert_shadow")
        return {"sent": False, "reason": "flag_off",
                "to": to_number, "body": body}

    twilio_from = twilio_from or os.environ.get("TWILIO_NUMBER") or ""
    if twilio_client is None or not twilio_from:
        log.info("owner_alert skipped: twilio unavailable client=%s",
                 client.get("id"))
        return {"sent": False, "reason": "twilio_unavailable",
                "to": to_number, "body": body}

    try:
        twilio_client.messages.create(to=to_number, from_=twilio_from, body=body)
    except Exception as e:
        log.error("owner_alert send failed for %s: %s", client.get("id"), e)
        return {"sent": False, "reason": f"send_error:{type(e).__name__}",
                "to": to_number, "body": body}

    usage.log_sms(call_sid, client.get("id", ""), to_number, body,
                  direction="owner_alert")
    log.info("owner_alert sent: client=%s to=%s", client.get("id"), to_number)
    return {"sent": True, "reason": "ok", "to": to_number, "body": body}
