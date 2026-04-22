"""Per-client scheduler for timezone-sensitive jobs.

Today: owner end-of-day digest at 22:00 local time per client.
Future: add more per-tz jobs here; keep this module boring.

Design:
  - One asyncio task wakes every 60 seconds, iterates active clients,
    and for each client checks if local time is in the digest hour and
    we haven't sent today. In-memory dedupe keyed by `(client_id, date)`.
  - State is lost on restart (expected — worst case is a duplicate
    digest if the server bounces at 22:00:30 local). Not persisted
    because the tradeoff isn't worth the complexity.

Env:
  OWNER_DIGEST_HOUR_LOCAL  default '22' (10 PM)
  ENFORCE_OWNER_DIGEST     default 'true'
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from src import owner_digest, tenant

log = logging.getLogger("scheduler")

_sent_today: dict = {}  # (client_id, date_iso) -> True
_task: Optional[asyncio.Task] = None


def _reset_state():
    """Test hook."""
    _sent_today.clear()


def _digest_hour() -> int:
    try:
        return int(os.environ.get("OWNER_DIGEST_HOUR_LOCAL", "22"))
    except ValueError:
        return 22


def _eval_hour_utc() -> int:
    try:
        return int(os.environ.get("EVAL_REGRESSION_HOUR_UTC", "7"))
    except ValueError:
        return 7


_eval_last_run_date: Optional[str] = None


def _twilio_client():
    try:
        import main as _main
        return _main._twilio_client()
    except Exception:
        return None


def _maybe_run_eval_regression(now_utc: datetime):
    """Once a day at EVAL_REGRESSION_HOUR_UTC, run the eval suite and
    alert on regression. Guarded so multiple ticks in the same hour
    don't re-run."""
    if os.environ.get("ENFORCE_EVAL_REGRESSION", "false").lower() != "true":
        return
    global _eval_last_run_date
    if now_utc.hour != _eval_hour_utc():
        return
    today = now_utc.date().isoformat()
    if _eval_last_run_date == today:
        return
    _eval_last_run_date = today
    try:
        from evals import regression_detector
        out = regression_detector.run()
        log.info("eval regression run: pass_rate=%s regressed=%s",
                 out.get("summary_meta", {}).get("pass_rate"),
                 out.get("diff", {}).get("regressed"))
    except Exception as e:
        log.error("eval regression run failed: %s", e)


def tick(now_utc: Optional[datetime] = None):
    """One iteration of the scheduler. Public so tests can drive it
    without setting up an event loop.

    For each active client:
      - Compute local time in client's timezone.
      - If local hour matches _digest_hour() and not sent today, send.

    Also runs the eval regression detector once per day at
    EVAL_REGRESSION_HOUR_UTC when ENFORCE_EVAL_REGRESSION=true.
    """
    now_utc = now_utc or datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")) if ZoneInfo \
              else datetime.utcnow()
    target_hour = _digest_hour()
    for client in tenant.list_all():
        cid = client.get("id") or ""
        if cid.startswith("_"):
            continue
        if not (client.get("inbound_number") or ""):
            continue
        try:
            tz = owner_digest._client_tz(client)
            now_local = now_utc.astimezone(tz)
        except Exception as e:
            log.error("scheduler tz error for %s: %s", cid, e)
            continue
        if now_local.hour != target_hour:
            continue
        key = (cid, now_local.date().isoformat())
        if _sent_today.get(key):
            continue
        try:
            result = owner_digest.send_digest(
                client,
                twilio_client=_twilio_client(),
                local_date=now_local.date(),
            )
        except Exception as e:  # never let one client break the loop
            log.error("owner_digest.send_digest raised for %s: %s", cid, e)
            continue
        if result.get("sent") or result.get("reason") == "flag_off":
            # Mark as handled even when flag-off so we don't spam the log
            _sent_today[key] = True
        log.info("owner_digest tick for %s result=%s",
                 cid, {k: v for k, v in result.items() if k != "digest"})

    # Eval regression (independent of clients)
    _maybe_run_eval_regression(now_utc)


async def _loop():
    while True:
        try:
            now_utc = datetime.utcnow()
            if ZoneInfo:
                now_utc = now_utc.replace(tzinfo=ZoneInfo("UTC"))
            tick(now_utc=now_utc)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("scheduler loop error: %s", e)
        await asyncio.sleep(60)


def start():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())
        log.info("scheduler started (owner_digest hour=%d local)", _digest_hour())


def stop():
    global _task
    if _task:
        _task.cancel()
        _task = None
