"""P1 — client portal: token signing, route auth, content hygiene."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import client_portal, usage


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret-for-portal")
    import main
    importlib.reload(main)
    # Reset rate-limit buckets between tests (middleware lives in main)
    from src import security
    security.reset_buckets()
    return TestClient(main.app)


def test_token_roundtrip(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "abc123")
    tok = client_portal.issue_token("ace_hvac")
    assert client_portal.verify_token("ace_hvac", tok) is True
    # Mismatched client id
    assert client_portal.verify_token("other_client", tok) is False
    # Mangled token
    assert client_portal.verify_token("ace_hvac", tok[:-1] + "0") is False


def test_token_requires_secret(monkeypatch):
    monkeypatch.delenv("CLIENT_PORTAL_SECRET", raising=False)
    with pytest.raises(ValueError):
        client_portal.issue_token("ace_hvac")


def test_rotation_invalidates_old_token(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "v1")
    tok = client_portal.issue_token("ace_hvac")
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "v2")
    assert client_portal.verify_token("ace_hvac", tok) is False


def test_token_unset_always_rejects(monkeypatch):
    # Even a previously-valid-looking token is rejected if secret is unset
    monkeypatch.delenv("CLIENT_PORTAL_SECRET", raising=False)
    assert client_portal.verify_token("ace_hvac", "123.abc") is False


def test_summary_rejects_bad_token(app_client):
    r = app_client.get("/client/ace_hvac?t=bogus")
    assert r.status_code == 403


def test_summary_rejects_missing_token(app_client):
    r = app_client.get("/client/ace_hvac")
    assert r.status_code == 403


def test_summary_accepts_valid_token(app_client):
    import re
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert r.status_code == 200
    body = r.text
    # Sanity: client name renders
    assert "Ace HVAC" in body
    # Operator-only vocabulary must not leak into the visible content.
    # Strip the <style> block (which uses CSS "margin" and "cost"-adjacent rules).
    visible = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)
    for banned in ("margin %", "platform_cost", "revenue", "$/call", "cost_usd"):
        assert banned not in visible.lower(), f"{banned!r} must not appear in client summary"


def test_unknown_client_returns_403_not_404(app_client):
    """Don't leak client-exists info via differing status codes."""
    tok = client_portal.issue_token("nonexistent")
    r = app_client.get(f"/client/nonexistent?t={tok}")
    assert r.status_code == 403


def test_default_client_not_reachable(app_client):
    """_default can never be reached via the portal even with a valid token."""
    tok = client_portal.issue_token("_default")
    r = app_client.get(f"/client/_default?t={tok}")
    assert r.status_code == 403


def test_calls_route_shows_log(app_client):
    # Seed a call
    usage.start_call("CA_portal_1", "ace_hvac", "+14155550199", "+18449403274")
    usage.log_turn("CA_portal_1", "ace_hvac", "assistant",
                   input_tokens=50, output_tokens=10, tts_chars=40,
                   intent="Scheduling")
    usage.end_call("CA_portal_1", outcome="normal")
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/calls?t={tok}")
    assert r.status_code == 200
    assert "Handled" in r.text
    assert "Scheduling" in r.text


def test_invoice_route_renders_fallback(app_client):
    tok = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/invoice/2026-04?t={tok}")
    assert r.status_code == 200
    assert "Invoice" in r.text
    assert "2026-04" in r.text
    # No cost/margin terms in internal-only sense
    assert "platform_cost" not in r.text


def test_portal_url_helper(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    url = client_portal.portal_url("ace_hvac")
    assert url.startswith("https://example.com/client/ace_hvac?t=")


def test_cli_issue_unknown_client(monkeypatch, capsys):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "s")
    rc = client_portal._cli(["issue", "does_not_exist"])
    assert rc == 2


def test_cli_issue_known_client(monkeypatch, capsys):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "s")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    rc = client_portal._cli(["issue", "ace_hvac"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("https://example.com/client/ace_hvac?t=")


def test_cli_issue_requires_secret(monkeypatch):
    monkeypatch.delenv("CLIENT_PORTAL_SECRET", raising=False)
    rc = client_portal._cli(["issue", "ace_hvac"])
    assert rc == 2
