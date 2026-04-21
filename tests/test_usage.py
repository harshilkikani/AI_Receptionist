"""Section E — usage tracking + margin calculation."""

from src import usage, tenant


def test_call_start_end_records_duration(monkeypatch):
    usage.start_call("CA_T1", "ace_hvac", "+14155550142", "+18449403274")
    # Simulate 60 seconds of call
    conn = usage._connect()
    conn.execute("UPDATE calls SET start_ts = start_ts - 60 WHERE call_sid = ?",
                 ("CA_T1",))
    conn.close()
    usage.end_call("CA_T1", outcome="normal")
    rows = usage.recent_calls("ace_hvac")
    assert len(rows) == 1
    assert rows[0]["duration_s"] >= 60
    assert rows[0]["outcome"] == "normal"


def test_log_turn_accumulates_tokens():
    usage.start_call("CA_T2", "ace_hvac", "+14155550142", "+18449403274")
    usage.log_turn("CA_T2", "ace_hvac", "assistant",
                   input_tokens=500, output_tokens=40, tts_chars=70)
    usage.log_turn("CA_T2", "ace_hvac", "assistant",
                   input_tokens=600, output_tokens=50, tts_chars=85)
    summary = usage.monthly_summary("ace_hvac")
    assert summary["llm_input_tokens"] == 1100
    assert summary["llm_output_tokens"] == 90
    assert summary["tts_chars"] == 155


def test_sms_count_per_call():
    assert usage.sms_count_for_call("CA_T3") == 0
    usage.log_sms("CA_T3", "ace_hvac", "+14155550142", "hello")
    usage.log_sms("CA_T3", "ace_hvac", "+14155550142", "there")
    assert usage.sms_count_for_call("CA_T3") == 2
    # Inbound doesn't count
    usage.log_sms("CA_T3", "ace_hvac", "+14155550142", "hi back", direction="inbound")
    assert usage.sms_count_for_call("CA_T3") == 2


def test_margin_calculation_positive(client_ace):
    # Heavy usage but under plan — should be profitable
    for i in range(10):
        usage.start_call(f"M_{i}", "ace_hvac", "+14155550142", "+18449403274")
        usage.log_turn(f"M_{i}", "ace_hvac", "assistant",
                       input_tokens=500, output_tokens=50, tts_chars=80)
        usage.end_call(f"M_{i}", outcome="normal")

    m = usage.margin_for(client_ace)
    assert m["revenue_usd"] == 297.0  # from ace_hvac.yaml
    assert m["margin_usd"] > 0
    assert m["margin_pct"] > 0


def test_spam_filtered_calls_tagged():
    usage.start_call("SPM_1", "ace_hvac", "+19995550123", "+18449403274")
    usage.end_call("SPM_1", outcome="spam_number")
    summary = usage.monthly_summary("ace_hvac")
    assert summary["calls_filtered"] >= 1


def test_emergency_flag_counted():
    usage.start_call("EM_1", "ace_hvac", "+14155550142", "+18449403274")
    usage.end_call("EM_1", outcome="emergency_transfer", emergency=True)
    summary = usage.monthly_summary("ace_hvac")
    assert summary["emergencies"] >= 1
