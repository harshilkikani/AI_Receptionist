"""P9 — tunnel URL reclaim + Twilio webhook patch tests."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Make scripts/ importable
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

import reclaim_tunnel  # type: ignore


# ── URL extraction ─────────────────────────────────────────────────────

def test_extract_url_success():
    line = "2026-04-21T10:00:00Z INF | https://foo-bar-baz-qux.trycloudflare.com"
    assert reclaim_tunnel.extract_url(line) == "https://foo-bar-baz-qux.trycloudflare.com"


def test_extract_url_none_when_absent():
    assert reclaim_tunnel.extract_url("no urls in this line") is None
    assert reclaim_tunnel.extract_url("") is None


def test_extract_url_ignores_non_trycloudflare():
    assert reclaim_tunnel.extract_url("https://example.com") is None
    assert reclaim_tunnel.extract_url("https://foo.trycloudflare.net") is None


# ── persist_url ────────────────────────────────────────────────────────

def test_persist_url_writes_file(tmp_path):
    dst = tmp_path / "subdir" / "tunnel_url.txt"
    reclaim_tunnel.persist_url("https://abc.trycloudflare.com", dst)
    assert dst.read_text(encoding="utf-8") == "https://abc.trycloudflare.com"


# ── update_twilio_webhooks ─────────────────────────────────────────────

def _fake_twilio_with_numbers(numbers: list, raise_on_update: bool = False):
    """Create a fake twilio client whose list() returns each string as a
    SimpleNamespace with phone_number + an update() that records calls."""
    updates = []

    def make_num(pn):
        ns = SimpleNamespace(phone_number=pn)
        def _update(**kwargs):
            if raise_on_update:
                raise RuntimeError("twilio 500")
            updates.append((pn, kwargs))
        ns.update = _update
        return ns

    tw = SimpleNamespace(
        incoming_phone_numbers=SimpleNamespace(
            list=lambda limit=200: [make_num(p) for p in numbers],
        ),
    )
    return tw, updates


def test_update_twilio_targets_managed_numbers_only():
    tw, updates = _fake_twilio_with_numbers(
        ["+18449403274", "+19991110000"]
    )
    r = reclaim_tunnel.update_twilio_webhooks(
        "https://abc.trycloudflare.com",
        tw_client=tw,
        target_numbers={"+18449403274"},  # ace_hvac only
    )
    assert r["updated"] == ["+18449403274"]
    assert "+19991110000" in r["skipped"]
    # URL was written correctly
    assert updates[0][1]["voice_url"] == "https://abc.trycloudflare.com/voice/incoming"
    assert updates[0][1]["sms_url"] == "https://abc.trycloudflare.com/sms/incoming"


def test_update_twilio_without_credentials_skips(monkeypatch):
    """No Twilio creds in env → no-op skip."""
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    r = reclaim_tunnel.update_twilio_webhooks(
        "https://x.trycloudflare.com",
        tw_client=None,  # explicit None → falls back to env-derived
        target_numbers={"+18449403274"},
    )
    assert "twilio_client_unavailable" in r["skipped"]


def test_update_twilio_handles_list_error():
    tw = SimpleNamespace(
        incoming_phone_numbers=SimpleNamespace(
            list=lambda limit=200: (_ for _ in ()).throw(RuntimeError("boom")),
        ),
    )
    r = reclaim_tunnel.update_twilio_webhooks(
        "https://x.trycloudflare.com",
        tw_client=tw,
        target_numbers={"+18449403274"},
    )
    assert any("list_failed" in e for e in r["errors"])


def test_update_twilio_handles_per_number_error():
    tw, _ = _fake_twilio_with_numbers(["+18449403274"], raise_on_update=True)
    r = reclaim_tunnel.update_twilio_webhooks(
        "https://x.trycloudflare.com",
        tw_client=tw,
        target_numbers={"+18449403274"},
    )
    assert r["updated"] == []
    assert any("+18449403274" in e for e in r["errors"])


def test_update_twilio_no_target_numbers():
    tw, _ = _fake_twilio_with_numbers(["+19991110000"])
    r = reclaim_tunnel.update_twilio_webhooks(
        "https://x.trycloudflare.com",
        tw_client=tw,
        target_numbers=set(),
    )
    assert "no_tenant_numbers" in r["skipped"]


# ── tenant_numbers ─────────────────────────────────────────────────────

def test_tenant_numbers_excludes_reserved(monkeypatch):
    # ace_hvac has +18449403274; _default, _template, example_client have empty
    nums = reclaim_tunnel._tenant_numbers()
    assert "+18449403274" in nums
    # Reserved and empty-inbound tenants are excluded
    assert not any(n == "" for n in nums)


# ── watch_and_update (with a fake subprocess) ─────────────────────────

def test_watch_captures_url_and_invokes_callback(tmp_path, monkeypatch):
    """Simulate cloudflared output; verify URL capture + persistence."""
    monkeypatch.setattr(reclaim_tunnel, "_TUNNEL_HINT",
                        tmp_path / "tunnel_url.txt")
    # Fake Popen
    lines = iter([
        "INF starting cloudflared\n",
        "INF | https://foo.trycloudflare.com\n",
        "INF ready\n",
        "",  # EOF
    ])

    class FakeStdout:
        def readline(self):
            try:
                return next(lines)
            except StopIteration:
                return ""

    fake_proc = SimpleNamespace(
        stdout=FakeStdout(),
        wait=lambda: 0,
        terminate=lambda: None,
    )
    captured = []
    rc = reclaim_tunnel.watch_and_update(
        fake_proc, dry_run=True,
        on_url=lambda u: captured.append(u),
    )
    assert captured == ["https://foo.trycloudflare.com"]
    assert rc == 0
    # Persisted
    assert (tmp_path / "tunnel_url.txt").read_text() == \
           "https://foo.trycloudflare.com"
