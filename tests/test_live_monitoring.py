"""V3.14 — live call monitoring page tests."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import call_timer, transcripts


@pytest.fixture(autouse=True)
def _reset_call_timer():
    # Clear any existing state before each test
    with call_timer._state_lock:
        call_timer._calls.clear()
    yield
    with call_timer._state_lock:
        call_timer._calls.clear()


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


def test_live_page_empty_state(app_client):
    r = app_client.get("/admin/live")
    assert r.status_code == 200
    assert "Nothing live" in r.text or "No calls in flight" in r.text


def test_live_page_lists_active_calls(app_client):
    call_timer.record_start("CA_live_1", "ace_hvac")
    r = app_client.get("/admin/live")
    assert r.status_code == 200
    assert "CA_live_1"[:12] in r.text
    assert "ace_hvac" in r.text


def test_live_page_includes_meta_refresh(app_client):
    r = app_client.get("/admin/live")
    assert 'http-equiv="refresh"' in r.text
    assert 'content="3"' in r.text


def test_live_page_emergency_badge(app_client):
    call_timer.record_start("CA_live_em", "ace_hvac")
    call_timer.mark_emergency("CA_live_em")
    r = app_client.get("/admin/live")
    assert "🚨" in r.text


def test_live_page_shows_latest_transcript_line(app_client):
    call_timer.record_start("CA_live_tr", "ace_hvac")
    transcripts.record_turn("CA_live_tr", "ace_hvac", "user",
                             "my water heater just burst")
    r = app_client.get("/admin/live")
    # The latest caller line should appear truncated into the row
    assert "water heater" in r.text


def test_live_page_links_to_call_detail(app_client):
    call_timer.record_start("CA_link_1", "ace_hvac")
    r = app_client.get("/admin/live")
    assert "/admin/call/CA_link_1?live=1" in r.text
