"""App-wide security middleware.

Two jobs:
  1. Token-bucket rate limiter for /admin/* — 60 req/min per client IP,
     429 on exceed. In-memory (process-local). Defaults are conservative
     so a page refresh + nav never trips. For multi-instance, swap the
     dict for Redis.
  2. Security headers on every response:
       X-Content-Type-Options: nosniff
       Referrer-Policy: no-referrer

Both are wired as Starlette middlewares in main.py. Tests in
tests/test_security.py.

Env knobs (rarely needed):
  ADMIN_RATE_LIMIT_PER_MIN   default 60
  ADMIN_RATE_LIMIT_PATHS     comma list of path prefixes; default "/admin"
"""
from __future__ import annotations

import os
import time
import threading
from typing import Callable, Awaitable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse


_bucket_lock = threading.Lock()
_buckets: dict = {}  # ip -> {tokens: float, last: float}

# V5.1 — bound to prevent year-of-distinct-IPs memory growth. On
# overflow, evict the LEAST-recently-used IP (oldest .last).
MAX_BUCKETS = 10_000


def _evict_lru_if_full():
    """Caller must hold _bucket_lock."""
    if len(_buckets) <= MAX_BUCKETS:
        return
    oldest_ip = min(_buckets.items(), key=lambda kv: kv[1].get("last", 0))[0]
    _buckets.pop(oldest_ip, None)


def _default_rate() -> int:
    try:
        return max(1, int(os.environ.get("ADMIN_RATE_LIMIT_PER_MIN", "60")))
    except ValueError:
        return 60


def _protected_prefixes() -> tuple:
    raw = os.environ.get("ADMIN_RATE_LIMIT_PATHS", "/admin")
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _client_ip(request: Request) -> str:
    # Trust X-Forwarded-For when set (cloudflared tunnel passes it)
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _take_token(ip: str, rate_per_min: int) -> bool:
    """Classic token bucket. Capacity = rate_per_min, refill = rate/60 per sec."""
    now = time.monotonic()
    capacity = float(rate_per_min)
    refill = capacity / 60.0
    with _bucket_lock:
        b = _buckets.get(ip)
        if b is None:
            _buckets[ip] = {"tokens": capacity - 1.0, "last": now}
            _evict_lru_if_full()
            return True
        elapsed = now - b["last"]
        b["tokens"] = min(capacity, b["tokens"] + elapsed * refill)
        b["last"] = now
        if b["tokens"] >= 1.0:
            b["tokens"] -= 1.0
            return True
        return False


def reset_buckets():
    """Test hook — clears in-memory limiter state."""
    with _bucket_lock:
        _buckets.clear()


class AdminRateLimitMiddleware(BaseHTTPMiddleware):
    """Token-bucket rate limit for admin paths. 429 on exceed."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path or ""
        if not path.startswith(_protected_prefixes()):
            return await call_next(request)
        ip = _client_ip(request)
        if not _take_token(ip, _default_rate()):
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited",
                         "detail": "admin rate limit exceeded (60 req/min). "
                                   "Slow down and retry."},
                headers={"Retry-After": "60"},
            )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds minimal hardening headers to every response."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response
