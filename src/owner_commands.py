"""V6 — owner-facing SMS commands and welcome flow.

Two surfaces:
  1. `handle_help_sms(body, from_phone, client, twilio_client, twilio_from)`
     — called from /sms/incoming before the LLM pipeline. If the message
     matches a HELP/INFO/STATUS keyword, reply with a useful cheat sheet:
     portal URL + escalation contact + how to reach a human. Owners
     (recognized by owner_cell / escalation_phone) get the full link;
     unknown callers get a polite "call us for service" redirect.

  2. `build_welcome_body(client)` + `send_welcome_sms(...)` — used by
     the onboarding wizard's new `welcome` subcommand to push one SMS
     confirming the service is live + sharing the portal URL.

Keeps the /sms/incoming handler flat by owning the "is this a
command?" logic here.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from src import sms_limiter, usage

log = logging.getLogger("owner_commands")

# Keywords that trip the command handler (case-insensitive, first word)
HELP_KEYWORDS = {"help", "info", "status", "link"}


def _normalize_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _is_owner(from_phone: str, client: dict) -> bool:
    """True if `from_phone` matches the client's owner_cell or escalation_phone."""
    cand = _normalize_phone(from_phone)
    for f in ("owner_cell", "escalation_phone"):
        n = _normalize_phone((client or {}).get(f) or "")
        if n and n == cand:
            return True
    return False


def is_help_command(body: str) -> bool:
    if not body:
        return False
    first = body.strip().split()[0] if body.strip().split() else ""
    return first.lower().strip(".,!?;:") in HELP_KEYWORDS


def _portal_url(client: dict) -> Optional[str]:
    """Try to mint a portal URL. Returns None if secret isn't set."""
    try:
        from src import client_portal
        if not client_portal._secret():
            return None
        return client_portal.portal_url(client["id"])
    except Exception as e:
        log.error("portal_url failed for %s: %s", client.get("id"), e)
        return None


def build_owner_help_body(client: dict) -> str:
    """Body for a recognized owner. Tries to include portal URL."""
    owner_name = (client or {}).get("owner_name") or "there"
    biz_name = (client or {}).get("name") or ""
    escalation = (client or {}).get("escalation_phone") or ""
    portal = _portal_url(client)
    lead = f"Hey {owner_name} — {biz_name}'s AI receptionist is active." \
        if biz_name else f"Hey {owner_name} — your AI receptionist is active."
    lines = [lead]
    if portal:
        lines.append(f"Dashboard: {portal}")
    if escalation:
        lines.append(f"Emergency line: {escalation}")
    lines.append("Reply to this text if anything looks off.")
    return sms_limiter.cap_length(" ".join(lines))


def build_public_help_body(client: dict) -> str:
    """Body for an unknown caller texting HELP."""
    name = (client or {}).get("name") or "our office"
    return sms_limiter.cap_length(
        f"You've reached {name}. For service, please call the number "
        f"you just texted instead of messaging — we answer calls faster."
    )


def handle_help_sms(body: str, *, from_phone: str, client: dict,
                    twilio_client=None, twilio_from: Optional[str] = None) -> dict:
    """Respond to a HELP-style inbound SMS. Returns dict with `handled`,
    `reply` (the body we would/did send), and `via` ('twilio' or 'twiml')."""
    if not is_help_command(body):
        return {"handled": False, "reply": ""}

    if _is_owner(from_phone, client):
        reply = build_owner_help_body(client)
        variant = "owner"
    else:
        reply = build_public_help_body(client)
        variant = "public"

    # Log in the usage DB as a special direction so it's distinguishable
    # from normal outbound SMS but still counts toward billable.
    call_sid = f"HELP_{_normalize_phone(from_phone)}"
    usage.log_sms(call_sid, client.get("id", ""), from_phone, reply,
                  direction=f"help_{variant}")

    # If a Twilio client is provided + creds set, send proactively. The
    # FastAPI handler can alternatively return this body in TwiML
    # <Message>. The caller picks based on what's cheaper to them.
    sent = False
    if twilio_client is not None and twilio_from:
        try:
            twilio_client.messages.create(
                to=from_phone, from_=twilio_from, body=reply)
            sent = True
        except Exception as e:
            log.error("help send failed: %s", e)

    return {"handled": True, "reply": reply, "via": "twilio" if sent else "twiml",
            "variant": variant}


# ── Welcome flow ───────────────────────────────────────────────────────

def build_welcome_body(client: dict) -> str:
    """Welcome SMS sent by the operator on day 1 of service."""
    name = (client or {}).get("name") or "your business"
    owner_name = (client or {}).get("owner_name") or "there"
    portal = _portal_url(client)
    parts = [
        f"Hey {owner_name} — {name}'s AI receptionist is live.",
        "Save this number. Text HELP anytime for your dashboard link.",
    ]
    if portal:
        parts.append(f"Dashboard: {portal}")
    return sms_limiter.cap_length(" ".join(parts))


def send_welcome_sms(client: dict, *, twilio_client=None,
                     twilio_from: Optional[str] = None,
                     to_override: Optional[str] = None) -> dict:
    """Send the welcome SMS to the client's owner_cell (or override).
    Returns {sent, reason, to, body}."""
    to_number = to_override or (client or {}).get("owner_cell") or ""
    if not to_number:
        return {"sent": False, "reason": "no_owner_cell", "to": "", "body": ""}
    twilio_from = twilio_from or os.environ.get("TWILIO_NUMBER") or ""
    body = build_welcome_body(client)
    if twilio_client is None or not twilio_from:
        return {"sent": False, "reason": "twilio_unavailable",
                "to": to_number, "body": body}
    try:
        twilio_client.messages.create(to=to_number, from_=twilio_from, body=body)
    except Exception as e:
        log.error("welcome SMS failed: %s", e)
        return {"sent": False, "reason": f"send_error:{type(e).__name__}",
                "to": to_number, "body": body}
    usage.log_sms(f"WELCOME_{client.get('id', '')}",
                  client.get("id", ""), to_number, body, direction="welcome")
    return {"sent": True, "reason": "ok", "to": to_number, "body": body}
