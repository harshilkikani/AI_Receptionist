"""V9.6.1 — three direct fixes the user reported on V9.6:

  1. Phone numbers misaligned in call cards (name + phone were inline,
     so the phone column wandered with name length). Now stacked.
  2. Demo timestamps aged forever — "13 hours ago" stuck because seed
     ran once at boot. demo_seed.refresh_timestamps() now slides
     DEMO_v_* rows back to "now − minutes_ago" on every /demo/today.
  3. Call cards need photo avatars. DiceBear (notionists style) URL
     emitted by partner_photo_url(seed); call_card renders an <img>
     overlay with the initial-letter disc as fallback on load error.
"""
from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient

from src import client_portal, demo_seed, design, transcripts, usage


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


# ── 1. Phone alignment — call_card markup ────────────────────────────

def test_call_card_phone_renders_as_block_under_name():
    """V9.6.1 — phone is a sibling <div class="from">, not inline with
    the name. That's what gets the column to align across cards."""
    out = design.call_card(caller="Sarah Wong",
                            from_number="+1 (415) 555-0142",
                            status="answered")
    # Name and phone are siblings under .body, not inline siblings of
    # text in the same .who span.
    assert '<div class="who">Sarah Wong</div>' in out
    assert '<div class="from">+1 (415) 555-0142</div>' in out
    # The legacy inline-span form is gone
    assert '<span class="from">' not in out


def test_call_card_no_phone_omits_from_block():
    out = design.call_card(caller="Unknown", from_number="",
                            status="answered")
    assert '<div class="from">' not in out


def test_from_block_has_tabular_nums_for_aligned_columns():
    """Phone numbers must align across cards — that needs tabular-nums."""
    css = design.css()
    idx = css.find(".call .body .from")
    chunk = css[idx:idx + 400]
    assert "tabular-nums" in chunk


# ── 2. Fresh timestamps via demo_seed.refresh_timestamps() ───────────

def test_refresh_timestamps_slides_demo_voice_calls():
    """A seeded scenario's start_ts must move forward on each refresh
    so it always reads as "minutes_ago" from now."""
    demo_seed.seed_septic_pro()
    # Look up Marcus's emergency (minutes_ago=360, so 6h ago).
    sid = "DEMO_v_15550101001"
    # Move it artificially into the past first.
    fake_past = int(time.time()) - 7 * 86400
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        conn.execute("UPDATE calls SET start_ts = ? WHERE call_sid = ?",
                     (fake_past, sid))
        conn.close()
    # Refresh.
    demo_seed.refresh_timestamps()
    # Now start_ts should be approximately now − 6h.
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        row = conn.execute("SELECT start_ts FROM calls WHERE call_sid = ?",
                           (sid,)).fetchone()
        new_ts = int(row["start_ts"])
        conn.close()
    expected = int(time.time()) - 360 * 60
    # Tolerate a couple of seconds of wall-clock drift.
    assert abs(new_ts - expected) <= 5


def test_refresh_timestamps_slides_demo_sms_rows():
    """SMS-only scenarios (Ron's after-hours callback) also need to
    slide so they don't read stale."""
    demo_seed.seed_septic_pro()
    # Ron's phone +15550101004 normalizes to "5550101004" (leading
    # country code stripped). SID = SMS_5550101004; minutes_ago = 14*60.
    from memory import normalize_phone
    sid = f"SMS_{normalize_phone('+15550101004')}"
    fake_past = int(time.time()) - 30 * 86400
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        conn.execute(
            "UPDATE sms SET ts = ? WHERE call_sid = ? AND client_id = ?",
            (fake_past, sid, "septic_pro"))
        conn.close()
    demo_seed.refresh_timestamps()
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        rows = conn.execute(
            """SELECT ts FROM sms WHERE call_sid = ? AND client_id = ?
            ORDER BY ts ASC""",
            (sid, "septic_pro")).fetchall()
        conn.close()
    assert rows, f"sms rows for {sid} should exist after seed"
    # First sms row ts should be approximately now − 14h.
    first_ts = int(rows[0]["ts"])
    expected = int(time.time()) - 14 * 60 * 60
    assert abs(first_ts - expected) <= 60


def test_refresh_timestamps_is_idempotent():
    """Multiple refreshes within a few seconds should leave the same
    drift, not accumulate."""
    demo_seed.seed_septic_pro()
    sid = "DEMO_v_15550101001"
    demo_seed.refresh_timestamps()
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        ts1 = int(conn.execute(
            "SELECT start_ts FROM calls WHERE call_sid = ?",
            (sid,)).fetchone()["start_ts"])
        conn.close()
    time.sleep(0.05)
    demo_seed.refresh_timestamps()
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        ts2 = int(conn.execute(
            "SELECT start_ts FROM calls WHERE call_sid = ?",
            (sid,)).fetchone()["start_ts"])
        conn.close()
    # Should differ by < 2s (just the wall-clock drift)
    assert abs(ts2 - ts1) < 2


