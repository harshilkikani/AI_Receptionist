"""V9.0 — public contact form endpoint.

The landing page replaces mailto with a real /contact form. We need to
make sure:
  - Valid submissions land as a JSONL row.
  - Required fields are enforced.
  - Field-length caps prevent payload bombs.
  - The endpoint records minimal request metadata (IP, UA).
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    """Boot the app with isolated data dir so each test gets a clean
    contact_leads.jsonl."""
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    # Route lead writes to a tmp file
    monkeypatch.setattr(main, "_CONTACT_LEADS_PATH",
                         tmp_path / "data" / "contact_leads.jsonl")
    return TestClient(main.app), main


def test_contact_minimal_valid_payload(app_client):
    client, mod = app_client
    r = client.post("/contact", json={
        "name": "Mike Reilly",
        "business": "Reilly Plumbing",
        "phone": "(555) 123-4567",
    })
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # Lead appended as JSONL
    p = mod._CONTACT_LEADS_PATH
    assert p.exists()
    line = p.read_text(encoding="utf-8").strip()
    rec = json.loads(line)
    assert rec["name"] == "Mike Reilly"
    assert rec["business"] == "Reilly Plumbing"
    assert rec["phone"] == "(555) 123-4567"
    assert rec["email"] is None
    assert rec["note"] is None
    assert isinstance(rec["ts"], int)
    # IP + UA captured
    assert "ua" in rec
    assert "ip" in rec


def test_contact_full_payload(app_client):
    client, mod = app_client
    r = client.post("/contact", json={
        "name": "Mike",
        "business": "Ace HVAC",
        "phone": "555-0100",
        "email": "mike@example.com",
        "note": "We miss 15-20 calls a week after 5pm.",
    })
    assert r.status_code == 200
    rec = json.loads(mod._CONTACT_LEADS_PATH.read_text(encoding="utf-8").strip())
    assert rec["email"] == "mike@example.com"
    assert "15-20" in rec["note"]


def test_contact_appends_not_overwrites(app_client):
    client, mod = app_client
    for n in range(3):
        r = client.post("/contact", json={
            "name": f"Person {n}", "business": "B", "phone": "555",
        })
        assert r.status_code == 200
    lines = mod._CONTACT_LEADS_PATH.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3


def test_contact_rejects_missing_name(app_client):
    client, _ = app_client
    r = client.post("/contact", json={"name": "", "business": "B", "phone": "5"})
    assert r.status_code == 400


def test_contact_rejects_missing_business(app_client):
    client, _ = app_client
    r = client.post("/contact", json={"name": "n", "business": "", "phone": "5"})
    assert r.status_code == 400


def test_contact_rejects_missing_phone(app_client):
    client, _ = app_client
    r = client.post("/contact", json={"name": "n", "business": "b", "phone": ""})
    assert r.status_code == 400


def test_contact_strips_whitespace_for_validation(app_client):
    client, _ = app_client
    # Whitespace-only fields are treated as missing
    r = client.post("/contact", json={
        "name": "   ", "business": "b", "phone": "5",
    })
    assert r.status_code == 400


def test_contact_field_length_caps(app_client):
    client, _ = app_client
    r = client.post("/contact", json={
        "name": "x" * 81,           # exceeds 80
        "business": "b",
        "phone": "5",
    })
    assert r.status_code == 400
    assert "too long" in r.json()["detail"].lower()


def test_contact_note_length_cap(app_client):
    client, _ = app_client
    r = client.post("/contact", json={
        "name": "n", "business": "b", "phone": "5",
        "note": "x" * 601,
    })
    assert r.status_code == 400


def test_contact_returns_503_on_disk_failure(app_client, monkeypatch):
    """If the leads file can't be written (read-only volume, permissions),
    surface 503 so the form's retry hint shows."""
    client, mod = app_client
    def boom(*a, **k):
        raise OSError("read-only fs")
    monkeypatch.setattr(Path, "mkdir", boom)
    r = client.post("/contact", json={
        "name": "n", "business": "b", "phone": "5",
    })
    assert r.status_code == 503


def test_contact_ts_is_unix_seconds(app_client):
    """Operators reading the JSONL need to be able to interpret ts."""
    import time
    client, mod = app_client
    before = int(time.time())
    r = client.post("/contact", json={
        "name": "n", "business": "b", "phone": "5",
    })
    after = int(time.time())
    assert r.status_code == 200
    rec = json.loads(mod._CONTACT_LEADS_PATH.read_text(encoding="utf-8").strip())
    assert before <= rec["ts"] <= after


def test_contact_endpoint_still_reachable_after_v95(app_client):
    """V9.5 — the public page at / no longer hosts a marketing contact
    form (the brief said 'no marketing, just a demo'). The /contact
    endpoint stays as a dormant API for any external marketing site
    that wants to POST to it. Verify the endpoint contract is intact
    even with no caller in the codebase."""
    client, _ = app_client
    r = client.post("/contact", json={
        "name": "API user",
        "business": "External site",
        "phone": "555-0123",
    })
    assert r.status_code == 200
    assert r.json() == {"ok": True}
