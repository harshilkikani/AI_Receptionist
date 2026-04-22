"""End-of-day owner digest.

Per-client summary fired once per day at ~22:00 local time (client
timezone). Delivered via SMS to `owner_cell` if set, else email to
`owner_email`, else skipped with a log line.

Body:
  - calls_total, emergencies, bookings_captured, spam_filtered
  - avg_response_s (mean duration of HANDLED calls; zeros excluded)
  - top_issue_themes (top 3 intents by turn count)

SMS bodies are capped to 320 chars via sms_limiter.cap_length so they
fit in 2 segments (single message on most carriers).

Scheduling lives in src/scheduler.py — this module is the pure
build + send API plus a CLI.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from src import sms_limiter, tenant, usage

log = logging.getLogger("owner_digest")


def _enforcement_active() -> bool:
    global_on = os.environ.get("MARGIN_PROTECTION_ENABLED", "true").lower() != "false"
    feature_on = os.environ.get("ENFORCE_OWNER_DIGEST", "true").lower() == "true"
    return global_on and feature_on


def _client_tz(client: dict) -> "ZoneInfo":
    tz_name = (client or {}).get("timezone") or "America/New_York"
    if ZoneInfo is None:  # pragma: no cover
        return timezone.utc
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("America/New_York")


def _local_day_bounds(client: dict, local_date) -> tuple:
    """Return (start_ts_utc, end_ts_utc) for the given local date."""
    tz = _client_tz(client)
    start_local = datetime(local_date.year, local_date.month, local_date.day,
                           0, 0, 0, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return (int(start_local.astimezone(timezone.utc).timestamp()),
            int(end_local.astimezone(timezone.utc).timestamp()))


def build_digest(client: dict, local_date=None) -> dict:
    """Query the usage DB for one client's activity inside one local day.
    Returns the digest dict without sending anything."""
    if not client or (client.get("id") or "").startswith("_"):
        raise ValueError("cannot digest a reserved/empty client")

    tz = _client_tz(client)
    if local_date is None:
        local_date = datetime.now(tz).date()
    start_ts, end_ts = _local_day_bounds(client, local_date)
    cid = client["id"]

    from src.usage import _connect, _init_schema, _db_lock  # type: ignore
    with _db_lock:
        conn = _connect()
        _init_schema(conn)

        calls_row = conn.execute("""
            SELECT
              COUNT(*)                                               AS total,
              SUM(CASE WHEN outcome IN ('spam_number','spam_phrase','silence_timeout')
                       THEN 1 ELSE 0 END)                            AS filtered,
              SUM(emergency)                                         AS emergencies,
              SUM(CASE WHEN outcome NOT IN ('spam_number','spam_phrase','silence_timeout')
                            AND duration_s IS NOT NULL AND duration_s > 0
                       THEN duration_s ELSE 0 END)                   AS handled_sec_sum,
              SUM(CASE WHEN outcome NOT IN ('spam_number','spam_phrase','silence_timeout')
                            AND duration_s IS NOT NULL AND duration_s > 0
                       THEN 1 ELSE 0 END)                            AS handled_count
              FROM calls
             WHERE client_id=? AND start_ts>=? AND start_ts<?
        """, (cid, start_ts, end_ts)).fetchone()

        bookings_row = conn.execute("""
            SELECT COUNT(DISTINCT call_sid) AS n
              FROM turns
             WHERE client_id=? AND intent='Scheduling' AND call_sid<>''
               AND ts>=? AND ts<?
        """, (cid, start_ts, end_ts)).fetchone()

        intents = conn.execute("""
            SELECT intent, COUNT(*) AS n
              FROM turns
             WHERE client_id=? AND intent IS NOT NULL AND intent <> ''
               AND ts>=? AND ts<?
          GROUP BY intent
          ORDER BY n DESC
             LIMIT 3
        """, (cid, start_ts, end_ts)).fetchall()

        conn.close()

    handled_count = int(calls_row["handled_count"] or 0)
    handled_sec_sum = int(calls_row["handled_sec_sum"] or 0)
    avg_response_s = (handled_sec_sum / handled_count) if handled_count else 0.0

    return {
        "client_id": cid,
        "client_name": client.get("name") or cid,
        "date": local_date.isoformat(),
        "timezone": (client.get("timezone") or "America/New_York"),
        "calls_total": int(calls_row["total"] or 0),
        "emergencies": int(calls_row["emergencies"] or 0),
        "bookings_captured": int(bookings_row["n"] or 0),
        "spam_filtered": int(calls_row["filtered"] or 0),
        "avg_response_s": round(avg_response_s, 1),
        "top_issue_themes": [r["intent"] for r in intents],
        "owner_cell": client.get("owner_cell") or "",
        "owner_email": client.get("owner_email") or "",
    }


# ── Rendering ──────────────────────────────────────────────────────────

def render_sms(digest: dict) -> str:
    """Short SMS body. Capped at 320 chars."""
    themes = (
        ", ".join(digest["top_issue_themes"][:2])
        if digest["top_issue_themes"]
        else "—"
    )
    body = (
        f"[{digest['client_name']}] {digest['date']}: "
        f"{digest['calls_total']} calls, "
        f"{digest['emergencies']} emergency, "
        f"{digest['bookings_captured']} bookings, "
        f"{digest['spam_filtered']} filtered. "
        f"Avg {int(digest['avg_response_s'])}s. "
        f"Top: {themes}."
    )
    return sms_limiter.cap_length(body)


def render_email(digest: dict) -> tuple:
    """Return (subject, html_body)."""
    subject = (
        f"{digest['client_name']} — {digest['date']} daily summary"
    )
    themes = (
        ", ".join(digest["top_issue_themes"])
        if digest["top_issue_themes"]
        else "—"
    )
    html = f"""<!doctype html>
