"""V6.3 — preflight diagnostic tests.

Each individual check has a unit test for ok/warn/fail. The aggregate
`run_all()` rolls them up. The /admin/diagnose route is covered by the
last block, including auth + JSON variant.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import preflight


# ── ANTHROPIC_API_KEY ──────────────────────────────────────────────────

def test_anthropic_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = preflight.check_anthropic_key()
    assert c.status == "fail"


def test_anthropic_set_no_ping(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test12345abcdef")
    c = preflight.check_anthropic_key(ping=False)
    assert c.status == "ok"
    assert "cdef" in c.message  # last 4


def test_anthropic_set_wrong_prefix(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "wrong-format-1234")
    c = preflight.check_anthropic_key()
    assert c.status == "warn"


# ── TWILIO_CREDENTIALS ─────────────────────────────────────────────────

def test_twilio_missing(monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    c = preflight.check_twilio_creds()
    assert c.status == "fail"


def test_twilio_partial(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    c = preflight.check_twilio_creds()
    assert c.status == "fail"


def test_twilio_full_no_ping(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC12345678abcdef")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok123456abcdef")
    c = preflight.check_twilio_creds(ping=False)
    assert c.status == "ok"


def test_twilio_sid_wrong_prefix(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "BAD123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    c = preflight.check_twilio_creds()
    assert c.status == "warn"


# ── TWILIO_VERIFY_SIGNATURES ──────────────────────────────────────────

def test_sig_mode_enforced(monkeypatch):
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "true")
    c = preflight.check_signature_mode()
    assert c.status == "ok"


def test_sig_mode_shadow(monkeypatch):
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    c = preflight.check_signature_mode()
    assert c.status == "warn"


def test_sig_mode_unset_defaults_to_enforced(monkeypatch):
    monkeypatch.delenv("TWILIO_VERIFY_SIGNATURES", raising=False)
    c = preflight.check_signature_mode()
    assert c.status == "ok"


def test_sig_mode_garbage(monkeypatch):
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "maybe")
    c = preflight.check_signature_mode()
    assert c.status == "warn"


# ── PUBLIC_BASE_URL ───────────────────────────────────────────────────

def test_public_base_unset(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    c = preflight.check_public_base_url()
    assert c.status == "fail"


def test_public_base_https(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    c = preflight.check_public_base_url()
    assert c.status == "ok"


def test_public_base_http(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://example.com")
    c = preflight.check_public_base_url()
    assert c.status == "warn"


def test_public_base_garbage(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "not-a-url")
    c = preflight.check_public_base_url()
    assert c.status == "fail"


# ── ADMIN_CREDENTIALS ──────────────────────────────────────────────────

def test_admin_unset(monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)
    c = preflight.check_admin_creds()
    assert c.status == "warn"   # ok for local; only fail if half-set


def test_admin_full(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "longenough123")
    c = preflight.check_admin_creds()
    assert c.status == "ok"


def test_admin_short_password(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "short")
    c = preflight.check_admin_creds()
    assert c.status == "warn"


def test_admin_half_set(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.delenv("ADMIN_PASS", raising=False)
    c = preflight.check_admin_creds()
    assert c.status == "fail"


# ── CLIENT_PORTAL_SECRET ──────────────────────────────────────────────

def test_portal_secret_unset(monkeypatch):
    monkeypatch.delenv("CLIENT_PORTAL_SECRET", raising=False)
    c = preflight.check_portal_secret()
    assert c.status == "fail"


def test_portal_secret_short(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "short")
    c = preflight.check_portal_secret()
    assert c.status == "warn"


def test_portal_secret_ok(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET",
                       "this-is-32-chars-or-more-aaaaaaaaa")
    c = preflight.check_portal_secret()
    assert c.status == "ok"


# ── TENANTS ────────────────────────────────────────────────────────────

def test_tenants_finds_routable():
    """Repo's own clients/ace_hvac.yaml has inbound_number; this should
    always be 'ok' when run from the project root."""
    c = preflight.check_tenants()
    # In the fixture-isolated env, real client YAMLs are loaded, so
    # this should report 'ok' as long as at least one yaml is routable.
    assert c.status in ("ok", "warn")  # warn ok in CI without yamls


# ── USAGE_DB ───────────────────────────────────────────────────────────

def test_usage_db_writable():
    c = preflight.check_usage_db_writable()
    # conftest sets DB_PATH to tmp — always writable
    assert c.status == "ok"


# ── Aggregate ──────────────────────────────────────────────────────────

def test_run_all_returns_summary(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = preflight.run_all(ping=False)
    assert result["summary"] == "fail"
    assert result["counts"]["fail"] >= 1
    assert isinstance(result["checks"], list)
    assert all("status" in c for c in result["checks"])


def test_run_all_passes_with_good_config(monkeypatch):
    """Set everything to good values; aggregate should be 'ok' or 'warn'
    (depends on TENANTS check which loads real YAMLs)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test1234567890")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC12345678abcdef")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok123456abcdef")
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://x.example")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "longenough123")
    monkeypatch.setenv("CLIENT_PORTAL_SECRET",
                       "long-enough-secret-aaaaaaaaaaaaa")
    result = preflight.run_all(ping=False)
    assert result["summary"] in ("ok", "warn")
    assert result["counts"]["fail"] == 0


# ── CLI rendering ──────────────────────────────────────────────────────

def test_render_includes_every_check_name(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = preflight.run_all()
    rendered = preflight._render(result, color=False)
    for c in result["checks"]:
        assert c["name"] in rendered
    assert "preflight" in rendered.lower()


def test_main_exits_nonzero_on_fail(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = preflight.main(["--no-color"])
    assert rc != 0
    out = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY" in out


def test_main_json_output(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test1234567890")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1234")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    preflight.main(["--json", "--no-color"])
    import json
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "summary" in parsed
    assert "checks" in parsed


# ── /admin/diagnose route ──────────────────────────────────────────────

@pytest.fixture
def admin_client(monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


def test_admin_diagnose_html(admin_client):
    r = admin_client.get("/admin/diagnose")
    assert r.status_code == 200
    assert "Preflight diagnostic" in r.text
    assert "ANTHROPIC_API_KEY" in r.text


def test_admin_diagnose_json(admin_client):
    r = admin_client.get("/admin/diagnose.json")
    assert r.status_code == 200
    data = r.json()
    assert "summary" in data
    assert "checks" in data
    assert any(c["name"] == "ANTHROPIC_API_KEY" for c in data["checks"])


def test_admin_diagnose_requires_auth(monkeypatch, admin_client):
    """Same auth rules as the rest of /admin."""
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "secret123")
    r = admin_client.get("/admin/diagnose")
    assert r.status_code == 401
    r2 = admin_client.get("/admin/diagnose.json")
    assert r2.status_code == 401
