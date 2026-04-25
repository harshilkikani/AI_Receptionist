"""V4.5 — Twilio call recording bookkeeping + admin playback tests."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src import recordings, usage


# ── feature flag ─────────────────────────────────────────────────────

def test_is_enabled_default_false():
    assert recordings.is_enabled(None) is False
    assert recordings.is_enabled({}) is False


def test_is_enabled_explicit_true():
    assert recordings.is_enabled({"record_calls": True}) is True
    assert recordings.is_enabled({"record_calls": "true"}) is True
    assert recordings.is_enabled({"record_calls": "yes"}) is True


def test_is_enabled_explicit_false():
    assert recordings.is_enabled({"record_calls": False}) is False
    assert recordings.is_enabled({"record_calls": "false"}) is False


# ── store + retrieve ────────────────────────────────────────────────

def test_store_and_get_roundtrip():
    usage.start_call("CA_rec_1", "ace_hvac", "+1", "+1")
    usage.end_call("CA_rec_1", outcome="normal")
    recordings.store_recording(
        call_sid="CA_rec_1",
        recording_sid="RE_test_1",
        recording_url="https://api.twilio.com/.../recordings/RE_test_1",
        duration_s=125,
    )
    rec = recordings.get_recording("CA_rec_1")
    assert rec is not None
    assert rec["recording_sid"] == "RE_test_1"
    assert rec["duration_s"] == 125


def test_get_recording_unknown_returns_none():
    assert recordings.get_recording("CA_does_not_exist") is None


def test_store_no_op_on_empty_args():
    recordings.store_recording("", "RE_x", "url", 10)
    recordings.store_recording("CA_x", "", "url", 10)
    # Both should silently no-op


def test_get_recording_empty_sid():
    assert recordings.get_recording("") is None
    assert recordings.get_recording(None) is None


# ── start_recording_via_rest ────────────────────────────────────────

def test_start_recording_via_rest_calls_twilio_update():
    captured = []

    class FakeCalls:
        def __call__(self, sid):
            self.sid = sid
            return self
        def update(self, **kwargs):
            captured.append({"sid": self.sid, **kwargs})

    fake_client = SimpleNamespace(calls=FakeCalls())
    ok = recordings.start_recording_via_rest(
        "CA_rec_2", fake_client, "https://example.com/voice/recording")
    assert ok is True
    assert captured[0]["sid"] == "CA_rec_2"
    assert captured[0]["record"] is True
    assert "voice/recording" in captured[0]["recording_status_callback"]


def test_start_recording_via_rest_handles_failure():
    class FailCalls:
        def __call__(self, sid): return self
        def update(self, **kwargs): raise RuntimeError("api down")
    fake_client = SimpleNamespace(calls=FailCalls())
    ok = recordings.start_recording_via_rest(
        "CA_rec_3", fake_client, "https://example.com/voice/recording")
    assert ok is False


def test_start_recording_via_rest_no_client():
    assert recordings.start_recording_via_rest("CA_x", None, "url") is False


def test_start_recording_via_rest_no_sid():
    fake_client = SimpleNamespace(calls=lambda sid: None)
    assert recordings.start_recording_via_rest("", fake_client, "url") is False


# ── disclosure ───────────────────────────────────────────────────────

def test_disclosure_text():
    assert "recorded" in recordings.disclosure_text().lower()


# ── /voice/recording webhook ────────────────────────────────────────

@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


def test_voice_recording_callback_stores(app_client):
    usage.start_call("CA_cb_1", "ace_hvac", "+14155550142", "+18449403274")
    usage.end_call("CA_cb_1", outcome="normal")
    r = app_client.post("/voice/recording", data={
        "CallSid": "CA_cb_1",
        "RecordingSid": "RE_cb_1",
        "RecordingUrl": "https://api.twilio.com/.../recordings/RE_cb_1",
        "RecordingDuration": "240",
        "RecordingStatus": "completed",
    })
    assert r.status_code == 200
    rec = recordings.get_recording("CA_cb_1")
    assert rec is not None
    assert rec["recording_sid"] == "RE_cb_1"
    assert rec["duration_s"] == 240


def test_voice_recording_callback_skips_non_completed(app_client):
    usage.start_call("CA_cb_2", "ace_hvac", "+1", "+1")
    r = app_client.post("/voice/recording", data={
        "CallSid": "CA_cb_2",
        "RecordingSid": "RE_in_progress",
        "RecordingUrl": "https://...",
        "RecordingDuration": "0",
        "RecordingStatus": "in-progress",
    })
    assert r.status_code == 200
    body = r.json()
    assert body.get("skipped") == "in-progress"
    # No row stored
    assert recordings.get_recording("CA_cb_2") is None


# ── admin call detail surfaces playback ─────────────────────────────

def test_admin_call_detail_shows_audio_player(app_client):
    usage.start_call("CA_adm_1", "ace_hvac", "+14155550142", "+18449403274")
    usage.end_call("CA_adm_1", outcome="normal")
    recordings.store_recording(
        "CA_adm_1", "RE_adm_1",
        "https://api.twilio.com/.../recordings/RE_adm_1", 120)
    r = app_client.get("/admin/call/CA_adm_1")
    assert r.status_code == 200
    assert "<audio" in r.text
    assert "/admin/recording/CA_adm_1.mp3" in r.text
    assert "Audio recording" in r.text


def test_admin_call_detail_no_player_without_recording(app_client):
    usage.start_call("CA_adm_2", "ace_hvac", "+14155550142", "+18449403274")
    usage.end_call("CA_adm_2", outcome="normal")
    r = app_client.get("/admin/call/CA_adm_2")
    assert r.status_code == 200
    assert "<audio" not in r.text


# ── /admin/recording proxy ──────────────────────────────────────────

def test_admin_recording_404_unknown(app_client):
    r = app_client.get("/admin/recording/CA_nope.mp3")
    assert r.status_code == 404


def test_admin_recording_503_no_creds(app_client, monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    usage.start_call("CA_proxy_1", "ace_hvac", "+1", "+1")
    usage.end_call("CA_proxy_1", outcome="normal")
    recordings.store_recording(
        "CA_proxy_1", "RE_proxy_1",
        "https://api.twilio.com/.../recordings/RE_proxy_1", 60)
    r = app_client.get("/admin/recording/CA_proxy_1.mp3")
    assert r.status_code == 503


def test_admin_recording_streams_when_creds_present(app_client, monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    usage.start_call("CA_proxy_2", "ace_hvac", "+1", "+1")
    usage.end_call("CA_proxy_2", outcome="normal")
    recordings.store_recording(
        "CA_proxy_2", "RE_proxy_2",
        "https://api.twilio.com/.../recordings/RE_proxy_2", 60)

    # Mock urlopen to return fake bytes
    class FakeResponse:
        def __init__(self):
            self._iter = iter([b"fakempb1", b"fakempb2"])
            self.status = 200
            self.headers = {}
        def __iter__(self): return self._iter
        def __next__(self): return next(self._iter)
        def read(self, n=-1): return b"".join(self._iter)
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): self.close()

    def fake_urlopen(req, timeout=None):
        # Verify Basic auth header was set
        auth = req.headers.get("Authorization", "")
        assert auth.startswith("Basic ")
        return FakeResponse()

    monkeypatch.setattr("src.recordings.urllib.request.urlopen", fake_urlopen)
    r = app_client.get("/admin/recording/CA_proxy_2.mp3")
    # Status 200 with the right content type
    assert r.status_code == 200
    assert r.headers.get("content-type") == "audio/mpeg"
