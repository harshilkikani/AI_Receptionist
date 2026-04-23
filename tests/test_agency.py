"""V3.9 — agency tenancy tests."""
from __future__ import annotations

import importlib

import pytest
import yaml
from fastapi.testclient import TestClient

from src import agency


@pytest.fixture(autouse=True)
def _reload():
    agency.reload()
    yield
    agency.reload()


# ── loader ─────────────────────────────────────────────────────────────

def test_example_agency_loads():
    agencies = agency.list_agencies()
    ids = {a["id"] for a in agencies}
    assert "acme_ai" in ids


def test_get_agency_existing():
    a = agency.get_agency("acme_ai")
    assert a is not None
    assert a["name"] == "Acme AI Agency"


def test_get_agency_missing():
    assert agency.get_agency("nobody") is None


def test_clients_for_agency():
    clients = agency.clients_for_agency("acme_ai")
    assert "ace_hvac" in clients
    assert "septic_pro" in clients


def test_clients_for_missing_agency_is_empty():
    assert agency.clients_for_agency("nobody") == []


def test_agency_owns_client():
    assert agency.agency_owns_client("acme_ai", "ace_hvac") is True
    assert agency.agency_owns_client("acme_ai", "unknown_client") is False


def test_agency_for_client_reverse_lookup():
    assert agency.agency_for_client("ace_hvac") == "acme_ai"


def test_agency_for_client_unknown():
    assert agency.agency_for_client("unowned_client_xyz") is None


# ── malformed yaml survives ───────────────────────────────────────────

def test_malformed_yaml_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(agency, "_AGENCIES_DIR", tmp_path)
    (tmp_path / "good.yaml").write_text(
        yaml.safe_dump({"id": "good", "owned_clients": ["x"]}))
    (tmp_path / "bad.yaml").write_text("::: not valid yaml :::")
    agency.reload()
    ids = {a["id"] for a in agency.list_agencies()}
    assert "good" in ids
    assert "bad" not in ids


def test_yaml_missing_id_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(agency, "_AGENCIES_DIR", tmp_path)
    (tmp_path / "noid.yaml").write_text(
        yaml.safe_dump({"name": "no id"}))
    agency.reload()
    assert agency.list_agencies() == []


# ── admin route ────────────────────────────────────────────────────────

@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


def test_admin_agency_view_renders(app_client):
    r = app_client.get("/admin/agency/acme_ai")
    assert r.status_code == 200
    assert "Acme AI Agency" in r.text
    assert "ace_hvac" in r.text


def test_admin_agency_view_404_unknown(app_client):
    r = app_client.get("/admin/agency/nobody")
    assert r.status_code == 404