<html><body style="font:14px -apple-system,Segoe UI,sans-serif;color:#1a1a1a;padding:16px">
<h2 style="margin-top:0">Daily summary — {digest['date']}</h2>
<p>{digest['client_name']} ({digest['timezone']})</p>
<table style="border-collapse:collapse">
  <tr><td style="padding:4px 12px 4px 0">Calls</td><td><b>{digest['calls_total']}</b></td></tr>
  <tr><td style="padding:4px 12px 4px 0">Emergencies routed</td><td><b>{digest['emergencies']}</b></td></tr>
  <tr><td style="padding:4px 12px 4px 0">Bookings captured</td><td><b>{digest['bookings_captured']}</b></td></tr>
  <tr><td style="padding:4px 12px 4px 0">Spam filtered</td><td>{digest['spam_filtered']}</td></tr>
  <tr><td style="padding:4px 12px 4px 0">Avg handled duration</td><td>{digest['avg_response_s']}s</td></tr>
  <tr><td style="padding:4px 12px 4px 0">Top themes</td><td>{themes}</td></tr>
</table>
<p style="color:#888;font-size:12px">AI receptionist — review /admin for full call log.</p>
</body></html>"""
    return subject, html


# ── Send ───────────────────────────────────────────────────────────────

def _send_sms(digest: dict, twilio_client, twilio_from: str) -> bool:
    to_number = digest["owner_cell"]
    if not to_number or twilio_client is None or not twilio_from:
        return False
    body = render_sms(digest)
    try:
        twilio_client.messages.create(to=to_number, from_=twilio_from, body=body)
    except Exception as e:
        log.error("owner_digest SMS failed for %s: %s", digest["client_id"], e)
        return False
    usage.log_sms(call_sid=f"DIGEST_{digest['date']}_{digest['client_id']}",
                  client_id=digest["client_id"], to_number=to_number,
                  body=body, direction="owner_digest")
    return True


def _send_email(digest: dict) -> bool:
    if not digest["owner_email"]:
        return False
    # Reuse alerts SMTP settings
    from src import alerts as _alerts
    cfg = _alerts._cfg() or {}
    smtp = cfg.get("smtp") or {}
    host = smtp.get("host") or ""
    port = int(smtp.get("port") or 587)
    user = smtp.get("user") or ""
    password = os.environ.get("ALERT_SMTP_PASSWORD", "")
    from_addr = smtp.get("from") or user
    if not (host and user and password):
        log.info("owner_digest email suppressed: SMTP config incomplete")
        return False
    import smtplib
    import ssl
    from email.mime.text import MIMEText
    subject, body = render_email(digest)
    msg = MIMEText(body, "html")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = digest["owner_email"]
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=30) as s:
            if smtp.get("tls"):
                s.starttls(context=ctx)
            s.login(user, password)
            s.sendmail(from_addr, [digest["owner_email"]], msg.as_string())
        return True
    except Exception as e:
        log.error("owner_digest email failed for %s: %s", digest["client_id"], e)
        return False


def send_digest(client: dict, twilio_client=None,
                twilio_from: Optional[str] = None,
                local_date=None) -> dict:
    """Build + send one client's digest via the best available channel."""
    digest = build_digest(client, local_date=local_date)
    if not _enforcement_active():
        log.info("owner_digest suppressed by flag: %s", client.get("id"))
        return {"sent": False, "reason": "flag_off", "digest": digest}

    # Prefer SMS when owner_cell is available
    if digest["owner_cell"]:
        twilio_from = twilio_from or os.environ.get("TWILIO_NUMBER") or ""
        ok = _send_sms(digest, twilio_client, twilio_from)
        if ok:
            return {"sent": True, "via": "sms", "digest": digest}
        # Fall through to email on SMS failure

    if digest["owner_email"]:
        ok = _send_email(digest)
        if ok:
            return {"sent": True, "via": "email", "digest": digest}

    return {"sent": False, "reason": "no_channel_available", "digest": digest}


# ── CLI ────────────────────────────────────────────────────────────────

def _cli(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.owner_digest")
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("preview", help="print digest + SMS body for one client")
    pv.add_argument("client_id")
    pv.add_argument("date", nargs="?", default=None,
                    help="YYYY-MM-DD (default: today in client tz)")

    s = sub.add_parser("send", help="send digest for one client NOW")
    s.add_argument("client_id")
    s.add_argument("date", nargs="?", default=None)

    args = p.parse_args(argv)
    client = tenant.load_client_by_id(args.client_id)
    if client is None or (client.get("id") or "").startswith("_"):
        print(f"Unknown or reserved client: {args.client_id}", file=sys.stderr)
        return 2

    local_date = None
    if args.date:
        try:
            local_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Bad date: {args.date} (expected YYYY-MM-DD)", file=sys.stderr)
            return 2

    if args.cmd == "preview":
        digest = build_digest(client, local_date=local_date)
        print(json.dumps(digest, indent=2))
        print("\n--- SMS body ---")
        print(render_sms(digest))
        return 0

    if args.cmd == "send":
        # Lazy import so tests that monkeypatch main's twilio client work
        try:
            import main as _main
            tw = _main._twilio_client()
        except Exception:
            tw = None
        result = send_digest(client, twilio_client=tw, local_date=local_date)
        print(json.dumps({k: v for k, v in result.items() if k != "digest"}))
        return 0 if result.get("sent") else 1
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
