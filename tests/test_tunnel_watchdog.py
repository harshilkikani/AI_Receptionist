"""V8.8 — tunnel watchdog tests.

The watchdog wraps reclaim_tunnel so that a stale trycloudflare URL
(which the cloudflared process can't detect on its own) triggers an
automatic kill + respawn + Twilio-update cycle. These tests cover
the ping classifier, the capturing helper, and the restart trigger
logic without actually spawning cloudflared.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from scripts import tunnel_watchdog


# ── _ping ──────────────────────────────────────────────────────────────

def test_ping_returns_false_on_empty_url():
    assert tunnel_watchdog._ping("") is False
    assert tunnel_watchdog._ping(None) is False


def test_ping_handles_url_error(monkeypatch):
    import urllib.error

    def boom(req, **k):
        raise urllib.error.URLError("dns or socket dead")
    monkeypatch.setattr(tunnel_watchdog.urllib.request, "urlopen", boom)
    assert tunnel_watchdog._ping("https://gone.example") is False


def test_ping_handles_connection_error(monkeypatch):
    def boom(req, **k):
        raise ConnectionError("refused")
    monkeypatch.setattr(tunnel_watchdog.urllib.request, "urlopen", boom)
    assert tunnel_watchdog._ping("https://x") is False


def test_ping_handles_timeout(monkeypatch):
    def boom(req, **k):
        raise TimeoutError("slow")
    monkeypatch.setattr(tunnel_watchdog.urllib.request, "urlopen", boom)
    assert tunnel_watchdog._ping("https://x", timeout=0.5) is False


def test_ping_returns_true_on_200(monkeypatch):
    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(tunnel_watchdog.urllib.request, "urlopen",
                        lambda req, **k: FakeResp())
    assert tunnel_watchdog._ping("https://x") is True


def test_ping_returns_false_on_500(monkeypatch):
    class FakeResp:
        status = 500
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(tunnel_watchdog.urllib.request, "urlopen",
                        lambda req, **k: FakeResp())
    assert tunnel_watchdog._ping("https://x") is False


def test_ping_appends_health_path(monkeypatch):
    captured = {}

    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def cap(req, **k):
        captured["url"] = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        return FakeResp()
    monkeypatch.setattr(tunnel_watchdog.urllib.request, "urlopen", cap)
    tunnel_watchdog._ping("https://x.example")
    assert captured["url"].endswith("/health")


def test_ping_strips_trailing_slash(monkeypatch):
    captured = {}

    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(tunnel_watchdog.urllib.request, "urlopen",
                        lambda req, **k: (captured.setdefault("u", req.get_full_url()),
                                          FakeResp())[1])
    tunnel_watchdog._ping("https://x.example/")
    assert captured["u"] == "https://x.example/health"


# ── _CapturedURL thread safety ─────────────────────────────────────────

def test_captured_url_starts_none():
    c = tunnel_watchdog._CapturedURL()
    assert c.get() is None


def test_captured_url_set_get_roundtrip():
    c = tunnel_watchdog._CapturedURL()
    c.set("https://example")
    assert c.get() == "https://example"


def test_captured_url_concurrent_access():
    """Sanity check on the lock — many threads writing don't corrupt."""
    c = tunnel_watchdog._CapturedURL()
    def writer(i):
        c.set(f"https://url-{i}")
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(40)]
    for t in threads: t.start()
    for t in threads: t.join()
    # Some valid value should be there; doesn't matter which
    assert c.get() is not None
    assert c.get().startswith("https://url-")


# ── _terminate handles all failure modes ──────────────────────────────

def test_terminate_handles_none():
    """Calling terminate(None) must not crash."""
    tunnel_watchdog._terminate(None)   # should be silent


def test_terminate_kills_on_timeout(monkeypatch):
    """When proc.wait() times out after terminate(), proc.kill() fires."""
    import subprocess as _sp
    proc = MagicMock()

    def slow_wait(timeout=None):
        raise _sp.TimeoutExpired(cmd="x", timeout=timeout or 5)
    proc.wait = slow_wait
    proc.kill = MagicMock()
    tunnel_watchdog._terminate(proc)
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


def test_terminate_swallows_terminate_exception():
    proc = MagicMock()
    proc.terminate.side_effect = RuntimeError("already dead")
    tunnel_watchdog._terminate(proc)   # must not raise


# ── Tolerance logic — the core watchdog behavior ──────────────────────

def test_watchdog_uses_existing_reclaim_helpers():
    """Sanity that the import wiring isn't broken — these are the
    functions we re-export from reclaim_tunnel."""
    from scripts import reclaim_tunnel
    assert tunnel_watchdog._spawn_cloudflared is reclaim_tunnel._spawn_cloudflared
    assert tunnel_watchdog.extract_url is reclaim_tunnel.extract_url
    assert tunnel_watchdog.persist_url is reclaim_tunnel.persist_url
    assert tunnel_watchdog.update_twilio_webhooks is reclaim_tunnel.update_twilio_webhooks


def test_main_cli_accepts_documented_flags():
    """Smoke test the argparse layer without actually spawning anything."""
    # Use --tolerance=0 + run() patched out so we exit before the loop
    with patch.object(tunnel_watchdog, "run", return_value=0) as r:
        rc = tunnel_watchdog.main([
            "--port", "9000",
            "--interval", "30",
            "--tolerance", "3",
            "--dry-run",
        ])
    assert rc == 0
    kw = r.call_args.kwargs
    assert kw["local_url"] == "http://localhost:9000"
    assert kw["interval"] == 30.0
    assert kw["tolerance"] == 3
    assert kw["dry_run"] is True
