"""V8.8 — tunnel watchdog.

The trycloudflare quick-tunnel URLs we get from `cloudflared tunnel
--url …` have no uptime SLA. The cloudflared process can stay alive
indefinitely while the URL silently stops routing. That's what
happened twice in v6/v8 — caller hears "application error" because
Twilio's webhook points at a dead URL even though everything on our
end looks healthy from `ps`.

This script wraps reclaim_tunnel.py with a health-check loop:

  1. Spawn cloudflared + capture URL + update Twilio  (reuses
     reclaim_tunnel.py's machinery)
  2. Every 60s, GET {url}/health.
  3. On 2 consecutive failures (avoid flapping on transient blips):
       a. Kill cloudflared
       b. Re-spawn it
       c. Capture new URL + push to Twilio
  4. Loop forever.

Use this INSTEAD of `python scripts/reclaim_tunnel.py` when you want
the demo to stay up unattended.

Usage:
    python scripts/tunnel_watchdog.py
    python scripts/tunnel_watchdog.py --port 9000
    python scripts/tunnel_watchdog.py --interval 30      # ping every 30s
    python scripts/tunnel_watchdog.py --tolerance 3      # 3 consecutive fails before restart
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

from scripts.reclaim_tunnel import (
    _spawn_cloudflared,
    extract_url,
    persist_url,
    update_twilio_webhooks,
    _DEFAULT_EXE,
)

_DEFAULT_LOCAL_URL = "http://localhost:8765"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("tunnel_watchdog")


def _ping(url: str, *, timeout: float = 5.0) -> bool:
    """Returns True if {url}/health is 200, else False (any failure
    counts as a miss)."""
    if not url:
        return False
    try:
        req = urllib.request.Request(url.rstrip("/") + "/health",
                                      method="GET",
                                      headers={"User-Agent": "tunnel-watchdog"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, ConnectionError, OSError):
        return False
    except Exception as e:
        log.warning("watchdog ping unexpected error: %s", e)
        return False


class _CapturedURL:
    """Thread-safe holder for the current tunnel URL. The reader
    thread pulls cloudflared's stderr; the main thread polls."""

    def __init__(self):
        self.url: Optional[str] = None
        self._lock = threading.Lock()

    def set(self, url: str) -> None:
        with self._lock:
            self.url = url

    def get(self) -> Optional[str]:
        with self._lock:
            return self.url


def _start_tunnel(exe: str, local_url: str,
                  captured: _CapturedURL,
                  *, dry_run: bool = False) -> subprocess.Popen:
    """Spawn cloudflared + start a reader thread that captures the
    URL from stderr + updates Twilio when the URL changes. Returns
    the Popen handle so the caller can terminate later."""
    proc = _spawn_cloudflared(exe, local_url)

    def _reader():
        assert proc.stdout is not None
        current: Optional[str] = None
        for raw in iter(proc.stdout.readline, ""):
            line = raw.rstrip()
            if line:
                print(line, flush=True)
            url = extract_url(line)
            if url and url != current:
                current = url
                persist_url(url)
                captured.set(url)
                log.info("watchdog: tunnel URL captured: %s", url)
                if not dry_run:
                    try:
                        r = update_twilio_webhooks(url)
                        log.info("watchdog: twilio webhook update: %s", r)
                    except Exception as e:
                        log.error("watchdog: twilio update failed: %s", e)
    t = threading.Thread(target=_reader, daemon=True, name="cloudflared-reader")
    t.start()
    return proc


def _terminate(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass


def run(*, exe: str = _DEFAULT_EXE,
        local_url: str = _DEFAULT_LOCAL_URL,
        interval: float = 60.0,
        tolerance: int = 2,
        dry_run: bool = False) -> int:
    """Main loop. Returns the last cloudflared exit code on Ctrl-C."""
    captured = _CapturedURL()
    proc = _start_tunnel(exe, local_url, captured, dry_run=dry_run)

    # Wait briefly for the first URL to land
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline and captured.get() is None:
        time.sleep(1)
    if captured.get() is None:
        log.error("watchdog: cloudflared didn't produce a URL in 30s — giving up")
        _terminate(proc)
        return 1

    log.info("watchdog: monitoring %s every %ds (tolerance=%d)",
             captured.get(), interval, tolerance)

    consecutive_fails = 0
    try:
        while True:
            time.sleep(interval)
            url = captured.get()
            ok = _ping(url) if url else False

            if ok:
                if consecutive_fails:
                    log.info("watchdog: tunnel healthy again")
                consecutive_fails = 0
                continue

            consecutive_fails += 1
            log.warning("watchdog: health check failed (%d/%d) url=%s",
                        consecutive_fails, tolerance, url)
            if consecutive_fails < tolerance:
                continue

            # Restart cycle
            log.error("watchdog: tunnel dead after %d consecutive misses — "
                      "restarting cloudflared", tolerance)
            _terminate(proc)
            captured.set("")
            proc = _start_tunnel(exe, local_url, captured, dry_run=dry_run)
            # Wait for new URL
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline and not captured.get():
                time.sleep(1)
            if captured.get():
                log.info("watchdog: restart complete, new URL = %s",
                         captured.get())
                consecutive_fails = 0
            else:
                log.error("watchdog: restart failed — no URL in 30s. Will "
                          "keep trying on next interval.")
    except KeyboardInterrupt:
        log.info("watchdog: Ctrl-C received — terminating cloudflared")
        _terminate(proc)
        return 130


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python scripts/tunnel_watchdog.py",
                                description=__doc__)
    p.add_argument("--exe", default=_DEFAULT_EXE,
                   help="cloudflared executable (default: ./cloudflared.exe)")
    p.add_argument("--port", type=int, default=8765,
                   help="local server port (default 8765)")
    p.add_argument("--interval", type=float, default=60.0,
                   help="seconds between health checks (default 60)")
    p.add_argument("--tolerance", type=int, default=2,
                   help="consecutive health-check failures before "
                        "restart (default 2)")
    p.add_argument("--dry-run", action="store_true",
                   help="don't actually call Twilio API on URL change")
    args = p.parse_args(argv)
    local_url = f"http://localhost:{args.port}"
    return run(exe=args.exe, local_url=local_url,
               interval=args.interval, tolerance=args.tolerance,
               dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
