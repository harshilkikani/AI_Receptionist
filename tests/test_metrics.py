"""V3.15 — /metrics endpoint tests."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import llm
from src import call_timer, ops, usage


@pytest.fixture(autouse=True)
def _reset_llm_stats():
    llm.reset_degradation_stats()
    with call_timer._state_lock:
        call_timer._calls.clear()
    yield
    llm.reset_degradation_stats()
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


# ── _render_metrics (pure function) ───────────────────────────────────

def test_metrics_includes_uptime():
    out = ops._render_metrics()
    assert "receptionist_uptime_seconds" in out
    assert "# TYPE receptionist_uptime_seconds gauge" in out


def test_metrics_includes_active_calls():
    call_timer.record_start("CA_metric_1", "ace_hvac")
    out = ops._render_metrics()
    assert "receptionist_active_calls 1" in out


def test_metrics_includes_active_emergency():
    call_timer.record_start("CA_em", "ace_hvac")
    call_timer.mark_emergency("CA_em")
    out = ops._render_metrics()
    assert "receptionist_active_emergency_calls 1" in out


def test_metrics_includes_llm_degradations():
    llm._degraded_response("rate_limit")
    llm._degraded_response("timeout")
    out = ops._render_metrics()
    assert 'receptionist_llm_degradations_total{reason="rate_limit"} 1' in out
    assert 'receptionist_llm_degradations_total{reason="timeout"} 1' in out


def test_metrics_emits_zero_degradation_when_none():
    out = ops._render_metrics()
    # Counter should still appear even at zero so dashboards don't break
    assert "receptionist_llm_degradations_total" in out


def test_metrics_per_client_calls():
    # Seed some calls for ace_hvac
    usage.start_call("CA_m_1", "ace_hvac", "+1", "+1")
    usage.end_call("CA_m_1", outcome="normal")
    usage.start_call("CA_m_2", "ace_hvac", "+1", "+1")
    usage.end_call("CA_m_2", outcome="spam_phrase")
    out = ops._render_metrics()
    assert 'receptionist_calls_total{client="ace_hvac",outcome="all"} 2' in out
    assert 'receptionist_calls_total{client="ace_hvac",outcome="handled"} 1' in out
    assert 'receptionist_calls_total{client="ace_hvac",outcome="filtered"} 1' in out


def test_metrics_includes_margin_gauge():
    out = ops._render_metrics()
    assert "receptionist_margin_pct" in out


def test_metrics_endpoint_returns_200_plain(app_client):
    r = app_client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "receptionist_uptime_seconds" in r.text


def test_metrics_endpoint_no_auth_required(app_client, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "secret")
    r = app_client.get("/metrics")
    # /metrics is NOT under /admin/* so doesn't need auth
    assert r.status_code == 200


def test_metrics_format_has_help_and_type_lines():
    """Prometheus parsers require HELP + TYPE headers for each metric."""
    out = ops._render_metrics()
    # Every '# HELP' line should be followed by a '# TYPE' line
    lines = out.splitlines()
    help_indices = [i for i, l in enumerate(lines) if l.startswith("# HELP ")]
    assert help_indices, "no HELP lines found"
    for i in help_indices:
        # The immediately next line should start with '# TYPE ' for the same metric
        assert lines[i + 1].startswith("# TYPE "), \
            f"HELP at line {i} not followed by TYPE: {lines[i+1]!r}"
