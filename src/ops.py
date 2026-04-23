"""V7 — ops surfaces: /health, /ready, and a request-id correlation
middleware.

Three pieces:
  1. RequestIDMiddleware — attaches a short request ID to every request,
     sets it as an X-Request-ID response header, and exposes it to
     downstream log calls via a contextvar.

  2. `_request_id_filter` — a stdlib logging Filter that pulls the
     contextvar into each log record so `%(request_id)s` in the format
     string lights up with real IDs.

  3. /health (liveness) + /ready (readiness) routes — standard ops
     probes. /health is a cheap "did the process start?" signal;
     /ready additionally opens the SQLite DB and reads one row, so
     failures indicate the storage layer is down.
"""
from __future__ import annotations

import contextvars
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# ── request id plumbing ────────────────────────────────────────────────

_current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-",
)

_START_TS = int(time.time())
_APP_VERSION = os.environ.get("APP_VERSION", "v2.0")


def current_request_id() -> str:
    return _current_request_id.get()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Stamp every request with a short random correlation ID.

    - Honors an inbound `X-Request-ID` if present (so callers like
      cloudflared or an API gateway can set their own ID).
    - Otherwise mints 8 hex chars via secrets — short enough to read
      in logs, unique enough for a day's traffic.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rid = request.headers.get("x-request-id") or secrets.token_hex(4)
        token = _current_request_id.set(rid)
        try:
            response = await call_next(request)
        finally:
            _current_request_id.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


class _RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _current_request_id.get()
        return True


