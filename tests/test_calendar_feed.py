"""V4.6 — per-tenant ICS calendar feed tests."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import bookings, calendar_feed, client_portal


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


# ── generate_feed_ics ────────────────────────────────────────────────

def test_generate_feed_empty():
    out = bookings.generate_feed_ics([], tenant_name="Test Co")
    assert "BEGIN:VCALENDAR" in out
    assert "END:VCALENDAR" in out
    assert "X-WR-CALNAME:Test Co bookings" in out
    # No events
    assert "BEGIN:VEVENT" not in out


def test_generate_feed_one_event():
    bookings_list = [{
        "id": "bk_test_1", "caller_phone": "+15551234567",
        "caller_name": "Sarah", "address": "42 Oak St",
        "service": "Pump-out", "requested_when": "Tuesday morning",
        "notes": "prefers AM", "status": "pending",
        "created_ts": 1745000000,
    }]
    out = bookings.generate_feed_ics(bookings_list, tenant_name="Test Co")
    assert "BEGIN:VEVENT" in out
    assert "END:VEVENT" in out
    assert "bk_test_1@ai-receptionist" in out
    assert "Pump-out" in out
    assert "Sarah" in out
    assert "42 Oak St" in out


def test_generate_feed_multiple_events():
    bookings_list = [
        {"id": "bk_1", "service": "Pump-out", "created_ts": 1745000000},
        {"id": "bk_2", "service": "Inspection", "created_ts": 1745100000},
    ]
    out = bookings.generate_feed_ics(bookings_list)
    assert out.count("BEGIN:VEVENT") == 2
    assert out.count("END:VEVENT") == 2


def test_generate_feed_anchors_to_iso_date():
    """When requested_when starts with YYYY-MM-DD we use that as the day."""
    bk = [{"id": "bk_x", "service": "X",
           "requested_when": "2026-05-12 morning",
           "created_ts": 1700000000}]
    out = bookings.generate_feed_ics(bk)
    assert "DTSTART:20260512T100000Z" in out


def test_generate_feed_escapes_special_chars():
    """Commas, semicolons, newlines must be escaped per RFC 5545."""
    bk = [{"id": "bk_esc", "service": "X",
           "address": "42 Main, Apt 1",
           "notes": "Has;dog\nand cat",
           "created_ts": 1700000000}]
    out = bookings.generate_feed_ics(bk)
    # Comma in description should be escaped
    assert "42 Main\\, Apt 1" in out
    # Semicolon escaped
    assert "Has\\;dog" in out


def test_generate_feed_default_summary_when_no_service():
    bk = [{"id": "bk_x", "created_ts": 1700000000}]
    out = bookings.generate_feed_ics(bk)
    assert "Service appointment" in out


# ── feed_url ──────────────────────────────────────────────────────────

def test_feed_url_contains_token(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    url = calendar_feed.feed_url("ace_hvac")
    assert url.startswith("https://example.com/calendar/ace_hvac.ics?t=")


# ── /calendar/{id}.ics endpoint ──────────────────────────────────────

def test_feed_endpoint_returns_ics(app_client):
    bookings.record_booking(
        client_id="ace_hvac", caller_phone="+1",
        caller_name="X", address="Y", service="tune-up")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/calendar/ace_hvac.ics?t={tok}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in r.text


def test_feed_endpoint_403_bad_token(app_client):
    r = app_client.get("/calendar/ace_hvac.ics?t=bogus")
    assert r.status_code == 403


def test_feed_endpoint_403_unknown_client(app_client):
    tok = client_portal.issue_token("nobody")
    r = app_client.get(f"/calendar/nobody.ics?t={tok}")
    assert r.status_code == 403


def test_feed_endpoint_403_reserved_client(app_client):
    tok = client_portal.issue_token("_default")
    r = app_client.get(f"/calendar/_default.ics?t={tok}")
    assert r.status_code == 403


def test_feed_endpoint_no_token(app_client):
    r = app_client.get("/calendar/ace_hvac.ics")
    assert r.status_code == 403


def test_feed_endpoint_includes_calendar_name(app_client):
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/calendar/ace_hvac.ics?t={tok}")
    assert "Ace HVAC" in r.text


# ── CLI ──────────────────────────────────────────────────────────────

def test_cli_url_unknown_client(capsys):
    rc = calendar_feed._cli(["url", "nope"])
    assert rc == 2


def test_cli_url_no_secret(monkeypatch, capsys):
    monkeypatch.delenv("CLIENT_PORTAL_SECRET", raising=False)
    rc = calendar_feed._cli(["url", "ace_hvac"])
    assert rc == 2


def test_cli_url_happy(monkeypatch, capsys):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "s")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    rc = calendar_feed._cli(["url", "ace_hvac"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "https://example.com/calendar/ace_hvac.ics?t=" in out
