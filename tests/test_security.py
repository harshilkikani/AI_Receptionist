"""P0 — security middleware tests.

Covers:
  - X-Content-Type-Options and Referrer-Policy headers applied everywhere
  - /admin/* 429s after 60 req/min per IP
  - Non-admin paths are NOT rate-limited
  - Admin Basic auth returns 401 when ADMIN_USER + ADMIN_PASS are set
  - Admin auth accepts correct credentials
"""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from src import security


@pytest.fixture(autouse=True)
def _reset_buckets():
    security.reset_buckets()
    yield
    security.reset_buckets()


@pytest.fixture
def app_client():
    # Import main lazily so env tweaks in individual tests apply first
    import importlib
    import main
    importlib.reload(main)
    return TestClient(main.app)


def test_security_headers_on_every_response(app_client):
    r = app_client.get("/missed-calls")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("referrer-policy") == "no-referrer"


def test_security_headers_on_admin(app_client):
    r = app_client.get("/admin/flags")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("referrer-policy") == "no-referrer"


def test_admin_rate_limit_blocks_past_cap(monkeypatch, app_client):
    # Lower cap to 3 so the test runs fast
    monkeypatch.setenv("ADMIN_RATE_LIMIT_PER_MIN", "3")
    # First 3 requests succeed
    for _ in range(3):
        r = app_client.get("/admin/flags")
        assert r.status_code == 200
    # 4th is blocked
    r = app_client.get("/admin/flags")
    assert r.status_code == 429
    body = r.json()
    assert body["error"] == "rate_limited"
    assert r.headers.get("retry-after") == "60"


def test_non_admin_path_never_rate_limited(monkeypatch, app_client):
    monkeypatch.setenv("ADMIN_RATE_LIMIT_PER_MIN", "2")
    # Hammer a non-admin path well past the cap
    for _ in range(20):
        r = app_client.get("/missed-calls")
        assert r.status_code == 200


def test_admin_auth_401_when_creds_set(monkeypatch, app_client):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "s3cret")
    r = app_client.get("/admin/flags")
    assert r.status_code == 401
    assert "basic" in r.headers.get("www-authenticate", "").lower()


def test_admin_auth_accepts_correct_creds(monkeypatch, app_client):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "s3cret")
    auth = base64.b64encode(b"admin:s3cret").decode()
    r = app_client.get("/admin/flags", headers={"Authorization": f"Basic {auth}"})
    assert r.status_code == 200


def test_admin_auth_rejects_wrong_creds(monkeypatch, app_client):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "s3cret")
    auth = base64.b64encode(b"admin:wrong").decode()
    r = app_client.get("/admin/flags", headers={"Authorization": f"Basic {auth}"})
    assert r.status_code == 401


def test_xff_is_used_for_ip_identity(monkeypatch, app_client):
    """Two different X-Forwarded-For IPs get independent buckets."""
    monkeypatch.setenv("ADMIN_RATE_LIMIT_PER_MIN", "2")
    # IP A — exhaust its bucket
    for _ in range(2):
        r = app_client.get("/admin/flags", headers={"X-Forwarded-For": "1.2.3.4"})
        assert r.status_code == 200
    r = app_client.get("/admin/flags", headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 429
    # IP B — independent bucket, still allowed
    r = app_client.get("/admin/flags", headers={"X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 200
