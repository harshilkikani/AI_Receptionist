"""V6.4 — lifespan hardening tests.

The lifespan is the app's only chance to bring background loops up,
run migrations, pre-warm caches. If any of those crashes, the FastAPI
process used to die — but voice webhooks worked just fine without
them. v6.4 wraps every step so the demo answers the phone even when
peripheral systems are broken. These tests inject failure into each
startup step and assert the app still serves /health.
"""
from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def isolated_main(monkeypatch, tmp_path):
    """Re-import main fresh for each test (lifespan only runs on
    application boot)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    # Force a fresh reload each call so lifespan re-runs
    import main
    importlib.reload(main)
    return main


def _boot_and_probe(main_mod):
    """Boot the app via TestClient (which runs lifespan) and confirm
    /health responds 200."""
    with TestClient(main_mod.app) as c:
        r = c.get("/health")
        assert r.status_code == 200, (
            f"/health returned {r.status_code}: {r.text[:200]}")
        return r


# ── Each startup step can crash independently ──────────────────────────

def test_app_boots_when_migrations_crash(monkeypatch, isolated_main):
    """migrations.run_all blowing up must NOT prevent answering calls."""
    from src import migrations
    monkeypatch.setattr(migrations, "run_all",
                        lambda: (_ for _ in ()).throw(
                            RuntimeError("schema corrupt")))
    _boot_and_probe(isolated_main)


def test_app_boots_when_demo_purge_crashes(monkeypatch, isolated_main):
    from src import onboarding
    monkeypatch.setattr(onboarding, "purge_expired_demos",
                        lambda: (_ for _ in ()).throw(
                            OSError("disk full")))
    _boot_and_probe(isolated_main)


def test_app_boots_when_audio_cache_crashes(monkeypatch, isolated_main):
    from src import audio_cache
    monkeypatch.setattr(audio_cache, "evict_if_needed",
                        lambda **k: (_ for _ in ()).throw(
                            PermissionError("perm denied")))
    monkeypatch.setattr(audio_cache, "prewarm_all",
                        lambda: (_ for _ in ()).throw(
                            PermissionError("perm denied")))
    _boot_and_probe(isolated_main)


def test_app_boots_when_alerts_loop_crashes(monkeypatch, isolated_main):
    from src import alerts
    monkeypatch.setattr(alerts, "start_background_loop",
                        lambda: (_ for _ in ()).throw(
                            RuntimeError("alerts broken")))
    _boot_and_probe(isolated_main)


def test_app_boots_when_scheduler_crashes(monkeypatch, isolated_main):
    from src import scheduler
    monkeypatch.setattr(scheduler, "start",
                        lambda: (_ for _ in ()).throw(
                            RuntimeError("scheduler broken")))
    _boot_and_probe(isolated_main)


# ── Several failures in combination ────────────────────────────────────

def test_app_boots_when_everything_fails(monkeypatch, isolated_main):
    """Worst case: every optional startup step crashes simultaneously.
    Voice path MUST still work."""
    from src import migrations, onboarding, audio_cache, scheduler
    from src import alerts
    monkeypatch.setattr(migrations, "run_all",
                        lambda: (_ for _ in ()).throw(Exception("m")))
    monkeypatch.setattr(onboarding, "purge_expired_demos",
                        lambda: (_ for _ in ()).throw(Exception("o")))
    monkeypatch.setattr(audio_cache, "evict_if_needed",
                        lambda **k: (_ for _ in ()).throw(Exception("a")))
    monkeypatch.setattr(audio_cache, "prewarm_all",
                        lambda: (_ for _ in ()).throw(Exception("p")))
    monkeypatch.setattr(alerts, "start_background_loop",
                        lambda: (_ for _ in ()).throw(Exception("al")))
    monkeypatch.setattr(scheduler, "start",
                        lambda: (_ for _ in ()).throw(Exception("s")))
    _boot_and_probe(isolated_main)


# ── Shutdown also tolerates failures ───────────────────────────────────

def test_shutdown_tolerates_alerts_crash(monkeypatch, isolated_main):
    """The TestClient context exit triggers lifespan shutdown — that
    path must also be exception-safe."""
    from src import alerts
    from src import scheduler
    monkeypatch.setattr(alerts, "stop_background_loop",
                        lambda: (_ for _ in ()).throw(Exception("stop")))
    monkeypatch.setattr(scheduler, "stop",
                        lambda: (_ for _ in ()).throw(Exception("stop")))
    # Just entering + exiting the TestClient context is the test
    with TestClient(isolated_main.app) as c:
        r = c.get("/health")
        assert r.status_code == 200


# ── Voice path still works under broken background services ───────────

def test_voice_incoming_works_with_broken_background(monkeypatch, isolated_main):
    """The whole point — phone path keeps answering even if everything
    else is broken."""
    from src import migrations, onboarding, audio_cache, scheduler
    from src import alerts
    for mod, attr in [
        (migrations, "run_all"),
        (onboarding, "purge_expired_demos"),
        (audio_cache, "evict_if_needed"),
        (audio_cache, "prewarm_all"),
        (alerts, "start_background_loop"),
        (scheduler, "start"),
    ]:
        monkeypatch.setattr(mod, attr,
                            lambda *a, **k: (_ for _ in ()).throw(Exception("broken")))

    with TestClient(isolated_main.app) as c:
        r = c.post("/voice/incoming", data={
            "From": "+14155550199", "To": "+18449403274",
            "CallSid": "CA_lifespan_001",
        })
        # 200 with TwiML — voice path works regardless of background fail
        assert r.status_code == 200
        assert "<Response>" in r.text