def test_refresh_timestamps_does_not_touch_live_chat_rows():
    """Real chat data (call_sid pattern SMS_<digits> not in demo set)
    must NEVER be moved by the refresh — only DEMO_v_* and the
    seeded SMS_<digits> scenarios."""
    demo_seed.seed_septic_pro()
    # Write a real-looking chat row that the demo never seeded.
    real_sid = "SMS_19998887766"
    real_ts = int(time.time()) - 99
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        conn.execute(
            """INSERT INTO sms
                 (call_sid, client_id, ts, to_number, segments,
                  body_len, direction, month)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (real_sid, "septic_pro", real_ts, "+19998887766",
             1, 5, "inbound", usage._now_month()))
        conn.close()
    demo_seed.refresh_timestamps()
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        row = conn.execute(
            "SELECT ts FROM sms WHERE call_sid = ?",
            (real_sid,)).fetchone()
        conn.close()
    assert int(row["ts"]) == real_ts


def test_demo_today_endpoint_refreshes_timestamps(app_client):
    """E2E: hitting /demo/today should run the refresh as a side effect
    so the rendered body never shows stale ages."""
    demo_seed.seed_septic_pro()
    # Manually age the Marcus row deep in the past.
    sid = "DEMO_v_15550101001"
    fake_past = int(time.time()) - 30 * 86400
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        conn.execute("UPDATE calls SET start_ts = ? WHERE call_sid = ?",
                     (fake_past, sid))
        conn.close()
    # One request to /demo/today
    r = app_client.get("/demo/today")
    assert r.status_code == 200
    # Now Marcus's row should be ~6h ago
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        row = conn.execute("SELECT start_ts FROM calls WHERE call_sid = ?",
                           (sid,)).fetchone()
        conn.close()
    age = int(time.time()) - int(row["start_ts"])
    # 6 hours ± a minute of drift
    assert 6 * 3600 - 60 < age < 6 * 3600 + 60


# ── 3. Photo avatars via partner_photo_url() ─────────────────────────

def test_partner_photo_url_uses_dicebear_notionists():
    """V9.6.1 — DiceBear `notionists` style. Clean illustrated portraits,
    public CDN, no licensing concerns."""
    url = design.partner_photo_url("+15551234567")
    assert "dicebear.com" in url
    assert "notionists" in url


def test_partner_photo_url_stable_for_same_seed():
    """Same partner → same URL → same illustrated portrait across pages."""
    a = design.partner_photo_url("+15551234567")
    b = design.partner_photo_url("+15551234567")
    assert a == b


def test_partner_photo_url_different_for_different_seeds():
    a = design.partner_photo_url("+15551234567")
    b = design.partner_photo_url("+15559998877")
    assert a != b


def test_partner_photo_url_normalizes_phone_for_stable_seed():
    """A partner reached via different formats must get the same photo."""
    a = design.partner_photo_url("+1 (555) 123-4567")
    b = design.partner_photo_url("+15551234567")
    c = design.partner_photo_url("15551234567")
    # All resolve to the same digits seed → same URL
    assert a == b == c


def test_partner_photo_url_empty_returns_empty():
    assert design.partner_photo_url("") == ""


def test_call_card_with_photo_url_renders_img():
    out = design.call_card(caller="Sarah", from_number="+15551112222",
                            status="answered",
                            photo_url="https://example.com/sarah.svg")
    assert '<img class="av-img"' in out
    assert "https://example.com/sarah.svg" in out
    # Initial fallback still present (just hidden behind the img)
    assert "av-initial" in out


def test_call_card_without_photo_falls_back_to_initial():
    out = design.call_card(caller="Linda", from_number="+15553334444",
                            status="answered")
    assert "av-img" not in out
    # Initial sits directly in the .av disc
    assert ">L<" in out


def test_call_card_photo_img_has_onerror_fallback():
    """If the photo URL fails to load, the JS onerror hides the img and
    the initial behind it stays visible."""
    out = design.call_card(caller="X", from_number="+15550000000",
                            status="answered",
                            photo_url="https://example.com/x.svg")
    assert "onerror=" in out
    assert "display='none'" in out or 'display=\\\'none\\\'' in out


def test_call_card_photo_escapes_url():
    """URL is interpolated into HTML — must be escaped so the quote
    inside the payload can't break out of the attribute. The literal
    `onload="..."` injection should not appear in the parsed-attribute
    position; the quote character must be entity-encoded."""
    out = design.call_card(caller="X", from_number="+1",
                            status="answered",
                            photo_url='" onload="alert(1)')
    # The injection's payload, post-escape, has &quot; instead of "
    assert "&quot;" in out
    # The literal unescaped sequence that would close the src=" attribute
    # and then add an onload= handler must not appear.
    assert 'src=" onload="' not in out


# ── Portal today body actually uses photos ───────────────────────────

def test_portal_today_body_emits_photo_imgs_for_partners(app_client):
    """E2E: /demo/today renders call cards with DiceBear <img> tags."""
    demo_seed.seed_septic_pro()
    r = app_client.get("/demo/today")
    assert r.status_code == 200
    body = r.text
    assert "av-img" in body
    assert "dicebear.com" in body


def test_portal_today_partners_have_unique_photos(app_client):
    """Each partner must get a distinct photo URL (different seed)."""
    demo_seed.seed_septic_pro()
    r = app_client.get("/demo/today")
    body = r.text
    import re
    urls = set(re.findall(r'src="(https://api\.dicebear\.com[^"]+)"', body))
    # At least 2 unique partner photos surface in the Today feed
    assert len(urls) >= 2


# ── Demo page chat chips also get photos ────────────────────────────

def test_demo_page_chat_chips_use_photo_avatars(app_client):
    """V9.6.1 — the customer-side phone chips show illustrated portraits
    using the same DiceBear notionists style, so the caller in the
    chat is recognizable as the same person in the portal."""
    r = app_client.get("/")
    body = r.text
    # The JS template that builds the chips has the dicebear URL
    assert "dicebear.com/9.x/notionists" in body
    assert "av-img" in body
