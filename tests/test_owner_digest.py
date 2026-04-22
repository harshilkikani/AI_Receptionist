"""P4 — owner end-of-day digest + scheduler tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src import owner_digest, scheduler, tenant, usage


# ── build_digest ───────────────────────────────────────────────────────

def test_build_digest_zero_activity(client_ace):
    d = owner_digest.build_digest(client_ace, local_date=date(2026, 4, 21))
    assert d["calls_total"] == 0
    assert d["emergencies"] == 0
    assert d["bookings_captured"] == 0
    assert d["spam_filtered"] == 0
    assert d["avg_response_s"] == 0.0
    assert d["top_issue_themes"] == []
    assert d["timezone"] == "America/New_York"


def test_build_digest_counts_activity(client_ace):
    # Seed a handful of calls TODAY (local), some emergencies, some spam
    today = datetime.now(owner_digest._client_tz(client_ace)).date()
    _seed_call(client_ace, today, "CA_d1", duration=90, outcome="normal",
               intent="Scheduling")
    _seed_call(client_ace, today, "CA_d2", duration=60, outcome="normal",
               intent="Quote")
    _seed_call(client_ace, today, "CA_d3", duration=120, outcome="emergency_transfer",
               intent="Emergency", emergency=True)
    _seed_call(client_ace, today, "CA_d4", duration=3, outcome="spam_phrase",
               intent=None)

    d = owner_digest.build_digest(client_ace, local_date=today)
    assert d["calls_total"] == 4
    assert d["emergencies"] == 1
    assert d["bookings_captured"] == 1
    assert d["spam_filtered"] == 1
    # avg = (90 + 60 + 120) / 3 = 90 (spam excluded, emergency included
    # because outcome 'emergency_transfer' isn't in filtered set)
    assert 85 <= d["avg_response_s"] <= 95
    assert "Scheduling" in d["top_issue_themes"]


def test_build_digest_rejects_reserved(client_default):
    with pytest.raises(ValueError):
        owner_digest.build_digest(client_default)


# ── render ─────────────────────────────────────────────────────────────

def test_render_sms_is_short():
    digest = {
        "client_id": "ace_hvac", "client_name": "Ace HVAC", "date": "2026-04-21",
        "timezone": "America/New_York", "calls_total": 12,
        "emergencies": 1, "bookings_captured": 3, "spam_filtered": 4,
        "avg_response_s": 87.5, "top_issue_themes": ["Scheduling", "Quote"],
        "owner_cell": "", "owner_email": "",
    }
    body = owner_digest.render_sms(digest)
    assert len(body) <= 320
    assert "Ace HVAC" in body
    assert "12 calls" in body
    assert "Scheduling" in body


def test_render_email_has_subject_and_html():
    digest = {
        "client_id": "ace_hvac", "client_name": "Ace HVAC", "date": "2026-04-21",
        "timezone": "America/New_York", "calls_total": 0,
        "emergencies": 0, "bookings_captured": 0, "spam_filtered": 0,
        "avg_response_s": 0.0, "top_issue_themes": [],
        "owner_cell": "", "owner_email": "o@ex.com",
    }
    subject, body = owner_digest.render_email(digest)
    assert "2026-04-21" in subject
    assert "<html" in body.lower()


# ── send_digest ────────────────────────────────────────────────────────

def test_send_digest_prefers_sms_when_cell_set(client_ace, monkeypatch):
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    monkeypatch.setenv("ENFORCE_OWNER_DIGEST", "true")
    captured = []
    fake_tw = SimpleNamespace(messages=SimpleNamespace(
        create=lambda to, from_, body: captured.append((to, body))))
    r = owner_digest.send_digest(
        client_ace, twilio_client=fake_tw, twilio_from="+18449403274",
        local_date=date(2026, 4, 21),
    )
    assert r["sent"] is True
    assert r["via"] == "sms"
    assert captured[0][0] == "+15551234567"


def test_send_digest_shadow_mode(client_ace, monkeypatch):
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    monkeypatch.setenv("ENFORCE_OWNER_DIGEST", "false")
    captured = []
    fake_tw = SimpleNamespace(messages=SimpleNamespace(
        create=lambda to, from_, body: captured.append((to, body))))
    r = owner_digest.send_digest(
        client_ace, twilio_client=fake_tw, twilio_from="+18449403274",
        local_date=date(2026, 4, 21),
    )
    assert r["sent"] is False
    assert r["reason"] == "flag_off"
    assert captured == []


def test_send_digest_no_channel(client_ace, monkeypatch):
    monkeypatch.setitem(client_ace, "owner_cell", "")
    monkeypatch.setitem(client_ace, "owner_email", "")
    r = owner_digest.send_digest(
        client_ace, twilio_client=None, twilio_from="",
        local_date=date(2026, 4, 21),
    )
    assert r["sent"] is False
    assert r["reason"] == "no_channel_available"


def test_send_digest_falls_back_to_email(client_ace, monkeypatch):
    """SMS send fails (no Twilio client) → falls back to email path,
    which itself fails without SMTP — but the code path was exercised."""
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    monkeypatch.setitem(client_ace, "owner_email", "o@example.com")
    r = owner_digest.send_digest(
        client_ace, twilio_client=None, twilio_from="",
        local_date=date(2026, 4, 21),
    )
    # Neither path completes without real creds
    assert r["sent"] is False


# ── scheduler ──────────────────────────────────────────────────────────

def test_scheduler_tick_fires_at_local_hour(client_ace, monkeypatch):
    """Set digest hour to 22, pretend now_local = 22:15 for ace_hvac."""
    scheduler._reset_state()
    monkeypatch.setenv("OWNER_DIGEST_HOUR_LOCAL", "22")
    monkeypatch.setenv("ENFORCE_OWNER_DIGEST", "true")
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")

    fire_count = {"n": 0}

    def fake_send(client, twilio_client=None, twilio_from=None, local_date=None):
        fire_count["n"] += 1
        return {"sent": True, "via": "sms",
                "digest": {"client_id": client["id"], "date": str(local_date)}}

    monkeypatch.setattr(owner_digest, "send_digest", fake_send)

    # Construct a UTC time that is 22:15 in America/New_York
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    now_local = datetime(2026, 4, 21, 22, 15, tzinfo=ny)
    now_utc = now_local.astimezone(ZoneInfo("UTC"))
    scheduler.tick(now_utc=now_utc)
    assert fire_count["n"] == 1
    # Second tick in the same hour → dedupe, no second send
    scheduler.tick(now_utc=now_utc)
    assert fire_count["n"] == 1


def test_scheduler_tick_skips_off_hour(client_ace, monkeypatch):
    scheduler._reset_state()
    monkeypatch.setenv("OWNER_DIGEST_HOUR_LOCAL", "22")
    monkeypatch.setenv("ENFORCE_OWNER_DIGEST", "true")
    monkeypatch.setitem(client_ace, "owner_cell", "+15551234567")
    fired = []
    monkeypatch.setattr(owner_digest, "send_digest",
                        lambda *a, **k: fired.append(True) or {"sent": True})
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    now_local = datetime(2026, 4, 21, 15, 30, tzinfo=ny)
    now_utc = now_local.astimezone(ZoneInfo("UTC"))
    scheduler.tick(now_utc=now_utc)
    assert fired == []


def test_scheduler_tick_ignores_reserved_and_missing_number(monkeypatch):
    scheduler._reset_state()
    monkeypatch.setenv("OWNER_DIGEST_HOUR_LOCAL", "22")
    monkeypatch.setenv("ENFORCE_OWNER_DIGEST", "true")
    fired = []
    monkeypatch.setattr(owner_digest, "send_digest",
                        lambda *a, **k: fired.append(a[0]["id"]) or {"sent": True})
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    now_utc = datetime(2026, 4, 21, 22, 15, tzinfo=ny).astimezone(ZoneInfo("UTC"))
    scheduler.tick(now_utc=now_utc)
    # Only real active tenants fire; _default / _template / example_client
    # (no inbound_number) are skipped
    assert "_default" not in fired
    assert "bobs_septic" not in fired  # example_client has no inbound_number


# ── CLI ────────────────────────────────────────────────────────────────

def test_cli_preview(capsys):
    rc = owner_digest._cli(["preview", "ace_hvac", "2026-04-21"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Ace HVAC" in out
    assert "SMS body" in out


def test_cli_preview_unknown_client():
    rc = owner_digest._cli(["preview", "nope", "2026-04-21"])
    assert rc == 2


def test_cli_preview_bad_date():
    rc = owner_digest._cli(["preview", "ace_hvac", "not-a-date"])
    assert rc == 2


# ── helpers ────────────────────────────────────────────────────────────

def _seed_call(client, local_d, sid, *, duration=60, outcome="normal",
               intent=None, emergency=False):
    """Insert a call + one turn that lives inside the client's local day."""
    tz = owner_digest._client_tz(client)
    start_local = datetime(local_d.year, local_d.month, local_d.day,
                           10, 0, 0, tzinfo=tz)
    start_ts = int(start_local.astimezone(timezone.utc).timestamp())
    end_ts = start_ts + duration

    from src.usage import _connect, _init_schema, _db_lock
    month = start_local.strftime("%Y-%m")
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        conn.execute("""
            INSERT OR REPLACE INTO calls
              (call_sid, client_id, from_number, to_number, start_ts, end_ts,
               duration_s, outcome, emergency, month)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (sid, client["id"], "+14155550142", client["inbound_number"],
              start_ts, end_ts, duration, outcome, 1 if emergency else 0,
              month))
        if intent:
            conn.execute("""
                INSERT INTO turns
                  (call_sid, client_id, ts, input_tokens, output_tokens,
                   tts_chars, role, intent, month)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (sid, client["id"], start_ts + 10, 50, 10, 40,
                  "assistant", intent, month))
        conn.close()