def install_logging(level: str = None):
    """Wire the request-id filter into the root logger. Idempotent.

    Reconfigures the root format so log lines include the request id.
    Safe to call once at app startup.
    """
    level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    fmt = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s %(message)s"
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt))
    handler.addFilter(_RequestIDFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


# ── routes ─────────────────────────────────────────────────────────────

router = APIRouter(tags=["ops"])


@router.get("/health")
def health():
    """Liveness probe — process is up and handling requests."""
    uptime = int(time.time() - _START_TS)
    return {
        "status": "ok",
        "version": _APP_VERSION,
        "uptime_s": uptime,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@router.get("/ready")
def ready():
    """Readiness probe — dependencies reachable. 503 on any failure so
    load balancers can stop sending traffic."""
    checks = {}
    overall_ok = True

    # 1. SQLite reachable
    try:
        from src.usage import _connect, _init_schema, _db_lock
        with _db_lock:
            conn = _connect()
            _init_schema(conn)
            conn.execute("SELECT 1").fetchone()
            conn.close()
        checks["sqlite"] = "ok"
    except Exception as e:
        checks["sqlite"] = f"fail:{type(e).__name__}:{e}"
        overall_ok = False

    # 2. At least one tenant config loads
    try:
        from src import tenant
        tenant.reload()
        clients = tenant.list_all()
        real = [c for c in clients if not (c.get("id") or "").startswith("_")
                and (c.get("inbound_number") or "")]
        if not real:
            checks["tenant"] = "warn:no_active_tenants"
        else:
            checks["tenant"] = f"ok:{len(real)}_active"
    except Exception as e:
        checks["tenant"] = f"fail:{type(e).__name__}:{e}"
        overall_ok = False

    # 3. Prompt template readable (LLM path)
    try:
        prompt_path = Path(__file__).parent.parent / "prompts" / "receptionist_core.md"
        if not prompt_path.exists():
            checks["prompt_template"] = "fail:missing"
            overall_ok = False
        else:
            checks["prompt_template"] = "ok"
    except Exception as e:
        checks["prompt_template"] = f"fail:{type(e).__name__}"
        overall_ok = False

    payload = {"status": "ok" if overall_ok else "degraded", "checks": checks}
    return JSONResponse(payload, status_code=200 if overall_ok else 503)


# ── /metrics (V3.15) ───────────────────────────────────────────────────

def _render_metrics() -> str:
    """Build Prometheus text-exposition output. Pure function so tests
    can call it without starting the app."""
    lines: list = []

    def helpline(name, desc, kind):
        lines.append(f"# HELP {name} {desc}")
        lines.append(f"# TYPE {name} {kind}")

    def _esc_label(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace('"', '\\"')

    # Process uptime
    helpline("receptionist_uptime_seconds",
             "Seconds since process start.", "gauge")
    lines.append(f"receptionist_uptime_seconds {int(time.time() - _START_TS)}")

    # Active calls (call_timer)
    try:
        from src import call_timer
        snap = call_timer.snapshot()
        active = len(snap)
        emergency_active = sum(1 for v in snap.values() if v.get("emergency"))
        helpline("receptionist_active_calls",
                 "Calls currently in flight.", "gauge")
        lines.append(f"receptionist_active_calls {active}")
        helpline("receptionist_active_emergency_calls",
                 "In-flight calls tagged as emergency.", "gauge")
        lines.append(f"receptionist_active_emergency_calls {emergency_active}")
    except Exception:
        pass

    # LLM degradation counter
    try:
        from llm import degradation_stats
        d = degradation_stats()
        helpline("receptionist_llm_degradations_total",
                 "Total LLM degradation events by reason.", "counter")
        for reason, n in (d.get("by_reason") or {}).items():
            lines.append(
                f'receptionist_llm_degradations_total{{reason="{_esc_label(reason)}"}} {n}'
            )
        if not (d.get("by_reason") or {}):
            # Emit a zero sample so scrape never returns empty counters
            lines.append('receptionist_llm_degradations_total{reason="none"} 0')
    except Exception:
        pass

    # Per-client metrics from usage aggregates
    try:
        from src import tenant, usage
        helpline("receptionist_calls_total",
                 "Calls in current month by client + outcome.", "counter")
        helpline_added = False
        for c in tenant.list_all():
            cid = c.get("id") or ""
            if cid.startswith("_"):
                continue
            if not (c.get("inbound_number") or ""):
                continue
            s = usage.monthly_summary(cid)
            # Emit
            lines.append(
                f'receptionist_calls_total{{client="{_esc_label(cid)}",outcome="all"}} '
                f'{s["total_calls"]}'
            )
            lines.append(
                f'receptionist_calls_total{{client="{_esc_label(cid)}",outcome="handled"}} '
                f'{s["calls_handled"]}'
            )
            lines.append(
                f'receptionist_calls_total{{client="{_esc_label(cid)}",outcome="filtered"}} '
                f'{s["calls_filtered"]}'
            )
        helpline("receptionist_minutes_total",
                 "Total call minutes in current month by client.", "counter")
        helpline("receptionist_emergencies_total",
                 "Emergency calls in current month by client.", "counter")
        helpline("receptionist_margin_pct",
                 "Current-month margin percent by client.", "gauge")
        for c in tenant.list_all():
            cid = c.get("id") or ""
            if cid.startswith("_"):
                continue
            if not (c.get("inbound_number") or ""):
                continue
            m = usage.margin_for(c)
            lines.append(
                f'receptionist_minutes_total{{client="{_esc_label(cid)}"}} '
                f'{m["total_minutes"]:.1f}'
            )
            lines.append(
                f'receptionist_emergencies_total{{client="{_esc_label(cid)}"}} '
                f'{m["emergencies"]}'
            )
            lines.append(
                f'receptionist_margin_pct{{client="{_esc_label(cid)}"}} '
                f'{m["margin_pct"]}'
            )
    except Exception:
        pass

    # Sentiment-escalation counter (process-local)
    try:
        from src import sentiment_tracker
        escalated = sum(
            1 for v in sentiment_tracker.snapshot().values()
            if v.get("escalated"))
        helpline("receptionist_sentiment_escalations_active",
                 "Active calls where sentiment has escalated.", "gauge")
        lines.append(f"receptionist_sentiment_escalations_active {escalated}")
    except Exception:
        pass

    return "\n".join(lines) + "\n"


@router.get("/metrics", response_class=Response)
def metrics():
    """Prometheus text-exposition /metrics endpoint. No auth (same as
    /health /ready — scrapers don't carry Basic)."""
    return Response(_render_metrics(),
                    media_type="text/plain; version=0.0.4")
