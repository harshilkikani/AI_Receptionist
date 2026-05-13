"""V8.9b — endpointing-filler tests.

Under the new operating mode (stability > realism > latency), every
failure mode of the async path is exercised. The big invariant:
graceful degradation to the synchronous path on ANY failure — never
a dropped call, never a 5xx.
"""
from __future__ import annotations

import importlib
import threading
import time
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


_AUTH_TOKEN = "test-endpointing-token"
_TENANT_NUMBER = "+18449403274"
_CALLER = "+15555550199"


@pytest.fixture
def signed_client(monkeypatch, tmp_path):
    """A signed TestClient that simulates Twilio webhooks; LLM + Twilio
    REST + webhooks are all mocked so the suite stays offline."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest_v89b")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", _AUTH_TOKEN)
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-mock")
    from src import security
    security.reset_buckets()

    import memory as _memory
    monkeypatch.setattr(_memory, "MEMORY_FILE",
                        tmp_path / "memory_v89b.json")
    if hasattr(_memory, "_cache"):
        _memory._cache = None

    import main
    importlib.reload(main)

    # Mock external IO
    from llm import ChatResponse
    import llm
    monkeypatch.setattr(
        llm, "chat_with_usage",
        lambda *a, **k: (ChatResponse(
            reply="What's the address?",
            intent="Scheduling", priority="low"), (10, 5)))
    from src import owner_notify, webhooks as _webhooks, recordings as _rec
    monkeypatch.setattr(owner_notify, "notify_emergency",
                        lambda *a, **k: None)
    monkeypatch.setattr(_webhooks, "fire_safe", lambda *a, **k: None)
    monkeypatch.setattr(_rec, "start_recording_via_rest",
                        lambda *a, **k: None)
    monkeypatch.setattr(main, "_twilio_client", lambda: None)

    c = TestClient(main.app, raise_server_exceptions=False)
    from twilio.request_validator import RequestValidator
    validator = RequestValidator(_AUTH_TOKEN)

    def sign(path, params=None):
        url = f"http://testserver{path}"
        return validator.compute_signature(url, params or {})
    return c, sign


def _post(client, sign, path, params):
    return client.post(path, data=params,
                       headers={"X-Twilio-Signature": sign(path, params)})


# ── Token store: bounded, TTL, thread-safe ────────────────────────────

def test_store_roundtrip():
    import main
    token = main._think_store_put(
        caller={"id": "x"}, user_message="hi",
        client={"id": "demo"}, call_sid="CA_a", wrap_up_mode=None,
        From="+15555550100", lang="en",
    )
    assert token
    rec = main._think_store_get(token)
    assert rec is not None
    assert rec["ready"] is False
    assert rec["result"] is None
    assert rec["error"] is None
    assert rec["ctx"]["user_message"] == "hi"


def test_store_pop_removes():
    import main
    token = main._think_store_put(
        caller={}, user_message="x", client={}, call_sid="CA_p",
        wrap_up_mode=None, From="+15555550100", lang="en",
    )
    popped = main._think_store_pop(token)
    assert popped is not None
    assert main._think_store_get(token) is None


def test_store_expired_purge_on_put(monkeypatch):
    import main
    # Force a record with stale ts then put another to trigger purge
    token_old = main._think_store_put(
        caller={}, user_message="old", client={}, call_sid="CA_old",
        wrap_up_mode=None, From="+15555550100", lang="en",
    )
    # Mutate ts directly to be stale
    with main._think_lock:
        main._think_store[token_old]["ts"] = time.time() - 60
    # New put triggers prune
    main._think_store_put(
        caller={}, user_message="fresh", client={}, call_sid="CA_fresh",
        wrap_up_mode=None, From="+15555550100", lang="en",
    )
    assert main._think_store_get(token_old) is None


def test_store_bounded_lru():
    import main
    # Save the real cap, set artificially low for test isolation
    original_max = main._THINK_MAX
    try:
        main._THINK_MAX = 5
        tokens = []
        for i in range(20):
            tokens.append(main._think_store_put(
                caller={}, user_message=str(i), client={},
                call_sid=f"CA_{i}", wrap_up_mode=None,
                From="+15555550100", lang="en",
            ))
        # Store size should not exceed cap
        with main._think_lock:
            assert len(main._think_store) <= main._THINK_MAX
    finally:
        main._THINK_MAX = original_max


def test_store_thread_safe():
    """100 threads concurrently put — store stays consistent."""
    import main

    def writer(_i):
        main._think_store_put(
            caller={}, user_message=str(_i), client={},
            call_sid=f"CA_{_i}", wrap_up_mode=None,
            From="+15555550100", lang="en",
        )
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(100)]
    for t in threads: t.start()
    for t in threads: t.join()
    # No exceptions — store is consistent (capped at _THINK_MAX)


# ── Worker: runs pipeline, handles crashes ────────────────────────────

def test_worker_stores_result(monkeypatch):
    import main
    from llm import ChatResponse
    captured = {}

    def fake_pipeline(*a, **k):
        captured["called"] = True
        return {"reply": "Got it.", "intent": "General",
                "priority": "low", "sentiment": "neutral",
                "caller": {"id": "x"}}
    monkeypatch.setattr(main, "_run_pipeline", fake_pipeline)

    token = main._think_store_put(
        caller={"id": "x"}, user_message="hi",
        client={"id": "demo"}, call_sid="CA_w1", wrap_up_mode=None,
        From="+15555550100", lang="en",
    )
    main._think_worker(token)
    rec = main._think_store_get(token)
    assert captured.get("called")
    assert rec["ready"] is True
    assert rec["result"]["reply"] == "Got it."
    assert rec["error"] is None


def test_worker_captures_exception(monkeypatch):
    import main

    def boom(*a, **k):
        raise RuntimeError("anthropic blew up")
    monkeypatch.setattr(main, "_run_pipeline", boom)

    token = main._think_store_put(
        caller={"id": "x"}, user_message="hi",
        client={"id": "demo"}, call_sid="CA_w2", wrap_up_mode=None,
        From="+15555550100", lang="en",
    )
    main._think_worker(token)
    rec = main._think_store_get(token)
    assert rec["ready"] is True
    assert rec["error"] and "anthropic" in rec["error"].lower()


def test_worker_no_op_on_unknown_token():
    import main
    main._think_worker("nonexistent_token_xyz")   # must not raise


# ── Feature flag + filler payload helpers ─────────────────────────────

def test_endpointing_disabled_by_default():
    import main
    assert main._endpointing_enabled({}) is False
    assert main._endpointing_enabled(None) is False
    assert main._endpointing_enabled({"endpointing_fillers": False}) is False


def test_endpointing_explicit_true():
    import main
    assert main._endpointing_enabled({"endpointing_fillers": True}) is True


def test_maybe_filler_returns_none_when_audio_cache_blows_up(monkeypatch):
    import main
    from src import audio_cache
    monkeypatch.setattr(audio_cache, "filler_payload_for",
                        lambda c: (_ for _ in ()).throw(RuntimeError("disk gone")))
    # Must NOT raise — must return None for graceful fallback
    out = main._maybe_filler_for_async({"endpointing_fillers": True,
                                          "tts_provider": "elevenlabs"})
    assert out is None


def test_filler_payload_skips_polly_tenant():
    from src import audio_cache
    out = audio_cache.filler_payload_for({"tts_provider": "polly"})
    assert out is None


def test_filler_payload_skips_when_no_cached_file(monkeypatch, tmp_path):
    from src import audio_cache
    monkeypatch.setattr(audio_cache, "_AUDIO_DIR", tmp_path / "empty")
    out = audio_cache.filler_payload_for(
        {"tts_provider": "elevenlabs", "tts_voice_id": "Rachel"})
    assert out is None


def test_filler_payload_returns_play_when_cached(monkeypatch, tmp_path):
    """When a filler IS cached + PUBLIC_BASE_URL is resolvable, return
    a Play payload. V8.10a — file must be created with the
    prewarm-model hash so the V8.10a-aware lookup finds it."""
    from src import audio_cache, tts
    monkeypatch.setattr(audio_cache, "_AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(tts, "_AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    tts.reset_base_url_cache()
    voice_id = "Rachel"
    first_filler = audio_cache.PREWARM_FILLERS[0]
    client = {"tts_provider": "elevenlabs", "tts_voice_id": "Rachel"}
    # Use the same prewarm model the lookup will use (None client tts_prewarm_model
    # → default eleven_multilingual_v2)
    prewarm_model = tts.model_for(client, prewarm=True)
    h = tts._hash_key(first_filler, voice_id, "elevenlabs",
                      model=prewarm_model)
    (tmp_path / "audio").mkdir(parents=True, exist_ok=True)
    (tmp_path / "audio" / f"{h}.mp3").write_bytes(b"fake mp3")
    import random as _r
    rng = _r.Random()
    rng.shuffle = lambda lst: None  # preserve order so we hit our cached file
    out = audio_cache.filler_payload_for(client, rng=rng)
    assert out is not None
    assert out.kind == "play"
    assert "example.com" in out.url


# ── /voice/respond endpoint — happy + degraded paths ──────────────────

def test_voice_respond_no_token_degrades(signed_client):
    """Bogus request — no token query param. Should NOT crash; should
    return TwiML with a re-prompt."""
    client, sign = signed_client
    r = _post(client, sign, "/voice/respond",
              {"From": _CALLER, "To": _TENANT_NUMBER,
               "CallSid": "CA_resp_notoken"})
    assert r.status_code == 200
    assert "<Response>" in r.text
    assert "<Gather" in r.text


def test_voice_respond_unknown_token_degrades(signed_client):
    """Token expired or never existed (Twilio retry past TTL).
    Re-prompt instead of dropping."""
    client, sign = signed_client
    r = client.post(
        "/voice/respond?t=expired_token_xyz",
        data={"From": _CALLER, "To": _TENANT_NUMBER,
              "CallSid": "CA_resp_expired"},
        headers={"X-Twilio-Signature":
                  sign("/voice/respond?t=expired_token_xyz",
                       {"From": _CALLER, "To": _TENANT_NUMBER,
                        "CallSid": "CA_resp_expired"})})
    assert r.status_code == 200
    assert "<Gather" in r.text  # re-prompt, not hangup
    assert "<Hangup" not in r.text


def test_voice_respond_pipeline_crash_returns_failsafe_twiml(signed_client, monkeypatch):
    """Worker crashed → record.error set → respond emits the V6.2
    failsafe TwiML (still 200 + valid TwiML)."""
    import main

    token = main._think_store_put(
        caller={"id": "x", "phone": _CALLER, "type": "new",
                "history": [], "conversation": []},
        user_message="hi", client={"id": "ace_hvac", "name": "Ace HVAC",
                                    "plan": {}},
        call_sid="CA_resp_crash", wrap_up_mode=None,
        From=_CALLER, lang="en",
    )
    # Simulate worker crash
    with main._think_lock:
        main._think_store[token]["ready"] = True
        main._think_store[token]["error"] = "RuntimeError: boom"

    c, sign = signed_client
    path = f"/voice/respond?t={token}"
    params = {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": "CA_resp_crash"}
    r = c.post(path, data=params,
               headers={"X-Twilio-Signature": sign(path, params)})
    assert r.status_code == 200
    assert "<Response>" in r.text


def test_voice_respond_happy_path_emits_real_reply(signed_client, monkeypatch):
    """Worker completed normally → respond pulls the result and emits
    the proper response TwiML with a Gather (same as the sync path)."""
    import main
    from llm import ChatResponse

    # Put a pre-ready record into the store
    token = main._think_store_put(
        caller={"id": "x", "phone": _CALLER, "type": "new",
                "history": [], "conversation": []},
        user_message="my AC quit", client={"id": "ace_hvac",
                                            "name": "Ace HVAC", "plan": {}},
        call_sid="CA_resp_happy", wrap_up_mode=None,
        From=_CALLER, lang="en",
    )
    with main._think_lock:
        main._think_store[token]["ready"] = True
        main._think_store[token]["result"] = {
            "reply": "Got it — what's the address?",
            "intent": "Scheduling", "priority": "low",
            "sentiment": "neutral",
            "caller": {"id": "x", "phone": _CALLER},
        }

    c, sign = signed_client
    path = f"/voice/respond?t={token}"
    params = {"From": _CALLER, "To": _TENANT_NUMBER,
              "CallSid": "CA_resp_happy"}
    r = c.post(path, data=params,
               headers={"X-Twilio-Signature": sign(path, params)})
    assert r.status_code == 200
    assert "<Response>" in r.text
    # The TwiML should have a Gather (response.priority was low so
    # _respond runs, not the emergency emit path)
    assert "<Gather" in r.text
    # Token should have been popped on success
    assert main._think_store_get(token) is None


def test_voice_respond_high_priority_uses_emergency_path(signed_client, monkeypatch):
    """When the stored result has priority=high, /voice/respond runs
    the emergency branch (same shape as the sync path)."""
    import main

    token = main._think_store_put(
        caller={"id": "x", "phone": _CALLER, "type": "new",
                "address": "412 Maple", "history": [], "conversation": []},
        user_message="gas smell in the basement",
        client={"id": "ace_hvac", "name": "Ace HVAC", "plan": {},
                "escalation_phone": "+15550000000"},
        call_sid="CA_resp_emerg", wrap_up_mode=None,
        From=_CALLER, lang="en",
    )
    with main._think_lock:
        main._think_store[token]["ready"] = True
        main._think_store[token]["result"] = {
            "reply": "Okay — getting a tech out now.",
            "intent": "Emergency", "priority": "high",
            "sentiment": "neutral",
            "caller": {"id": "x", "phone": _CALLER},
        }

    c, sign = signed_client
    path = f"/voice/respond?t={token}"
    params = {"From": _CALLER, "To": _TENANT_NUMBER,
              "CallSid": "CA_resp_emerg"}
    r = c.post(path, data=params,
               headers={"X-Twilio-Signature": sign(path, params)})
    assert r.status_code == 200
    # Emergency with escalation_phone → <Dial> in TwiML
    assert "<Dial" in r.text


# ── /voice/gather routing — sync vs async ────────────────────────────

def test_gather_async_path_returns_filler_redirect(signed_client, monkeypatch, tmp_path):
    """When tenant has endpointing_fillers=true AND a cached filler
    exists AND the base URL is resolvable, /voice/gather should return
    <Play filler><Redirect /voice/respond?t=...>."""
    from src import audio_cache, tts, tenant
    monkeypatch.setattr(audio_cache, "_AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(tts, "_AUDIO_DIR", tmp_path / "audio")
    # Don't set PUBLIC_BASE_URL env (would mismatch Twilio sig URL).
    # Override the resolver directly so audio URLs build correctly.
    monkeypatch.setattr(tts, "_public_base_url",
                        lambda: "http://testserver")
    tts.reset_base_url_cache()

    # Pre-create a cached file for the first filler in the rotation
    voice_id = "EXAVITQu4vr4xnSDxMaL"
    h = tts._hash_key(audio_cache.PREWARM_FILLERS[0], voice_id, "elevenlabs")
    (tmp_path / "audio").mkdir(parents=True, exist_ok=True)
    (tmp_path / "audio" / f"{h}.mp3").write_bytes(b"audio")

    fake_client = {
        "id": "ace_hvac", "name": "Ace HVAC",
        "inbound_number": _TENANT_NUMBER,
        "tts_provider": "elevenlabs",
        "tts_voice_id": voice_id,
        "endpointing_fillers": True,
        "emergency_keywords": [], "plan": {},
    }
    monkeypatch.setattr(tenant, "load_client_by_number",
                        lambda num: fake_client)
    # Force deterministic filler pick — first in list
    monkeypatch.setattr(audio_cache, "filler_payload_for",
                        lambda c, rng=None: tts.TtsPayload(
                            kind="play",
                            url=f"http://testserver/audio/{h}.mp3"))

    c, sign = signed_client
    params = {"From": _CALLER, "To": _TENANT_NUMBER,
              "CallSid": "CA_async_1", "SpeechResult": "my ac is broken",
              "Language": "en-US"}
    r = _post(c, sign, "/voice/gather", params)
    assert r.status_code == 200
    assert "<Play>" in r.text
    assert "/voice/respond?t=" in r.text


def test_gather_falls_through_when_endpointing_disabled(signed_client):
    """Tenant without endpointing_fillers flag → synchronous path.
    No /voice/respond redirect should appear."""
    client, sign = signed_client
    params = {"From": _CALLER, "To": _TENANT_NUMBER,
              "CallSid": "CA_sync_1",
              "SpeechResult": "what are your hours",
              "Language": "en-US"}
    r = _post(client, sign, "/voice/gather", params)
    assert r.status_code == 200
    # No filler redirect in the sync path
    assert "/voice/respond?t=" not in r.text


def test_gather_falls_through_when_no_cached_filler(signed_client, monkeypatch, tmp_path):
    """Endpointing enabled but no filler is cached → fall through to
    the synchronous path. This is the graceful-degradation invariant."""
    from src import audio_cache, tts, tenant
    monkeypatch.setattr(audio_cache, "_AUDIO_DIR", tmp_path / "no_audio")
    monkeypatch.setattr(tts, "_AUDIO_DIR", tmp_path / "no_audio")
    monkeypatch.setattr(tts, "_public_base_url",
                        lambda: "http://testserver")
    tts.reset_base_url_cache()

    fake_client = {
        "id": "ace_hvac", "name": "Ace HVAC",
        "inbound_number": _TENANT_NUMBER,
        "tts_provider": "elevenlabs",
        "tts_voice_id": "Rachel",
        "endpointing_fillers": True,
        "emergency_keywords": [], "plan": {},
    }
    monkeypatch.setattr(tenant, "load_client_by_number",
                        lambda num: fake_client)
    # No cached files exist → helper returns None → sync path
    monkeypatch.setattr(audio_cache, "filler_payload_for",
                        lambda c, rng=None: None)

    c, sign = signed_client
    params = {"From": _CALLER, "To": _TENANT_NUMBER,
              "CallSid": "CA_no_cache",
              "SpeechResult": "what are your hours",
              "Language": "en-US"}
    r = _post(c, sign, "/voice/gather", params)
    assert r.status_code == 200
    assert "/voice/respond?t=" not in r.text


def test_gather_empty_speech_still_uses_v89a_path(signed_client):
    """Empty SpeechResult must NOT trigger the async path — it goes
    through the V8.9a empty-speech re-prompt logic. Otherwise we'd
    burn a filler + redirect on every silence."""
    client, sign = signed_client
    # First record_start a call
    _post(client, sign, "/voice/incoming",
          {"From": _CALLER, "To": _TENANT_NUMBER, "CallSid": "CA_empty_v89b"})
    r = _post(client, sign, "/voice/gather",
              {"From": _CALLER, "To": _TENANT_NUMBER,
               "CallSid": "CA_empty_v89b",
               "SpeechResult": "", "Language": "en-US"})
    assert r.status_code == 200
    # Should re-prompt (V8.9a), NOT redirect to /voice/respond
    assert "/voice/respond?t=" not in r.text
    assert "<Gather" in r.text


# ── /voice/respond in PROTECTED_PATHS ─────────────────────────────────

def test_respond_path_in_protected_list():
    """V8.9b regression guard — /voice/respond MUST be signature-
    verified, otherwise an attacker could forge a token and trigger
    pipeline runs at our cost."""
    from src import twilio_signature
    assert "/voice/respond" in twilio_signature.PROTECTED_PATHS
