"""V4.1 — Pluggable TTS provider abstraction.

The default voice (Polly Neural via Twilio's `<Say>`) is fine. ElevenLabs
Conversational + OpenAI gpt-realtime sound dramatically more human in
2026 — and tenants who'll pay for the upgrade should be able to opt in
without forking the codebase.

This module is the seam:

  provider = resolve_provider(client)
  payload = provider.render(text, lang)
  if payload.kind == "polly":
      g.say(payload.text, voice=payload.polly_voice)
  else:                          # "play"
      g.play(payload.url)        # cached audio served from /audio/<hash>.mp3

Tenants pick via YAML:

    tts_provider: polly         # default — current behavior
    tts_provider: elevenlabs    # opt-in
    tts_voice_id: "Rachel"      # provider-specific voice id
    tts_voice_settings:         # optional ElevenLabs knobs
      stability: 0.55
      similarity: 0.75

Caching:
  - sha256(text + voice_id + provider) keys an entry in data/audio/
  - Repeated phrases (greetings, goodbyes, capacity messages) hit the
    cache; Anthropic responses don't repeat so they re-render

Failure handling:
  - Any provider error → fall back to PollyProvider so the call survives.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import urllib.request
import urllib.error
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("tts")

_AUDIO_DIR = Path(__file__).parent.parent / "data" / "audio"

# In-memory tracker so tests can introspect without touching disk
_render_stats: dict = {"polly": 0, "elevenlabs": 0, "fallback": 0,
                       "cache_hit": 0, "cache_miss": 0}
_stats_lock = threading.Lock()


def render_stats() -> dict:
    with _stats_lock:
        return dict(_render_stats)


def reset_stats():
    with _stats_lock:
        for k in _render_stats:
            _render_stats[k] = 0


def _bump(key: str):
    with _stats_lock:
        _render_stats[key] = _render_stats.get(key, 0) + 1


@dataclass
class TtsPayload:
    """Result of rendering text. `kind="polly"` → embed in <Say>;
    `kind="play"` → emit <Play> with `url`."""
    kind: str
    text: str = ""
    polly_voice: str = ""
    url: str = ""
    duration_estimate_ms: int = 0


def _polly_voice_for_lang(lang: str) -> str:
    return {
        "en": "Polly.Joanna-Neural", "es": "Polly.Lupe-Neural",
        "hi": "Polly.Kajal-Neural", "gu": "Polly.Kajal-Neural",
        "pt": "Polly.Camila-Neural", "it": "Polly.Bianca-Neural",
        "ja": "Polly.Kazuha-Neural", "ko": "Polly.Seoyeon-Neural",
        "zh": "Polly.Zhiyu-Neural",
    }.get(lang, "Polly.Joanna-Neural")


def _hash_key(text: str, voice_id: str, provider: str) -> str:
    return hashlib.sha256(
        f"{provider}|{voice_id}|{text}".encode("utf-8")
    ).hexdigest()[:24]


# ── Provider interface ────────────────────────────────────────────────

class TtsProvider:
    name: str = "base"

    def render(self, text: str, lang: str = "en",
               voice_id: Optional[str] = None,
               settings: Optional[dict] = None) -> TtsPayload:
        raise NotImplementedError


class PollyProvider(TtsProvider):
    """The default. Renders to a Twilio <Say> with the right Polly voice
    — no audio bytes generated locally; Twilio Polly synthesizes."""
    name = "polly"

    def render(self, text, lang="en", voice_id=None, settings=None):
        _bump("polly")
        return TtsPayload(
            kind="polly",
            text=text or "",
            polly_voice=voice_id or _polly_voice_for_lang(lang),
        )


class ElevenLabsProvider(TtsProvider):
    """Generates audio bytes via the ElevenLabs HTTP API, caches them
    to data/audio/<hash>.mp3, and emits a TwiML <Play> URL.

    Required env: ELEVENLABS_API_KEY.
    Optional env: ELEVENLABS_MODEL (default eleven_turbo_v2_5).
    """
    name = "elevenlabs"

    DEFAULT_MODEL = "eleven_turbo_v2_5"
    DEFAULT_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"   # Rachel — generic example

    def render(self, text, lang="en", voice_id=None, settings=None):
        api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        if not (text or "").strip() or not api_key:
            # Fall back silently. Caller should already have a Polly
            # backup wired; we just re-render via Polly provider.
            _bump("fallback")
            return PollyProvider().render(text, lang, voice_id=None)

        vid = voice_id or os.environ.get("ELEVENLABS_VOICE_ID",
                                          self.DEFAULT_VOICE_ID)
        h = _hash_key(text, vid, "elevenlabs")
        path = _AUDIO_DIR / f"{h}.mp3"

        if path.exists():
            _bump("cache_hit")
            return self._payload_for(h, text)

        _bump("cache_miss")
        ok, error = _fetch_elevenlabs(text, vid, settings or {}, path)
        if not ok:
            _bump("fallback")
            log.warning("elevenlabs render failed (%s); falling back to Polly", error)
            return PollyProvider().render(text, lang)

        _bump("elevenlabs")
        return self._payload_for(h, text)

    def _payload_for(self, h: str, text: str) -> TtsPayload:
        base = (os.environ.get("PUBLIC_BASE_URL", "") or "").rstrip("/")
        # If PUBLIC_BASE_URL is unset, return a relative URL — Twilio
        # rejects relative URLs in <Play> so we'll fall back instead.
        if not base:
            log.warning("PUBLIC_BASE_URL unset; cannot serve ElevenLabs audio")
            _bump("fallback")
            return PollyProvider().render(text, "en")
        return TtsPayload(
            kind="play",
            url=f"{base}/audio/{h}.mp3",
            duration_estimate_ms=int(len(text) * 60),
        )


def _fetch_elevenlabs(text: str, voice_id: str, settings: dict,
                      out_path: Path) -> tuple:
    """Synchronous POST to the ElevenLabs streaming endpoint, write the
    response body to disk. Returns (ok, error_string)."""
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    model = os.environ.get("ELEVENLABS_MODEL",
                            ElevenLabsProvider.DEFAULT_MODEL)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = json.dumps({
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": float(settings.get("stability", 0.5)),
            "similarity_boost": float(settings.get("similarity", 0.75)),
        },
    }).encode("utf-8")
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "accept": "audio/mpeg",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read()
        if not data:
            return False, "empty_response"
        out_path.write_bytes(data)
        return True, None
    except urllib.error.HTTPError as e:
        return False, f"http_{e.code}"
    except urllib.error.URLError as e:
        return False, f"url_error:{e.reason}"
    except Exception as e:
        return False, type(e).__name__


# ── Provider resolution ──────────────────────────────────────────────

_PROVIDERS: dict = {
    "polly": PollyProvider,
    "elevenlabs": ElevenLabsProvider,
}


def resolve_provider(client: Optional[dict]) -> TtsProvider:
    """Pick the provider for this tenant. Defaults to Polly; falls back
    to Polly if the tenant chose an unknown provider."""
    name = ((client or {}).get("tts_provider") or "polly").lower().strip()
    cls = _PROVIDERS.get(name, PollyProvider)
    return cls()


def voice_id_for(client: Optional[dict]) -> Optional[str]:
    """Per-tenant voice id override, falling back to provider default."""
    if not client:
        return None
    return (client.get("tts_voice_id") or "").strip() or None


def voice_settings_for(client: Optional[dict]) -> dict:
    s = (client or {}).get("tts_voice_settings")
    if isinstance(s, dict):
        return s
    return {}


def render(text: str, *, client: Optional[dict] = None,
           lang: str = "en") -> TtsPayload:
    """Convenience wrapper: pick provider, render. Always returns a
    valid payload — never raises."""
    if not (text or "").strip():
        return TtsPayload(kind="polly", text="", polly_voice=_polly_voice_for_lang(lang))
    provider = resolve_provider(client)
    try:
        return provider.render(
            text, lang=lang,
            voice_id=voice_id_for(client),
            settings=voice_settings_for(client),
        )
    except Exception as e:
        log.error("tts render failed (%s); falling back to Polly: %s",
                  provider.name, e)
        _bump("fallback")
        return PollyProvider().render(text, lang)


# ── /audio/<hash>.mp3 server ─────────────────────────────────────────

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

audio_router = APIRouter(tags=["audio"])


@audio_router.get("/audio/{filename}")
def serve_audio(filename: str):
    """Serve a cached audio file. Filenames are sha256[:24]+.mp3 — no
    path traversal possible."""
    if not filename.endswith(".mp3"):
        raise HTTPException(404, "not found")
    name = filename[:-4]
    if not name.isalnum() or len(name) > 64:
        raise HTTPException(404, "not found")
    path = _AUDIO_DIR / f"{name}.mp3"
    if not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="audio/mpeg")
