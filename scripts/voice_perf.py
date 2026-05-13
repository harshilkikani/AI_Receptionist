"""V8.6 — voice-perf benchmark.

Simulates a 5-turn signed Twilio call through the live tunnel and
reports per-turn latency, TwiML body shape (Play vs Say), and
tts.render_stats deltas. The whole point is to make perceptual
improvements MEASURABLE so we can verify each v8 change before
moving on.

Usage:
    python scripts/voice_perf.py
    python scripts/voice_perf.py --turns 7
    python scripts/voice_perf.py --reset-stats  # clear tts counters first
    python scripts/voice_perf.py --json         # machine output for diffs
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
import sys
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass


def _resolve_tunnel_url() -> str:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base:
        return base
    hint = _ROOT / "data" / "tunnel_url.txt"
    if hint.exists():
        return hint.read_text(encoding="utf-8").strip().rstrip("/")
    raise RuntimeError("no tunnel URL — set PUBLIC_BASE_URL or run reclaim_tunnel.py")


def _signer():
    from twilio.request_validator import RequestValidator
    tok = os.environ.get("TWILIO_AUTH_TOKEN")
    if not tok:
        raise RuntimeError("TWILIO_AUTH_TOKEN not set")
    return RequestValidator(tok)


def _twiml_kind(body: str) -> str:
    """Classify the TwiML — does the caller hear ElevenLabs Play or Polly Say?"""
    has_play = "<Play>" in body
    has_say = "<Say " in body or "<Say>" in body
    if has_play and not has_say:
        return "play (ElevenLabs)"
    if has_say and not has_play:
        return "say (Polly fallback)"
    if has_play and has_say:
        return "MIXED ✗"   # this is the v8.1 bug signal
    return "no-audio"


def _post(url_root: str, sig_fn, path: str, params: dict) -> dict:
    full_url = url_root + path
    sig = sig_fn(full_url, params)
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        full_url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "X-Twilio-Signature": sig})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = r.read().decode("utf-8", errors="replace")
    return {
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "size_b": len(resp),
        "twiml_kind": _twiml_kind(resp),
        "body_tail": resp[-200:].strip(),
    }


def _fetch_render_stats(url_root: str) -> dict:
    """Hit /admin/diagnose.json (no auth in dev) to read tts.render_stats
    via the diagnose payload — or fall back to importing tts directly."""
    # Direct import: faster + no auth headache
    try:
        from src import tts
        return tts.render_stats()
    except Exception as e:
        return {"error": str(e)}


def _reset_render_stats():
    try:
        from src import tts
        tts.reset_stats()
    except Exception:
        pass


_TURNS_DEFAULT = [
    ("yeah hi my ac stopped working last night and the house is getting hot",
     "summer-weekend HVAC call"),
    ("I'm at 412 maple street apartment B and what is it going to run me",
     "address + price ask"),
    ("okay let's get it on the calendar my number is the one I'm calling from",
     "scheduling closer"),
    ("when can you get someone out actually we're heading out at 3",
     "time-constraint follow-up"),
    ("yeah that works thanks talk soon",
     "wrap-up"),
]


def run(turns: int = 5, reset: bool = False, as_json: bool = False) -> dict:
    if reset:
        _reset_render_stats()
    url_root = _resolve_tunnel_url()
    signer = _signer()
    sig_fn = signer.compute_signature
    call_sid = f"CA_perf_{int(time.time())}"

    results = {
        "url_root": url_root,
        "call_sid": call_sid,
        "turns": [],
    }

    # Setup: incoming + setlang
    for path, params, label in [
        ("/voice/incoming",
         {"From": "+15555550199", "To": "+18449403274", "CallSid": call_sid},
         "incoming (new caller)"),
        ("/voice/setlang",
         {"From": "+15555550199", "To": "+18449403274", "Digits": "1"},
         "setlang (greeting)"),
    ]:
        r = _post(url_root, sig_fn, path, params)
        r["label"] = label
        results["turns"].append(r)

    # Gather turns
    speech = _TURNS_DEFAULT[: max(1, min(turns, len(_TURNS_DEFAULT)))]
    for i, (phrase, label) in enumerate(speech, 1):
        params = {"From": "+15555550199", "To": "+18449403274",
                  "CallSid": call_sid, "SpeechResult": phrase,
                  "Language": "en-US"}
        r = _post(url_root, sig_fn, "/voice/gather", params)
        r["label"] = f"gather #{i} — {label}"
        r["speech"] = phrase
        results["turns"].append(r)

    results["render_stats_after"] = _fetch_render_stats(url_root)

    # Summary metrics
    gathers = [t for t in results["turns"] if "gather" in t["label"]]
    if gathers:
        lats = [t["latency_ms"] for t in gathers]
        results["summary"] = {
            "gather_count": len(gathers),
            "gather_avg_ms": int(sum(lats) / len(lats)),
            "gather_max_ms": max(lats),
            "gather_min_ms": min(lats),
            "kind_counts": {
                k: sum(1 for t in gathers if t["twiml_kind"] == k)
                for k in set(t["twiml_kind"] for t in gathers)
            },
        }

    if as_json:
        print(json.dumps(results, indent=2))
    else:
        print(f"\n=== Voice perf — {url_root} ===")
        print(f"call_sid={call_sid}\n")
        for t in results["turns"]:
            kind = t["twiml_kind"]
            marker = " ✗" if "MIXED" in kind else ""
            print(f"  [{t['label']:42}]  {t['latency_ms']:5} ms   "
                  f"{t['size_b']:>5}B   {kind}{marker}")
        if "summary" in results:
            s = results["summary"]
            print()
            print(f"  gather turns:  {s['gather_count']}")
            print(f"  avg latency:   {s['gather_avg_ms']} ms")
            print(f"  range:         {s['gather_min_ms']}-{s['gather_max_ms']} ms")
            print(f"  twiml shape:   {s['kind_counts']}")
        stats = results["render_stats_after"]
        if "error" not in stats:
            print(f"\n  render_stats: polly={stats.get('polly',0)}  "
                  f"elevenlabs={stats.get('elevenlabs',0)}  "
                  f"cache_hit={stats.get('cache_hit',0)}  "
                  f"cache_miss={stats.get('cache_miss',0)}  "
                  f"fallback={stats.get('fallback',0)}  "
                  f"chars={stats.get('chars_rendered',0)}")
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--turns", type=int, default=5,
                   help="number of gather turns to simulate (max 5)")
    p.add_argument("--reset-stats", action="store_true",
                   help="reset tts.render_stats before run")
    p.add_argument("--json", action="store_true",
                   help="emit JSON instead of human-readable")
    args = p.parse_args()
    run(turns=args.turns, reset=args.reset_stats, as_json=args.json)


if __name__ == "__main__":
    main()
