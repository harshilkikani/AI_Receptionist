"""P2 — invoice generation, rendering, and monthly dispatch."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src import invoices, tenant, usage


# ── Generation ─────────────────────────────────────────────────────────

def test_generate_invoice_base_only(client_ace):
    """Zero calls → only the plan base line is billable."""
    invoice = invoices.generate_invoice(client_ace, month="2026-04")
    assert invoice["client_id"] == "ace_hvac"
    assert invoice["month"] == "2026-04"
    assert invoice["currency"] == "USD"
    # Base line present with correct amount
    base_lines = [ln for ln in invoice["line_items"]
                  if ln["label"] == "Monthly service plan"]
    assert len(base_lines) == 1
    assert base_lines[0]["amount"] == 297.00
    # No overage lines when no calls
    overage = [ln for ln in invoice["line_items"]
               if "overage" in ln["label"].lower()]
    assert overage == []
    # Total == base
    assert invoice["total"] == 297.00


def test_generate_invoice_with_overage(client_ace):
    """Calls over included_calls (250) produce an overage line."""
    # 260 handled calls = 10 overage at $0.75 = $7.50
    for i in range(260):
        sid = f"CA_ov_{i}"
        usage.start_call(sid, "ace_hvac", "+14155550142", "+18449403274")
        usage.end_call(sid, outcome="normal")

    invoice = invoices.generate_invoice(client_ace, month=_this_month())
    overage = [ln for ln in invoice["line_items"]
               if "Call overage" in ln["label"]]
    assert len(overage) == 1
    assert overage[0]["qty"] == 10
    assert overage[0]["amount"] == 7.50
    assert invoice["total"] == 297.00 + 7.50


def test_generate_invoice_rejects_reserved(client_default):
    with pytest.raises(ValueError):
        invoices.generate_invoice(client_default)


def test_generate_invoice_handles_filtered_calls(client_ace):
    """Filtered/spam calls are NOT billable."""
    # Mix of handled + filtered
    for i in range(5):
        sid = f"CA_h_{i}"
        usage.start_call(sid, "ace_hvac", "+14155550100", "+18449403274")
        usage.end_call(sid, outcome="normal")
    for i in range(100):
        sid = f"CA_spam_{i}"
        usage.start_call(sid, "ace_hvac", "+14155550199", "+18449403274")
        usage.end_call(sid, outcome="spam_phrase")

    invoice = invoices.generate_invoice(client_ace, month=_this_month())
    handled_line = [ln for ln in invoice["line_items"]
                    if ln["label"] == "Calls handled"][0]
    assert handled_line["qty"] == 5


# ── Rendering ──────────────────────────────────────────────────────────

def test_render_html_contains_total(client_ace):
    invoice = invoices.generate_invoice(client_ace, month="2026-04")
    html = invoices.render_invoice_html(invoice)
    assert "$297.00" in html
    assert "2026-04" in html
    assert "Ace HVAC" in html
    # No internal vocabulary
    assert "platform_cost" not in html.lower()


def test_render_csv_roundtrips(client_ace):
    invoice = invoices.generate_invoice(client_ace, month="2026-04")
    csv_text = invoices.render_invoice_csv(invoice)
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert rows[0] == ["client_id", "month", "label", "qty", "unit",
                       "unit_price", "amount"]
    # Last row is TOTAL
    assert rows[-1][2] == "TOTAL"
    assert float(rows[-1][-1]) == pytest.approx(297.00)


# ── Dispatch guards ────────────────────────────────────────────────────

def test_send_monthly_skips_when_not_configured_day(monkeypatch, client_ace):
    """If monthly_invoice.enabled=False, skip."""
    fake_now = datetime(2026, 5, 1, 15, 0, tzinfo=timezone.utc)
    result = invoices.send_monthly_invoices(now=fake_now, force=False)
    # Defaults have enabled=False → skipped
    assert result["skipped"] == "not_invoice_day"


def test_send_monthly_targets_previous_month(monkeypatch, client_ace):
    """force=True sends for previous month; target_month reflects it."""
    # Stub out the dispatch functions so no real transport fires
    calls = []
    monkeypatch.setattr(invoices, "_send_webhook_invoice",
                        lambda inv, html, csv: (calls.append(inv), True)[1])
    fake_now = datetime(2026, 5, 1, 15, 0, tzinfo=timezone.utc)
    result = invoices.send_monthly_invoices(now=fake_now, force=True)
    assert result["month"] == "2026-04"
    assert calls, "dispatch should have been called for ace_hvac"
    assert calls[0]["month"] == "2026-04"


def test_send_monthly_skips_smtp_without_owner_email(monkeypatch, client_ace):
    """SMTP transport without owner_email should be skipped with a reason."""
    def fake_cfg():
        return {
            "transport": "smtp",
            "monthly_invoice": {"enabled": True, "send_on_day": 1,
                                "send_hour_utc": 15, "transport": "same_as_digest"},
        }
    monkeypatch.setattr(invoices, "_cfg", fake_cfg)
    # ace_hvac.yaml has owner_email: "" → should skip
    fake_now = datetime(2026, 5, 1, 15, 0, tzinfo=timezone.utc)
    result = invoices.send_monthly_invoices(now=fake_now, force=True)
    skipped = [r for r in result["results"] if r.get("reason") == "owner_email_missing"]
    assert any(r["client_id"] == "ace_hvac" for r in skipped)


def test_previous_month_wraps_year():
    jan = datetime(2026, 1, 5, tzinfo=timezone.utc)
    assert invoices._previous_month(jan) == "2025-12"
    jul = datetime(2026, 7, 5, tzinfo=timezone.utc)
    assert invoices._previous_month(jul) == "2026-06"


def test_is_invoice_day_now_gate(monkeypatch):
    monkeypatch.setattr(invoices, "_cfg", lambda: {
        "monthly_invoice": {"enabled": True, "send_on_day": 1, "send_hour_utc": 15},
    })
    assert invoices._is_invoice_day_now(
        datetime(2026, 5, 1, 15, 30, tzinfo=timezone.utc)) is True
    assert invoices._is_invoice_day_now(
        datetime(2026, 5, 2, 15, 30, tzinfo=timezone.utc)) is False
    assert invoices._is_invoice_day_now(
        datetime(2026, 5, 1, 16, 30, tzinfo=timezone.utc)) is False


# ── Client portal integration ──────────────────────────────────────────

def test_client_portal_invoice_uses_module(monkeypatch):
    """When src.invoices is importable, the portal invoice view renders it."""
    import importlib
    from fastapi.testclient import TestClient
    from src import client_portal
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret")
    import main
    importlib.reload(main)
    c = TestClient(main.app)
    tok = client_portal.issue_token("ace_hvac")
    r = c.get(f"/client/ace_hvac/invoice/2026-04?t={tok}")
    assert r.status_code == 200
    # The rich render uses an explicit "Line" table header
    assert "Line</th>" in r.text or "Line</th" in r.text


# ── CLI ────────────────────────────────────────────────────────────────

def test_cli_preview(capsys, client_ace):
    rc = invoices._cli(["preview", "ace_hvac", "2026-04"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "$297.00" in out


def test_cli_csv(capsys, client_ace):
    rc = invoices._cli(["csv", "ace_hvac", "2026-04"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TOTAL" in out


def test_cli_rejects_unknown(capsys):
    rc = invoices._cli(["preview", "bogus_id", "2026-04"])
    assert rc == 2


def test_cli_send_all_force(monkeypatch, capsys):
    monkeypatch.setattr(invoices, "_send_webhook_invoice",
                        lambda inv, html, csv: True)
    rc = invoices._cli(["send-all", "--month", "2026-04"])
    assert rc == 0
    # Prints a JSON summary
    import json as _json
    out = capsys.readouterr().out
    data = _json.loads(out)
    assert "results" in data


# ── Helpers ────────────────────────────────────────────────────────────

def _this_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")
