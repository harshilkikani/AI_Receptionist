"""P5 — onboarding wizard tests (headless, no real input())."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from src import onboarding, tenant


# ── validators ─────────────────────────────────────────────────────────

def test_v_e164_accepts_valid():
    assert onboarding.v_e164("+14155550142") is None
    assert onboarding.v_e164("") is None  # optional


def test_v_e164_rejects_bad():
    assert onboarding.v_e164("4155550142") is not None
    assert onboarding.v_e164("+abc") is not None


def test_v_snake_id():
    assert onboarding.v_snake_id("bobs_septic") is None
    assert onboarding.v_snake_id("Bobs_Septic") is not None  # uppercase
    assert onboarding.v_snake_id("_leading") is not None
    assert onboarding.v_snake_id("") is not None


def test_v_timezone():
    assert onboarding.v_timezone("America/New_York") is None
    assert onboarding.v_timezone("Garbage/Not_A_Zone") is not None


def test_v_positive_number():
    assert onboarding.v_positive_number("100") is None
    assert onboarding.v_positive_number("0") is None
    assert onboarding.v_positive_number("-1") is not None
    assert onboarding.v_positive_number("abc") is not None


# ── _ask ───────────────────────────────────────────────────────────────

def _reader_from(values):
    """Return a reader(prompt) function yielding `values` in order."""
    it = iter(values)
    return lambda _prompt: next(it)


def test_ask_returns_default_when_empty():
    captured: list = []
    val = onboarding._ask("Foo", default="bar", reader=_reader_from([""]),
                          writer=lambda *a, **k: captured.append(a))
    assert val == "bar"


def test_ask_reprompts_until_valid():
    writes = []
    val = onboarding._ask(
        "Phone", default=None, validator=onboarding.v_e164_required,
        reader=_reader_from(["not-e164", "+14155550142"]),
        writer=lambda *a, **k: writes.append(a),
    )
    assert val == "+14155550142"
    # At least one error message was written
    assert any("E.164" in str(a) or "expected" in " ".join(str(x) for x in a)
               for a in writes)


# ── collect_full (headless) ────────────────────────────────────────────

def test_collect_full_happy_path(tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding, "CLIENTS_DIR", tmp_path)
    # Answer every prompt in order
    answers = [
        "new_tenant",               # id
        "New Tenant LLC",           # name
        "Jane",                     # owner_name
        "jane@example.com",         # owner_email
        "+15551234567",             # owner_cell
        "America/New_York",         # timezone
        "+18885551212",             # inbound_number
        "+15559876543",             # escalation_phone
        "HVAC, plumbing",           # services
        "Service call $99",         # pricing_summary
        "Town",                     # service_area
        "M-F 8-5",                  # hours
        "flooding,burst,gas",       # emergency_keywords
        "starter",                  # tier
        "297",                      # monthly_price
        "250",                      # included_calls
        "500",                      # included_minutes
        "0.75",                     # overage_rate
        "en",                       # language
    ]
    writes: list = []
    cfg = onboarding._collect_full(reader=_reader_from(answers),
                                   writer=lambda *a, **k: writes.append(a))
    assert cfg["id"] == "new_tenant"
    assert cfg["inbound_number"] == "+18885551212"
    assert cfg["emergency_keywords"] == ["flooding", "burst", "gas"]
    assert cfg["plan"]["monthly_price"] == 297.0


def test_collect_full_rejects_existing_id(tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding, "CLIENTS_DIR", tmp_path)
    (tmp_path / "clash.yaml").write_text("id: clash\n")
    with pytest.raises(FileExistsError):
        onboarding._collect_full(reader=_reader_from(["clash"]),
                                  writer=lambda *a, **k: None)


# ── demo ────────────────────────────────────────────────────────────────

def test_build_demo_has_expiry():
    cfg = onboarding._build_demo()
    assert cfg["demo"] is True
    assert cfg["demo_expires_ts"] > int(datetime.now(timezone.utc).timestamp())
    assert cfg["id"].startswith("demo_")


def test_build_demo_respects_override_id():
    cfg = onboarding._build_demo("demo_abc")
    assert cfg["id"] == "demo_abc"


# ── purge ──────────────────────────────────────────────────────────────

def test_purge_moves_expired_demos(tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding, "CLIENTS_DIR", tmp_path)
    # Active demo (future expiry)
    active = {
        "id": "demo_active", "name": "x", "demo": True,
        "demo_expires_ts": int(datetime.now(timezone.utc).timestamp() + 3600),
    }
    expired = {
        "id": "demo_expired", "name": "y", "demo": True,
        "demo_expires_ts": int(datetime.now(timezone.utc).timestamp() - 3600),
    }
    (tmp_path / "demo_active.yaml").write_text(yaml.safe_dump(active))
    (tmp_path / "demo_expired.yaml").write_text(yaml.safe_dump(expired))
    (tmp_path / "normal.yaml").write_text(yaml.safe_dump({"id": "normal"}))

    removed = onboarding.purge_expired_demos()
    assert removed == ["demo_expired"]
    # Moved to _expired/
    assert (tmp_path / "_expired" / "demo_expired.yaml").exists()
    # Active demo still in place
    assert (tmp_path / "demo_active.yaml").exists()
    # Non-demo untouched
    assert (tmp_path / "normal.yaml").exists()


# ── write + followup ──────────────────────────────────────────────────

def test_write_yaml_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding, "CLIENTS_DIR", tmp_path)
    cfg = onboarding._build_demo("demo_roundtrip")
    path = onboarding._write_yaml(cfg)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded["id"] == "demo_roundtrip"


def test_followup_prints_webhook_urls(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.delenv("CLIENT_PORTAL_SECRET", raising=False)
    text = onboarding._followup_text({"id": "xyz",
                                       "inbound_number": "+18885551212"})
    assert "/voice/incoming" in text
    assert "example.com" in text
    assert "+18885551212" in text
    assert "CLIENT_PORTAL_SECRET is not set" in text


def test_followup_includes_portal_url_when_secret_set(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "xyz-secret")
    text = onboarding._followup_text({"id": "ace_hvac",
                                       "inbound_number": "+18449403274"})
    assert "https://example.com/client/ace_hvac?t=" in text


# ── CLI ────────────────────────────────────────────────────────────────

def test_cli_new_demo(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(onboarding, "CLIENTS_DIR", tmp_path)
    rc = onboarding._cli(["new-demo", "--id", "demo_cli"])
    assert rc == 0
    assert (tmp_path / "demo_cli.yaml").exists()


def test_cli_purge_expired(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(onboarding, "CLIENTS_DIR", tmp_path)
    rc = onboarding._cli(["purge-expired"])
    assert rc == 0
