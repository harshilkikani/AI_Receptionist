"""V3.12 — self-serve signup form tests."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import signup


@pytest.fixture(autouse=True)
def _reset():
    signup._reset_rate_limits()
    yield
    signup._reset_rate_limits()


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    # Point onboarding.CLIENTS_DIR at tmp so test writes don't pollute
    from src import onboarding, tenant
    monkeypatch.setattr(onboarding, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    tenant.reload()
    import main
    importlib.reload(main)
    return TestClient(main.app)


# ── GET /signup ────────────────────────────────────────────────────────

def test_signup_form_renders(app_client):
    r = app_client.get("/signup")
    assert r.status_code == 200
    assert "<form" in r.text
    assert 'name="company_name"' in r.text
    assert 'name="services"' in r.text
    assert 'name="owner_email"' in r.text


def test_signup_disabled_returns_404(app_client, monkeypatch):
    monkeypatch.setenv("ENFORCE_PUBLIC_SIGNUP", "false")
    r = app_client.get("/signup")
    assert r.status_code == 404


# ── POST /signup ──────────────────────────────────────────────────────

def test_signup_submit_happy_path(app_client):
    r = app_client.post("/signup", data={
        "company_name": "Test Plumbing",
        "services": "Plumbing, drain cleaning",
        "owner_email": "test@example.com",
    })
    assert r.status_code == 200
    assert "demo is ready" in r.text.lower()
    assert "/client/demo_" in r.text


def test_signup_rejects_bad_email(app_client):
    r = app_client.post("/signup", data={
        "company_name": "X",
        "services": "Y",
        "owner_email": "not-an-email",
    })
    assert r.status_code == 400
    assert "email" in r.text.lower()


def test_signup_rejects_empty_company(app_client):
    r = app_client.post("/signup", data={
        "company_name": "",
        "services": "Y",
        "owner_email": "test@example.com",
    })
    # FastAPI Form(...) rejects empty with 422; custom validation returns 400.
    # Either is a valid "you messed up" response.
    assert r.status_code in (400, 422)


def test_signup_rejects_too_long_company(app_client):
    r = app_client.post("/signup", data={
        "company_name": "x" * 200,
        "services": "Y",
        "owner_email": "test@example.com",
    })
    assert r.status_code == 400


def test_signup_rate_limit(app_client, monkeypatch):
    monkeypatch.setenv("SIGNUP_RATE_LIMIT_PER_HOUR", "2")
    signup._reset_rate_limits()
    # 2 succeed, 3rd is 429
    for i in range(2):
        r = app_client.post("/signup", data={
            "company_name": f"Co{i}", "services": "x",
            "owner_email": "a@b.co",
        })
        assert r.status_code == 200
    r = app_client.post("/signup", data={
        "company_name": "Co3", "services": "x",
        "owner_email": "a@b.co",
    })
    assert r.status_code == 429


def test_signup_disabled_blocks_post(app_client, monkeypatch):
    monkeypatch.setenv("ENFORCE_PUBLIC_SIGNUP", "false")
    r = app_client.post("/signup", data={
        "company_name": "X", "services": "Y",
        "owner_email": "x@y.com",
    })
    assert r.status_code == 404


def test_signup_escapes_html_injection(app_client):
    """A malicious company name should be HTML-escaped in the error
    message if validation fails."""
    r = app_client.post("/signup", data={
        "company_name": "<script>alert(1)</script>",
        "services": "y",
        "owner_email": "bad-email-format",
    })
    # 400 with escaped form re-render
    assert r.status_code == 400
    assert "<script>alert(1)</script>" not in r.text


def test_signup_writes_demo_yaml(app_client, monkeypatch, tmp_path):
    from src import onboarding
    monkeypatch.setattr(onboarding, "CLIENTS_DIR", tmp_path / "c2")
    r = app_client.post("/signup", data={
        "company_name": "Acme Demo",
        "services": "test services",
        "owner_email": "me@ex.com",
    })
    assert r.status_code == 200
    # A demo_* YAML should have been written
    yamls = list((tmp_path / "c2").glob("demo_*.yaml"))
    assert len(yamls) >= 1


# ── _check_rate_limit unit test ───────────────────────────────────────

def test_rate_limit_clock(monkeypatch):
    monkeypatch.setenv("SIGNUP_RATE_LIMIT_PER_HOUR", "2")
    signup._reset_rate_limits()
    assert signup._check_rate_limit("ip1") is True
    assert signup._check_rate_limit("ip1") is True
    assert signup._check_rate_limit("ip1") is False
    # Different IP has its own bucket
    assert signup._check_rate_limit("ip2") is True
