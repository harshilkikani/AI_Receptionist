"""V5.4 — comprehensive cross-tenant isolation audit.

Defense-in-depth verification that a portal token signed for tenant A
cannot pull data belonging to tenant B, across every customer-facing
surface:
  - /client/{B}*?t=A_token              → 403 (already covered partially)
  - /client/{A}/call/{B's call_sid}     → 404 (tenant ownership filter)
  - /calendar/{B}.ics?t=A_token         → 403
  - bookings.list_bookings(B) by A      → empty / 0 leak
  - recall.prior_calls(B, phone)        → only B's calls
  - transcripts.get_call_meta seen ONLY through tenant-aware admin/portal

Operator (admin) routes are intentionally NOT in this audit — admin is
god-mode by design.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import bookings, client_portal, recall, transcripts, usage


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "isolation-test")
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


# ── Portal: token for A doesn't unlock B's pages ────────────────────

def test_portal_summary_cross_tenant_403(app_client):
    tok_a = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/septic_pro?t={tok_a}")
    assert r.status_code == 403


def test_portal_calls_cross_tenant_403(app_client):
    tok_a = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/septic_pro/calls?t={tok_a}")
    assert r.status_code == 403


def test_portal_invoice_cross_tenant_403(app_client):
    tok_a = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/septic_pro/invoice/2026-04?t={tok_a}")
    assert r.status_code == 403


# ── /client/{A}/call/{call_sid} where call belongs to B ─────────────

def test_call_detail_rejects_other_tenants_call_sid(app_client):
    """Even if A has a valid token AND knows B's call_sid, the route
    must 404 because the meta belongs to a different tenant."""
    # Seed a call owned by septic_pro
    sid_b = "CA_B_isolated"
    usage.start_call(sid_b, "septic_pro", "+14155550100", "+18885551212")
    transcripts.record_turn(sid_b, "septic_pro", "user", "septic problem")
    transcripts.record_turn(sid_b, "septic_pro", "assistant", "septic answer")
    usage.end_call(sid_b, outcome="normal")

    tok_a = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/client/ace_hvac/call/{sid_b}?t={tok_a}")
    assert r.status_code == 404
    # And the sensitive content must not leak in the body
    assert "septic problem" not in r.text


# ── /calendar/{B}.ics with A's token ────────────────────────────────

def test_calendar_feed_cross_tenant_403(app_client):
    tok_a = client_portal.issue_token("ace_hvac")
    r = app_client.get(f"/calendar/septic_pro.ics?t={tok_a}")
    assert r.status_code == 403


# ── bookings.list_bookings filter ───────────────────────────────────

def test_list_bookings_only_returns_own_tenant():
    bk_a = bookings.record_booking(
        client_id="ace_hvac", caller_phone="+14155550100",
        caller_name="A-customer")
    bk_b = bookings.record_booking(
        client_id="septic_pro", caller_phone="+17175550100",
        caller_name="B-customer")
    a_list = bookings.list_bookings(client_id="ace_hvac")
    a_ids = {r["id"] for r in a_list}
    assert bk_a["id"] in a_ids
    assert bk_b["id"] not in a_ids


# ── recall.prior_calls strictly tenant-scoped ──────────────────────

def test_prior_calls_strictly_filters_by_client_id():
    """Same caller phone calls TWO different tenants. Each tenant's
    recall block should only see ITS calls — never the other's."""
    # Customer +14155550100 calls ace_hvac
    usage.start_call("CA_iso_A", "ace_hvac", "+14155550100", "+18449403274")
    usage.end_call("CA_iso_A", outcome="normal")
    # SAME customer calls septic_pro (different number)
    usage.start_call("CA_iso_B", "septic_pro", "+14155550100", "+18885551212")
    usage.end_call("CA_iso_B", outcome="normal")

    a_calls = recall.prior_calls("ace_hvac", "+14155550100")
    b_calls = recall.prior_calls("septic_pro", "+14155550100")
    a_sids = {c["call_sid"] for c in a_calls}
    b_sids = {c["call_sid"] for c in b_calls}
    assert "CA_iso_A" in a_sids
    assert "CA_iso_A" not in b_sids
    assert "CA_iso_B" in b_sids
    assert "CA_iso_B" not in a_sids


# ── transcripts.get_call_meta is unbounded by design ────────────────

def test_transcripts_get_call_meta_returns_metadata_for_anyone():
    """get_call_meta is a low-level helper used by admin AND tenant-
    scoped portal routes. It returns meta regardless of tenant — the
    CALLER (admin or portal handler) is responsible for the
    tenant-ownership filter. This documents that contract."""
    sid = "CA_meta_test"
    usage.start_call(sid, "septic_pro", "+1", "+1")
    usage.end_call(sid, outcome="normal")
    meta = transcripts.get_call_meta(sid)
    # Returns the row even though we never asked which tenant
    assert meta is not None
    assert meta["client_id"] == "septic_pro"


# ── reserved client IDs are unreachable through portal ─────────────

def test_reserved_client_id_unreachable(app_client):
    """Even a perfectly valid token for "_default" or "_template"
    shouldn't load a portal — those are reserved/template tenants."""
    tok = client_portal.issue_token("_default")
    r = app_client.get(f"/client/_default?t={tok}")
    assert r.status_code == 403


def test_reserved_client_id_calendar_unreachable(app_client):
    tok = client_portal.issue_token("_template")
    r = app_client.get(f"/calendar/_template.ics?t={tok}")
    assert r.status_code == 403


# ── secret rotation invalidates ALL tokens ─────────────────────────

def test_secret_rotation_invalidates_tokens(app_client, monkeypatch):
    """Operator rotates CLIENT_PORTAL_SECRET → all tokens (regardless
    of tenant) become 403."""
    tok = client_portal.issue_token("ace_hvac")
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "rotated-new-secret")
    r = app_client.get(f"/client/ace_hvac?t={tok}")
    assert r.status_code == 403
    r = app_client.get(f"/calendar/ace_hvac.ics?t={tok}")
    assert r.status_code == 403


# ── webhooks fire only for the owning tenant ────────────────────────

def test_webhooks_fire_only_for_owning_tenant():
    """Subscriptions live in each tenant's YAML. webhooks.fire(event,
    client) only iterates client.webhooks — there's no global queue
    to leak across."""
    from src import webhooks as _wh
    captured = []

    def fake_post(url, body, signature, headers=None):
        captured.append({"url": url})
        return (200, None)

    a_client = {"id": "tenant_A", "webhooks": [
        {"url": "https://hooks.A.example/", "events": ["call.ended"]},
    ]}
    b_client = {"id": "tenant_B", "webhooks": [
        {"url": "https://hooks.B.example/", "events": ["call.ended"]},
    ]}
    _wh.fire("call.ended", a_client, post_fn=fake_post)
    # Only A's webhook should have been hit
    urls = {c["url"] for c in captured}
    assert "https://hooks.A.example/" in urls
    assert "https://hooks.B.example/" not in urls
