"""V5.7 — ElevenLabs streaming endpoint + connection reuse + lower bitrate.

The v4.1 implementation used `urllib.request.urlopen` which opens a
fresh TCP+TLS connection for every render. v5.7 routes through a
pooled `http.client.HTTPSConnection` and switches to the /stream
endpoint with mp3_22050_32 output (phone audio is 8kHz; 32kbps is
plenty). These tests guard the wire-level behavior.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src import tts


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_AUDIO_DIR", tmp_path / "audio")
    tts.reset_stats()
    tts.close_connection()
    yield
    tts.close_connection()
    tts.reset_stats()


# ── URL shape ───────────────────────────────────────────────────────────

def test_fetch_uses_stream_endpoint(monkeypatch, tmp_path):
    """The render must hit /v1/text-to-speech/{voice}/stream with an
    output_format query param — not the legacy non-streaming path."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    captured = {}

    def fake_post(path, headers, body, timeout=10.0):
        captured["path"] = path
        captured["headers"] = headers
        return 200, b"audio bytes", False

    monkeypatch.setattr(tts, "_request_post", fake_post)
    out_path = tmp_path / "x.mp3"
    ok, err = tts._fetch_elevenlabs("hello", "voice_xyz", {}, out_path)
    assert ok, err
    assert "/v1/text-to-speech/voice_xyz/stream" in captured["path"]
    assert "output_format=" in captured["path"]


def test_default_output_format_is_phone_friendly(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.delenv("ELEVENLABS_OUTPUT_FORMAT", raising=False)
    captured = {}

    def fake_post(path, headers, body, timeout=10.0):
        captured["path"] = path
        return 200, b"audio", False

    monkeypatch.setattr(tts, "_request_post", fake_post)
    tts._fetch_elevenlabs("x", "v", {}, tmp_path / "y.mp3")
    # mp3_22050_32 — sample rate 22050, bitrate 32k. Phone codec is much
    # lower than this so the file shouldn't be limited by our format.
    assert "mp3_22050_32" in captured["path"]


def test_output_format_overridable_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")
    captured = {}
    monkeypatch.setattr(tts, "_request_post",
                        lambda p, h, b, timeout=10.0: (captured.setdefault("p", p),
                                                       (200, b"x", False))[1])
    tts._fetch_elevenlabs("x", "v", {}, tmp_path / "y.mp3")
    assert "mp3_44100_128" in captured["p"]


def test_keepalive_header_sent(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    captured = {}
    monkeypatch.setattr(tts, "_request_post",
                        lambda p, h, b, timeout=10.0: (captured.setdefault("h", h),
                                                       (200, b"x", False))[1])
    tts._fetch_elevenlabs("x", "v", {}, tmp_path / "y.mp3")
    assert captured["h"].get("Connection") == "keep-alive"
    assert captured["h"].get("xi-api-key") == "k"


# ── Connection pool semantics ──────────────────────────────────────────

def test_request_post_creates_one_connection_for_two_calls(monkeypatch):
    """Two successful requests must share a single HTTPSConnection."""
    instances = []

    class FakeResp:
        def read(self):
            return b"audio"
        status = 200

    class FakeConn:
        def __init__(self, host, timeout=10):
            instances.append(self)
            self.requests = 0

        def request(self, method, path, body=None, headers=None):
            self.requests += 1

        def getresponse(self):
            return FakeResp()

        def close(self):
            pass

    monkeypatch.setattr(tts.http.client, "HTTPSConnection", FakeConn)
    s1, b1, r1 = tts._request_post("/p", {}, b"")
    s2, b2, r2 = tts._request_post("/p", {}, b"")
    assert s1 == s2 == 200
    assert r1 is False  # first call → fresh connection
    assert r2 is True   # second call → reused
    assert len(instances) == 1
    assert instances[0].requests == 2


def test_request_post_recovers_from_stale_connection(monkeypatch):
    """If the cached socket has been closed by the server, the next
    call must retry on a new connection."""
    instances = []

    class FakeResp:
        def read(self):
            return b"audio"
        status = 200

    class FakeConn:
        def __init__(self, host, timeout=10):
            self.idx = len(instances)
            instances.append(self)
            self.calls = 0

        def request(self, method, path, body=None, headers=None):
            self.calls += 1
            if self.idx == 0:
                # First connection: pretend the server hung up
                import http.client
                raise http.client.RemoteDisconnected("server closed")

        def getresponse(self):
            return FakeResp()

        def close(self):
            pass

    monkeypatch.setattr(tts.http.client, "HTTPSConnection", FakeConn)
    status, body, reused = tts._request_post("/p", {}, b"")
    assert status == 200
    assert len(instances) == 2  # one stale + one fresh


def test_close_connection_clears_pool(monkeypatch):
    instances = []

    class FakeConn:
        def __init__(self, host, timeout=10):
            instances.append(self)

        def request(self, *a, **k):
            pass

        def getresponse(self):
            r = MagicMock()
            r.status = 200
            r.read.return_value = b"x"
            return r

        def close(self):
            self.closed = True

    monkeypatch.setattr(tts.http.client, "HTTPSConnection", FakeConn)
    tts._request_post("/p", {}, b"")
    tts.close_connection()
    tts._request_post("/p", {}, b"")
    # Two distinct connections because we forced a close in between
    assert len(instances) == 2


# ── Telemetry ──────────────────────────────────────────────────────────

def test_chars_rendered_tracked_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(tts, "_request_post",
                        lambda p, h, b, timeout=10.0: (200, b"audio bytes", False))
    out = tmp_path / "x.mp3"
    tts._fetch_elevenlabs("hello world", "v", {}, out)
    assert tts.render_stats()["chars_rendered"] == len("hello world")


def test_chars_rendered_not_tracked_on_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(tts, "_request_post",
                        lambda p, h, b, timeout=10.0: (500, b"", False))
    tts._fetch_elevenlabs("hi", "v", {}, tmp_path / "x.mp3")
    assert tts.render_stats()["chars_rendered"] == 0


def test_connection_reused_stat_increments(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    flag = {"first": True}

    def fake_post(p, h, b, timeout=10.0):
        was_reused = not flag["first"]
        flag["first"] = False
        return 200, b"audio", was_reused

    monkeypatch.setattr(tts, "_request_post", fake_post)
    tts._fetch_elevenlabs("a", "v", {}, tmp_path / "a.mp3")
    tts._fetch_elevenlabs("b", "v", {}, tmp_path / "b.mp3")
    tts._fetch_elevenlabs("c", "v", {}, tmp_path / "c.mp3")
    assert tts.render_stats()["connection_reused"] == 2  # calls 2 + 3


# ── Failure paths fall back cleanly ────────────────────────────────────

def test_http_error_returns_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(tts, "_request_post",
                        lambda p, h, b, timeout=10.0: (429, b"", False))
    ok, err = tts._fetch_elevenlabs("x", "v", {}, tmp_path / "x.mp3")
    assert not ok
    assert err == "http_429"


def test_empty_body_returns_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(tts, "_request_post",
                        lambda p, h, b, timeout=10.0: (200, b"", False))
    ok, err = tts._fetch_elevenlabs("x", "v", {}, tmp_path / "x.mp3")
    assert not ok
    assert err == "empty_response"


def test_connection_exception_returns_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")

    def raise_conn(p, h, b, timeout=10.0):
        raise ConnectionRefusedError("nope")

    monkeypatch.setattr(tts, "_request_post", raise_conn)
    ok, err = tts._fetch_elevenlabs("x", "v", {}, tmp_path / "x.mp3")
    assert not ok
    assert err == "ConnectionRefusedError"
