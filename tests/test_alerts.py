"""Section F — alert threshold evaluation."""

from src import alerts, usage


def _simulate_minutes(client_id: str, minutes: int):
    """Log N 1-minute calls for this client in current month."""
    for i in range(minutes):
        sid = f"SIM_{client_id}_{i}"
        usage.start_call(sid, client_id, "+14155550142", "+18449403274")
        conn = usage._connect()
        conn.execute(
            "UPDATE calls SET duration_s = 60, end_ts = start_ts + 60, outcome = 'normal' "
            "WHERE call_sid = ?", (sid,))
        conn.close()


def test_no_threshold_under_60_pct(client_ace):
    # 100 minutes of a 500-min plan = 20%
    _simulate_minutes("ace_hvac", 100)
    ev = alerts.evaluate_client(client_ace)
    assert ev["threshold_name"] is None


def test_log_threshold_at_60_pct(client_ace):
    _simulate_minutes("ace_hvac", 300)  # 300/500 = 60%
    ev = alerts.evaluate_client(client_ace)
    assert ev["threshold_name"] == "log"


def test_notify_threshold_at_80_pct(client_ace):
    _simulate_minutes("ace_hvac", 400)  # 80%
    ev = alerts.evaluate_client(client_ace)
    assert ev["threshold_name"] == "notify"


def test_overage_threshold_at_100_pct(client_ace):
    _simulate_minutes("ace_hvac", 500)  # 100%
    ev = alerts.evaluate_client(client_ace)
    assert ev["threshold_name"] == "overage"


def test_urgent_threshold_at_150_pct(client_ace):
    _simulate_minutes("ace_hvac", 750)  # 150%
    ev = alerts.evaluate_client(client_ace)
    assert ev["threshold_name"] == "urgent"


def test_digest_payload_structure():
    # Stub the webhook call so we don't hit the network
    captured = {}
    def fake_webhook(payload):
        captured["payload"] = payload
        return True
    import src.alerts as mod
    orig = mod._send_webhook
    mod._send_webhook = fake_webhook
    try:
        import os
        os.environ["ENFORCE_USAGE_ALERTS"] = "true"
        os.environ["MARGIN_PROTECTION_ENABLED"] = "true"
        # Ensure at least one client has some usage
        _simulate_minutes("ace_hvac", 50)
        result = alerts.send_digest_now()
        assert result["sent"] is True
        assert "events" in captured["payload"]
        assert len(captured["payload"]["events"]) >= 1
    finally:
        mod._send_webhook = orig
