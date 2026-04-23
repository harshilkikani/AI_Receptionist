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
