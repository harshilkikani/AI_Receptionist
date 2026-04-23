"""V7 — /health, /ready, request-id correlation tests."""
from __future__ import annotations

import importlib
import logging

import pytest
from fastapi.testclient import TestClient

from src import ops


@pytest.fixture
def app_client(monkeypatch):
    from src import security
    security.reset_buckets()
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    import main
    importlib.reload(main)
    return TestClient(main.app)


# ── /health ────────────────────────────────────────────────────────────

def test_health_ok(app_client):
    r = app_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert body["uptime_s"] >= 0
    assert "ts" in body


def test_health_does_not_require_auth(app_client, monkeypatch):
    """Even with admin auth set, /health should remain open (k8s probes
    don't do Basic auth)."""
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "s3cret")
    r = app_client.get("/health")
    assert r.status_code == 200


# ── /ready ─────────────────────────────────────────────────────────────

def test_ready_happy(app_client):
    r = app_client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["sqlite"] == "ok"
    assert "tenant" in body["checks"]
    assert body["checks"]["prompt_template"] == "ok"


def test_ready_degrades_when_prompt_template_missing(app_client, monkeypatch, tmp_path):
    """Move the prompt template to simulate a bad deploy; /ready 503s."""
    from pathlib import Path
    import llm
    original = llm.PROMPT_PATH
    # Temporarily point to a nonexistent path by monkeypatching the ops
    # check's path resolution — we just move the file aside.
    backup = tmp_path / "receptionist_core.md.bak"
    original.replace(backup)
    try:
        r = app_client.get("/ready")
        assert r.status_code == 503
        assert r.json()["status"] == "degraded"
        assert "fail" in r.json()["checks"]["prompt_template"]
    finally:
        backup.replace(original)


# ── request-id ─────────────────────────────────────────────────────────

def test_request_id_header_present(app_client):
    r = app_client.get("/health")
    rid = r.headers.get("x-request-id")
    assert rid is not None
    assert len(rid) == 8  # 4 hex bytes


def test_request_id_honors_inbound_header(app_client):
    r = app_client.get("/health", headers={"X-Request-ID": "trace-abcdef"})
    assert r.headers["x-request-id"] == "trace-abcdef"


def test_request_id_differs_between_requests(app_client):
    r1 = app_client.get("/health")
    r2 = app_client.get("/health")
    assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


def test_request_id_filter_injects_into_log_record():
    """The logging filter should tag records with whatever contextvar holds."""
    token = ops._current_request_id.set("abc12345")
    try:
        f = ops._RequestIDFilter()
        rec = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", (), None)
        assert f.filter(rec) is True
        assert rec.request_id == "abc12345"
    finally:
        ops._current_request_id.reset(token)


def test_request_id_default_when_unset():
    """Outside any request context, request_id is the sentinel '-'."""
    f = ops._RequestIDFilter()
    rec = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", (), None)
    f.filter(rec)
    assert rec.request_id == "-"
