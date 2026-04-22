"""Twilio webhook signature verification middleware.

Twilio signs every webhook with `X-Twilio-Signature` — HMAC-SHA1 over
the full URL + sorted form params, keyed by the account auth token. This
middleware rejects any /voice/* or /sms/incoming request whose signature
doesn't match (when enforcement is on) or logs a warning (shadow mode).

Why middleware rather than a FastAPI dependency: the spec asked for a
wrapper around the transport paths, and handling it at the middleware
layer means FastAPI's form parsing never runs on a bad request.

Tunnel behavior: when cloudflared / nginx sits in front, the request the
app sees is http://... but Twilio signed https://public-domain/...
`X-Forwarded-Proto` + `X-Forwarded-Host` are honored to reconstruct the
URL Twilio actually signed.

Env:
  TWILIO_VERIFY_SIGNATURES   default 'true'  — flip to 'false' for
                                               shadow mode during rollout.
  TWILIO_AUTH_TOKEN          already required — reused here.
  PUBLIC_BASE_URL            optional — if set, overrides
                                        scheme+host detection entirely.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Awaitable, Iterable
from urllib.parse import parse_qsl

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

log = logging.getLogger("twilio_signature")


# Paths we guard. All other paths pass through untouched.
PROTECTED_PATHS: tuple = ("/voice/incoming", "/voice/setlang",
                          "/voice/gather", "/voice/status",
                          "/sms/incoming")


def _enforce() -> bool:
    return os.environ.get("TWILIO_VERIFY_SIGNATURES", "true").lower() == "true"


def _resolve_url(request: Request) -> str:
    """Reconstruct the URL Twilio actually signed, honoring tunnel headers."""
    override = os.environ.get("PUBLIC_BASE_URL")
    if override:
        return override.rstrip("/") + request.url.path + (
            ("?" + request.url.query) if request.url.query else ""
        )
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    path = request.url.path
    query = request.url.query
    q = ("?" + query) if query else ""
    return f"{scheme}://{host}{path}{q}"


def _validator():
    """Lazy-init the Twilio RequestValidator. Returns None if either the
    twilio SDK or the auth token isn't available — in which case we fall
    back to shadow-mode-style pass-through."""
    token = os.environ.get("TWILIO_AUTH_TOKEN") or ""
    if not token:
        return None
    try:
        from twilio.request_validator import RequestValidator
    except ImportError:  # pragma: no cover
        return None
    return RequestValidator(token)


class TwilioSignatureMiddleware(BaseHTTPMiddleware):
    """Verifies X-Twilio-Signature on the protected transport paths.

    Reads the raw body, verifies, and re-yields it to downstream handlers
    so FastAPI's form parsing still works. Non-POST / unprotected paths
    pass through with no overhead.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method.upper() != "POST":
            return await call_next(request)
        if request.url.path not in PROTECTED_PATHS:
            return await call_next(request)

        # Cache the body so downstream handlers can still read it
        body = await request.body()

        signature = request.headers.get("x-twilio-signature") or ""
        validator = _validator()
        enforce = _enforce()

        valid = False
        reason = ""
        if validator is None:
            # No token / SDK — don't block, but log every request as
            # unverifiable. Operator must enable twilio creds.
            reason = "validator_unavailable"
        else:
            url = _resolve_url(request)
            params = {k: v for k, v in parse_qsl(
                body.decode("utf-8", errors="replace"),
                keep_blank_values=True,
            )}
            try:
                valid = validator.validate(url, params, signature)
            except Exception as e:  # pragma: no cover
                log.error("twilio sig validate raised: %s", e)
                reason = f"validator_error:{type(e).__name__}"

        if not valid and enforce:
            log.warning(
                "twilio signature rejected path=%s reason=%s has_sig=%s",
                request.url.path, reason or "invalid_signature", bool(signature),
            )
            return JSONResponse(
                status_code=403,
                content={"error": "invalid_twilio_signature",
                         "detail": "X-Twilio-Signature did not match."},
            )

        if not valid and not enforce:
            log.warning(
                "twilio signature shadow-pass path=%s reason=%s",
                request.url.path, reason or "invalid_signature",
            )

        # Re-yield the body to the downstream receive channel
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        request._receive = receive
        return await call_next(request)
