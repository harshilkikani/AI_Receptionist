"""V4.6 — per-tenant ICS calendar feed.

Each tenant gets a single URL that returns an ICS feed of all their
bookings. Subscribable in Google Calendar / Apple Calendar / Outlook
via "Add calendar by URL" — those apps poll every few hours and the
new bookings just show up.

Auth: reuses CLIENT_PORTAL_SECRET HMAC scheme so the same operator
who minted the portal URL can hand the calendar URL to Bob without
managing a second credential.

Mint with:
    python -m src.calendar_feed url <client_id>

URL shape:
    https://example.com/calendar/septic_pro.ics?t=<hmac-token>
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from src import bookings, client_portal, tenant

log = logging.getLogger("calendar_feed")

router = APIRouter(tags=["calendar"])


def feed_url(client_id: str, base_url: Optional[str] = None) -> str:
    """Mint a signed feed URL. Reuses portal HMAC scheme."""
    token = client_portal.issue_token(client_id)
    base = base_url or os.environ.get("PUBLIC_BASE_URL", "http://localhost:8765")
    return f"{base.rstrip('/')}/calendar/{client_id}.ics?t={token}"


@router.get("/calendar/{client_id}.ics")
def feed(client_id: str, t: str = ""):
    """Return the tenant's ICS feed. 403 on bad token, also for
    reserved/missing tenants (no enumeration leak)."""
    client = tenant.load_client_by_id(client_id)
    if client is None or (client.get("id") or "").startswith("_"):
        raise HTTPException(status_code=403, detail="invalid token")
    if not client_portal.verify_token(client_id, t):
        raise HTTPException(status_code=403, detail="invalid token")

    rows = bookings.list_bookings(client_id=client_id, limit=200)
    body = bookings.generate_feed_ics(rows, tenant_name=client.get("name") or client_id)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            # Subscribed calendars work by URL; suggest a filename if
            # someone downloads instead.
            "Content-Disposition": f'inline; filename="{client_id}.ics"',
            "Cache-Control": "private, max-age=300",
        },
    )


# ── CLI ────────────────────────────────────────────────────────────

def _cli(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.calendar_feed")
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("url", help="print the calendar feed URL for a client")
    u.add_argument("client_id")
    u.add_argument("--base-url", default=None)
    args = p.parse_args(argv)
    if args.cmd == "url":
        c = tenant.load_client_by_id(args.client_id)
        if c is None or (c.get("id") or "").startswith("_"):
            print(f"Unknown client: {args.client_id}", file=sys.stderr)
            return 2
        if not client_portal._secret():
            print("CLIENT_PORTAL_SECRET is not set.", file=sys.stderr)
            return 2
        print(feed_url(args.client_id, base_url=args.base_url))
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
