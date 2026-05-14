"""V8.12 — ElevenLabs quota guard in preflight.

Free tier on ElevenLabs is 10k chars/month. Every prewarm cycle burns
~1.5k chars. Multiple iterations during development silently exhaust
the budget — every subsequent render falls back to Polly. The voice
consistency invariant (V8.1) breaks. The operator only finds out by
calling the number and hearing Polly Joanna.

V8.12 extends preflight.check_elevenlabs_key to surface quota status
loudly:
  - quota exhausted → FAIL (caller is in degraded voice mode)
  - quota ≥ 90% → WARN
  - quota OK → OK with current usage shown
"""
from __future__ import annotations

import json
import io
from unittest.mock import patch, MagicMock

import pytest

from src import preflight


def _mock_subscription_response(monkeypatch, *, character_count, character_limit, tier="free"):
    """Patch the urllib.request.urlopen used inside check_elevenlabs_key
    to return a canned ElevenLabs /v1/user/subscription response."""
    body = json.dumps({
        "character_count": character_count,
        "character_limit": character_limit,
        "tier": tier,
    }).encode("utf-8")

    class FakeResponse:
        status = 200
        def __init__(self, b): self._b = io.BytesIO(b)
        def read(self): return self._b.read()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(
        preflight.urllib.request, "urlopen",
        lambda req, **k: FakeResponse(body))


def test_elevenlabs_quota_ok_under_90_percent(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test1234567890")
    _mock_subscription_response(monkeypatch,
                                  character_count=5000,
                                  character_limit=10000)
    c = preflight.check_elevenlabs_key(ping=True)
    assert c.status == "ok"
    assert "5000/10000" in c.message
    assert "50%" in c.message


def test_elevenlabs_quota_warn_at_90_percent(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test1234567890")
    _mock_subscription_response(monkeypatch,
                                  character_count=9100,
                                  character_limit=10000)
    c = preflight.check_elevenlabs_key(ping=True)
    assert c.status == "warn"
    assert "91%" in c.message or "9100" in c.message


def test_elevenlabs_quota_fail_when_exhausted(monkeypatch):
    """V8.12 — the real-world failure mode that V8.12 surfaces."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test1234567890")
    _mock_subscription_response(monkeypatch,
                                  character_count=10000,
                                  character_limit=10000)
    c = preflight.check_elevenlabs_key(ping=True)
    assert c.status == "fail"
    assert "EXHAUSTED" in c.message or "10000/10000" in c.message
    # Operator-facing remediation guidance
    assert c.detail and ("upgrade" in c.detail.lower()
                          or "rotate" in c.detail.lower()
                          or "reset" in c.detail.lower())


def test_elevenlabs_quota_fail_when_over_limit(monkeypatch):
    """Some plans go over before throttling — still fail."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test1234567890")
    _mock_subscription_response(monkeypatch,
                                  character_count=10500,
                                  character_limit=10000)
    c = preflight.check_elevenlabs_key(ping=True)
    assert c.status == "fail"


def test_elevenlabs_quota_unlimited_tier_passes(monkeypatch):
    """Some tiers report 0/0 (unmetered). Should pass without quota check."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test1234567890")
    _mock_subscription_response(monkeypatch,
                                  character_count=99999,
                                  character_limit=0,
                                  tier="enterprise")
    c = preflight.check_elevenlabs_key(ping=True)
    assert c.status == "ok"


def test_elevenlabs_tier_shown_in_message(monkeypatch):
    """Useful for operator visibility: which plan are we on?"""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test1234567890")
    _mock_subscription_response(monkeypatch,
                                  character_count=5000,
                                  character_limit=10000,
                                  tier="creator")
    c = preflight.check_elevenlabs_key(ping=True)
    assert "creator" in c.message


def test_elevenlabs_no_ping_skips_subscription_fetch(monkeypatch):
    """V8.12 quota check only fires with --ping; the default path
    just verifies key shape so preflight stays fast."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test1234567890abcdef")
    called = []
    monkeypatch.setattr(preflight.urllib.request, "urlopen",
                        lambda req, **k: called.append("hit") or None)
    c = preflight.check_elevenlabs_key(ping=False)
    assert c.status == "ok"
    assert called == []   # no API call
    assert "not pinged" in c.message
