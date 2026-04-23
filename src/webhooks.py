"""V3.13 — Webhook event bus for client integrations.

Each tenant subscribes to events by adding a `webhooks:` block to
their YAML:

    webhooks:
      - url: "https://hooks.zapier.com/..."
        events: ["call.ended", "booking.created"]
        secret: "whsec_..."              # optional HMAC signing key
      - url: "https://n8n.ops.example/webhook/abc"
        events: ["emergency.triggered", "feedback.negative"]

Event names:
  - call.started
  - call.ended
  - emergency.triggered
  - booking.created
  - feedback.negative

`fire(event, client, data)` posts one JSON body per matching subscription.
Signing uses HMAC-SHA256 over the raw body with the per-subscription
secret; signature lands in the `X-AI-Receptionist-Signature` header as
`sha256=<hex>`. Recipients verify by recomputing.

Best-effort, synchronous, single attempt. Recipients that respond slow
block the webhook handler (we enforce a 5-second timeout). For higher-
reliability delivery, point these at a lightweight intermediary like a
Zapier catch hook or n8n trigger — those retry for you.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional

log = logging.getLogger("webhooks")

KNOWN_EVENTS = frozenset({
    "call.started",
    "call.ended",
    "emergency.triggered",
    "booking.created",
    "feedback.negative",
})

_DELIVERY_TIMEOUT_SECONDS = 5


def _subscriptions_for(client: Optional[dict]) -> list:
    if not client:
        return []
    subs = client.get("webhooks") or []
    if not isinstance(subs, list):
        return []
    out = []
    for s in subs:
        if not isinstance(s, dict):
            continue
        url = (s.get("url") or "").strip()
        if not url:
            continue
        events = s.get("events") or []
        if not isinstance(events, list):
            events = []
        out.append({
            "url": url,
            "events": [str(e) for e in events],
            "secret": s.get("secret") or "",
        })
    return out


def _sign(body: bytes, secret: str) -> str:
    if not secret:
        return ""
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _post(url: str, body: bytes, signature: str, headers: Optional[dict] = None) -> tuple:
    h = {"Content-Type": "application/json",
         "User-Agent": "ai-receptionist-webhooks/1.0"}
    if headers:
        h.update(headers)
    if signature:
        h["X-AI-Receptionist-Signature"] = signature
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_DELIVERY_TIMEOUT_SECONDS) as r:
            return (r.status, None)
    except urllib.error.HTTPError as e:
        return (e.code, f"http_{e.code}")
    except urllib.error.URLError as e:
        return (0, f"url_error:{e.reason}")
    except Exception as e:  # socket timeout, etc.
        return (0, f"{type(e).__name__}")


def fire(event: str, client: Optional[dict], data: Optional[dict] = None,
         *, post_fn=None) -> list:
    """Deliver `event` to every subscription matching it.

    Returns a list of `{url, status, error}` dicts so tests and the
    /admin layer can observe delivery results.

    `post_fn` is injectable for tests (signature (url, body, signature) →
    (status_code, error_or_none)).
    """
    if event not in KNOWN_EVENTS:
        log.warning("webhooks.fire unknown event: %s", event)
        return []
    subs = _subscriptions_for(client)
    if not subs:
        return []
    payload = {
        "event": event,
        "client_id": (client or {}).get("id") or "",
        "ts": int(time.time()),
        "data": data or {},
    }
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    post_fn = post_fn or _post

    results = []
    for sub in subs:
        if event not in sub["events"]:
            continue
        sig = _sign(body, sub["secret"])
        status, err = post_fn(sub["url"], body, sig)
        ok = status and 200 <= status < 300
        results.append({
            "url": sub["url"],
            "event": event,
            "status": status,
            "error": err,
            "delivered": bool(ok),
        })
        if not ok:
            log.warning("webhook delivery failed event=%s url=%s status=%s err=%s",
                        event, sub["url"], status, err)
        else:
            log.info("webhook delivered event=%s url=%s status=%d",
                     event, sub["url"], status)
    return results


def fire_safe(event: str, client: Optional[dict], data: Optional[dict] = None) -> None:
    """fire() with an outer try/except so a crash in delivery never
    bubbles up to the Twilio webhook handler."""
    try:
        fire(event, client, data)
    except Exception as e:
        log.error("webhooks.fire_safe crashed: %s", e)
