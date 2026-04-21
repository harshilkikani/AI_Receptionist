"""Section C — spam filtering (number blocklist + phrase detection + overrides)."""

import json
import pytest
from pathlib import Path
from src import spam_filter


@pytest.fixture(autouse=True)
def _temp_blocklist(tmp_path, monkeypatch):
    """Point spam_filter at a temp blocklist + phrases file for each test."""
    bl_path = tmp_path / "spam_blocklist.json"
    ph_path = tmp_path / "spam_phrases.json"
    bl_path.write_text(json.dumps({
        "numbers": ["+19995550123"],
        "area_codes_high_risk": ["555"],
    }))
    ph_path.write_text(json.dumps({
        "spam_phrases": ["google business listing", "extended warranty"],
        "override_keywords": ["plumbing", "address", "flooding", "street"],
    }))
    monkeypatch.setattr(spam_filter, "_BLOCKLIST_PATH", bl_path)
    monkeypatch.setattr(spam_filter, "_PHRASES_PATH", ph_path)
    spam_filter.reload()
    yield


def test_spam_phrase_rejects_with_enforcement(monkeypatch):
    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "true")
    r = spam_filter.check_phrases("Calling about your google business listing", 3)
    assert r["reject"] is True


def test_spam_phrase_shadow_mode_does_not_reject(monkeypatch):
    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "false")
    r = spam_filter.check_phrases("Calling about your google business listing", 3)
    assert r["reject"] is False  # logged but not enforced


def test_override_keyword_bypasses_spam_phrase(monkeypatch):
    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "true")
    # "google business listing" IS a spam phrase, but "plumbing" triggers override
    r = spam_filter.check_phrases(
        "Hi I'm calling about plumbing and also google business listing", 3
    )
    assert r["reject"] is False
    assert r["reason"] == "override_keyword"


def test_address_override(monkeypatch):
    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "true")
    r = spam_filter.check_phrases(
        "calling from 123 Main street about extended warranty", 3
    )
    assert r["reject"] is False


def test_emergency_override(monkeypatch):
    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "true")
    r = spam_filter.check_phrases("my house is flooding", 3)
    assert r["reject"] is False


def test_phrase_filter_window_closes_after_15s(monkeypatch):
    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "true")
    r = spam_filter.check_phrases("google business listing", 20)
    assert r["reject"] is False


def test_number_blocklist(monkeypatch):
    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "true")
    r = spam_filter.check_number("+19995550123")
    assert r["reject"] is True


def test_number_normalization(monkeypatch):
    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "true")
    # Same number in different formats should all match
    for phone in ["+19995550123", "9995550123", "(999) 555-0123"]:
        r = spam_filter.check_number(phone)
        assert r["reject"] is True, phone


def test_high_risk_area_code(monkeypatch):
    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "true")
    r = spam_filter.check_number("+15551234567")  # area code 555
    assert r["reject"] is True
    assert r["reason"] == "high_risk_area_code"


def test_legitimate_number_not_rejected(monkeypatch):
    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "true")
    r = spam_filter.check_number("+14155550142")  # normal number
    assert r["reject"] is False


def test_kill_switch_bypasses_number_filter(monkeypatch):
    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "false")
    r = spam_filter.check_number("+19995550123")
    assert r["reject"] is False  # kill switch wins


def test_rejections_logged(monkeypatch, tmp_path):
    reject_log = tmp_path / "rejected_calls.jsonl"
    monkeypatch.setattr(spam_filter, "_REJECT_LOG", reject_log)
    monkeypatch.setenv("ENFORCE_SPAM_FILTER", "true")
    monkeypatch.setenv("MARGIN_PROTECTION_ENABLED", "true")
    spam_filter.check_number("+19995550123", client_id="test", call_sid="CA_X")
    assert reject_log.exists()
    line = reject_log.read_text().strip()
    assert "number_blocklisted" in line
    assert "CA_X" in line
