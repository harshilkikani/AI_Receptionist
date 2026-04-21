"""Usage alerting — thresholds at 60/80/100/150% of plan.

Transport: SMTP OR webhook (operator picks one in config/alerts.json).
Mode: digest (daily, default) or event (per-crossing).

In digest mode, we build one payload per day summarizing every client's
margin + threshold status. In event mode, we post one notification per
threshold crossing (tracked in-memory; process restart re-fires).

Feature flag: ENFORCE_USAGE_ALERTS (default TRUE — alerts are safe).
Global kill: MARGIN_PROTECTION_ENABLED respected.

The digest loop runs as an asyncio task kicked off at FastAPI startup.
It sleeps until the next digest_hour_utc, sends, and repeats.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import smtplib
import ssl
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from src import tenant, usage

log = logging.getLogger("alerts")

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "alerts.json"
_config_cache: Optional[dict] = None
_fired: dict = {}  # client_id -> set(threshold_names_fired_this_month)


def reload():
    global _config_cache, _fired
    _config_cache = None
    _fired = {}


def _cfg() -> dict:
    global _config_cache
    if _config_cache is None:
        try:
            _config_cache = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _config_cache = {}
    return _config_cache


def _enforcement_active() -> bool:
    global_on = os.environ.get("MARGIN_PROTECTION_ENABLED", "true").lower() != "false"
    alerts_on = os.environ.get("ENFORCE_USAGE_ALERTS", "true").lower() == "true"
    return global_on and alerts_on


# ── Transport ──────────────────────────────────────────────────────────

def _send_webhook(payload: dict) -> bool:
    url = (_cfg().get("webhook") or {}).get("url") or ""
    if not url:
        log.info("webhook alert suppressed: no URL configured")
        return False
    headers = (_cfg().get("webhook") or {}).get("headers") or {}
    headers = {**headers, "Content-Type": "application/json"}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = 200 <= r.status < 300
            log.info("webhook alert sent: status=%d", r.status)
            return ok
    except urllib.error.URLError as e:
        log.error("webhook alert failed: %s", e)
        return False


def _send_email(subject: str, body: str) -> bool:
    smtp = _cfg().get("smtp") or {}
    host = smtp.get("host") or ""
    port = int(smtp.get("port") or 587)
    user = smtp.get("user") or ""
    password = os.environ.get("ALERT_SMTP_PASSWORD", "")
    from_addr = smtp.get("from") or user
    to_list = smtp.get("to") or []
    if not (host and user and password and to_list):
        log.info("email alert suppressed: SMTP config incomplete")
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_list)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=30) as s:
            if smtp.get("tls"):
                s.starttls(context=context)
            s.login(user, password)
            s.sendmail(from_addr, to_list, msg.as_string())
        log.info("email alert sent to %d recipients", len(to_list))
        return True
    except Exception as e:
        log.error("email alert failed: %s", e)
        return False


def _dispatch(subject: str, body: str, payload: dict) -> bool:
    transport = (_cfg().get("transport") or "webhook").lower()
    if transport == "smtp":
        return _send_email(subject, body)
    return _send_webhook(payload)


# ── Threshold evaluation ───────────────────────────────────────────────

def _threshold_name(pct: float, thresholds: dict) -> Optional[str]:
    """Return the highest threshold crossed, or None if below all."""
    if pct >= thresholds.get("urgent_pct", 150):
        return "urgent"
    if pct >= thresholds.get("overage_pct", 100):
        return "overage"
    if pct >= thresholds.get("notify_pct", 80):
        return "notify"
    if pct >= thresholds.get("log_pct", 60):
        return "log"
    return None


def _pct_consumed(client: dict, summary: dict) -> float:
    """How much of the plan's included minutes has been consumed (%)."""
    plan = (client or {}).get("plan") or {}
    included_min = float(plan.get("included_minutes", 0) or 0)
    if included_min <= 0:
        # No plan limit → use call count against included_calls instead
        included_calls = float(plan.get("included_calls", 0) or 0)
        if included_calls <= 0:
            return 0.0
        return (summary.get("total_calls", 0) / included_calls) * 100.0
    return (summary.get("total_minutes", 0.0) / included_min) * 100.0


# ── Per-client evaluation ─────────────────────────────────────────────

def evaluate_client(client: dict) -> dict:
    """Check one client's current-month usage against thresholds. Returns
    the event dict (or a no-op dict if nothing crossed)."""
    summary = usage.monthly_summary(client["id"])
    margin = usage.margin_for(client)
    pct = _pct_consumed(client, summary)
    t = _cfg().get("thresholds") or {}
    name = _threshold_name(pct, t)
    return {
        "client_id": client["id"],
        "month": summary["month"],
        "pct_consumed": round(pct, 1),
        "threshold_name": name,
        "margin": margin,
        "summary": summary,
    }


def _format_digest(events: list) -> tuple:
    """Produce (subject, body, payload) for the daily digest."""
    if not events:
        return ("Receptionist daily digest (no clients)", "No active clients.", {"events": []})

    subject = f"Receptionist digest {datetime.now(timezone.utc).date()}"
    lines = [f"Daily digest for {subject}", ""]
    flagged = []
    for ev in events:
        cid = ev["client_id"]
        pct = ev["pct_consumed"]
        marker = f"[{ev['threshold_name'].upper()}]" if ev["threshold_name"] else "[ok]"
        mg = ev["margin"]
        line = (
            f"{marker} {cid}: {pct}% of plan consumed, "
            f"cost ${mg['platform_cost_usd']}, revenue ${mg['revenue_usd']}, "
            f"margin ${mg['margin_usd']} ({mg['margin_pct']}%)"
        )
        lines.append(line)
        if ev["threshold_name"] in ("overage", "urgent"):
            flagged.append(cid)

    if flagged:
        lines.insert(1, f"FLAGGED: {', '.join(flagged)}")
        lines.insert(2, "")

    body = "\n".join(lines)
    payload = {"events": events, "subject": subject}
    return (subject, body, payload)


def send_digest_now() -> dict:
    """Build + send today's digest. Safe to call from admin endpoints."""
    if not _enforcement_active():
        log.info("alerts disabled by flag; digest skipped")
        return {"sent": False, "reason": "disabled"}
    events = []
    for client in tenant.list_all():
        cid = client.get("id") or ""
        if cid.startswith("_"):
            continue
        # Skip reference configs that have no actual inbound number
        if not (client.get("inbound_number") or ""):
            continue
        events.append(evaluate_client(client))
    subject, body, payload = _format_digest(events)
    ok = _dispatch(subject, body, payload)
    return {"sent": ok, "events": len(events)}


# ── Background loop ───────────────────────────────────────────────────

_loop_task: Optional[asyncio.Task] = None


async def _digest_loop():
    """Sleep until the next digest hour, send, repeat."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            target_hour = int(_cfg().get("digest_hour_utc", 14))
            target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            wait_s = (target - now).total_seconds()
            log.info("next alert digest in %.0f minutes", wait_s / 60)
            await asyncio.sleep(wait_s)
            log.info("running daily alert digest")
            send_digest_now()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("digest_loop error: %s", e)
            # Back off 1 hour on error
            await asyncio.sleep(3600)


def start_background_loop():
    """Call from FastAPI startup event."""
    global _loop_task
    if _loop_task is None or _loop_task.done():
        _loop_task = asyncio.create_task(_digest_loop())
        log.info("alert digest loop started")


def stop_background_loop():
    global _loop_task
    if _loop_task:
        _loop_task.cancel()
        _loop_task = None
