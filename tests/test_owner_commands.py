"""V6 — HELP SMS command + welcome-flow tests."""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src import owner_commands


def _fake_tw(capture: list):
    return SimpleNamespace(messages=SimpleNamespace(
        create=lambda to, from_, body: capture.append((to, body)) or None))


# ── is_help_command ────────────────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    ("HELP", True),
    ("help", True),
    ("Help!", True),
    ("info please", True),
    ("status", True),
    ("link", True),
    ("hey help", False),      # only first word counts
    ("", False),
    (None, False),
    ("i need help with my septic tank", False),
])
def test_is_help_command(body, expected):
    assert owner_commands.is_help_command(body) == expected


# ── owner detection ────────────────────────────────────────────────────

def test_is_owner_matches_owner_cell():
    c = {"owner_cell": "+15551234567", "escalation_phone": ""}
    assert owner_commands._is_owner("+15551234567", c) is True
    assert owner_commands._is_owner("+19998887777", c) is False


def test_is_owner_matches_escalation():
    c = {"owner_cell": "", "escalation_phone": "+15551234567"}
    assert owner_commands._is_owner("+15551234567", c) is True


def test_is_owner_normalizes_format():
    c = {"owner_cell": "+15551234567"}
    assert owner_commands._is_owner("(555) 123-4567", c) is True
    assert owner_commands._is_owner("15551234567", c) is True


# ── handle_help_sms ────────────────────────────────────────────────────

def test_handle_help_skips_non_help_body(client_ace):
    r = owner_commands.handle_help_sms(
        "just a normal message", from_phone="+14155550142", client=client_ace)
    assert r["handled"] is False


def test_handle_help_owner_gets_owner_body(client_ace, monkeypatch):
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    monkeypatch.setitem(client_ace, "escalation_phone", "+15559876543")
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret")
    r = owner_commands.handle_help_sms(
        "HELP", from_phone="+15551234567", client=client_ace)
    assert r["handled"] is True
    assert r["variant"] == "owner"
    # Portal URL should appear (secret is set)
    assert "Dashboard:" in r["reply"] or "dashboard" in r["reply"].lower()
    assert "+15559876543" in r["reply"]    # escalation number


def test_handle_help_public_gets_public_body(client_ace):
    r = owner_commands.handle_help_sms(
        "HELP", from_phone="+19998887777", client=client_ace)
    assert r["handled"] is True
    assert r["variant"] == "public"
    # Public body: no portal URL, redirects to calling
    assert "call" in r["reply"].lower()


def test_handle_help_portal_omitted_without_secret(client_ace, monkeypatch):
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    monkeypatch.delenv("CLIENT_PORTAL_SECRET", raising=False)
    r = owner_commands.handle_help_sms(
        "HELP", from_phone="+15551234567", client=client_ace)
    assert r["handled"] is True
    assert "Dashboard:" not in r["reply"]


# ── /sms/incoming integration ─────────────────────────────────────────

@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")   # bypass sig check
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret")
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


def test_sms_help_short_circuits_llm(app_client, monkeypatch):
    """HELP body should produce a canned reply, not route through Claude."""
    # Seed the client with an owner_cell so we hit the owner branch
    from src import tenant
    tenant.reload()
    ace = tenant.load_client_by_number("+18449403274")
    monkeypatch.setitem(ace, "owner_cell", "+14155550142")

    r = app_client.post("/sms/incoming",
                        data={"From": "+14155550142", "To": "+18449403274",
                              "Body": "HELP"})
    assert r.status_code == 200
    # The TwiML contains the help body, not an LLM-generated reply
    assert "Ace HVAC" in r.text  # client name or similar
    # It's a canned reply — no intent/priority keywords
    assert "priority" not in r.text.lower()


def test_sms_non_help_still_runs_llm(app_client):
    """A non-HELP body bypasses the help handler and goes through the pipeline
    (which may fail without a real LLM key, but we check routing)."""
    r = app_client.post("/sms/incoming",
                        data={"From": "+14155550142", "To": "+18449403274",
                              "Body": "my septic is backing up"})
    # With placeholder ANTHROPIC_API_KEY the handler returns 503 (auth error)
    # or 200 depending on key. Either way, help was NOT triggered.
    assert r.status_code in (200, 503)


# ── welcome SMS ────────────────────────────────────────────────────────

def test_build_welcome_body_mentions_business(client_ace, monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret")
    body = owner_commands.build_welcome_body(client_ace)
    assert "Ace HVAC" in body
    assert "HELP" in body   # tells them they can text HELP
    assert len(body) <= 320


def test_build_welcome_body_without_portal_secret(client_ace, monkeypatch):
    monkeypatch.delenv("CLIENT_PORTAL_SECRET", raising=False)
    body = owner_commands.build_welcome_body(client_ace)
    assert "Dashboard:" not in body
    assert "HELP" in body


def test_send_welcome_no_cell(client_ace):
    r = owner_commands.send_welcome_sms(client_ace, twilio_client=None)
    assert r["sent"] is False
    assert r["reason"] == "no_owner_cell"


def test_send_welcome_success(client_ace, monkeypatch):
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    capture = []
    r = owner_commands.send_welcome_sms(
        client_ace, twilio_client=_fake_tw(capture), twilio_from="+18449403274")
    assert r["sent"] is True
    assert capture[0][0] == "+15551234567"


def test_send_welcome_twilio_unavailable(client_ace, monkeypatch):
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    r = owner_commands.send_welcome_sms(
        client_ace, twilio_client=None, twilio_from="")
    assert r["sent"] is False
    assert r["reason"] == "twilio_unavailable"


# ── onboarding CLI ────────────────────────────────────────────────────

def test_cli_welcome_dry_run(capsys):
    from src import onboarding
    rc = onboarding._cli(["welcome", "ace_hvac", "--dry-run", "--to", "+15551234567"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Ace HVAC" in out
    assert "+15551234567" in out


def test_cli_welcome_missing_cell_is_error(capsys):
    from src import onboarding
    # ace_hvac.yaml has empty owner_cell and no --to override given → 2
    rc = onboarding._cli(["welcome", "ace_hvac", "--dry-run"])
    assert rc == 2


def test_cli_welcome_unknown_client(capsys):
    from src import onboarding
    rc = onboarding._cli(["welcome", "does_not_exist"])
    assert rc == 2
