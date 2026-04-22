"""P9 — auto-reclaim cloudflared trycloudflare URL and repoint Twilio.

Background: `cloudflared tunnel --url http://localhost:8765` mints a new
random URL on every run. Manually updating Twilio webhooks each time is
a common ops footgun. This script handles it automatically:

  1. Starts cloudflared as a subprocess (or attaches to an already-running
     one via PID, when rerun).
  2. Parses the "Your quick tunnel has been created!" line from its
     stderr stream and extracts the https://<random>.trycloudflare.com URL.
  3. Writes the URL to data/tunnel_url.txt (used by src.onboarding and
     anything else that needs PUBLIC_BASE_URL).
  4. PATCHes every Twilio number listed in any clients/*.yaml::inbound_number
     to point to the new URL's /voice/* + /sms/incoming endpoints.
  5. Blocks for the life of cloudflared, forwarding its output and
     re-running the update if the URL changes mid-session (shouldn't
     happen but we're defensive).

Run:
    python scripts/reclaim_tunnel.py               # defaults to localhost:8765
    python scripts/reclaim_tunnel.py --port 9000
    python scripts/reclaim_tunnel.py --exe ./cloudflared.exe
    python scripts/reclaim_tunnel.py --dry-run     # capture URL only, don't touch Twilio

For a permanent URL, use Option A — Cloudflare Named Tunnel + your own
domain. See ROLLOUT.md for the 3-command setup.
"""
from __future__ import annotations

import argparse
import logging
import os
import platform
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# Make 'from src import ...' work when invoked as a script
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src import tenant  # noqa: E402

log = logging.getLogger("reclaim_tunnel")

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_TUNNEL_HINT = _ROOT / "data" / "tunnel_url.txt"
_DEFAULT_EXE = (
    str(_ROOT / "cloudflared.exe")
    if platform.system() == "Windows"
    else "cloudflared"
)


def extract_url(line: str) -> Optional[str]:
    m = _URL_RE.search(line or "")
    return m.group(0) if m else None


def persist_url(url: str, path: Optional[Path] = None) -> None:
    # Resolve at call time so tests can monkeypatch _TUNNEL_HINT.
    path = path or _TUNNEL_HINT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(url, encoding="utf-8")


def _twilio_client():
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    tok = os.environ.get("TWILIO_AUTH_TOKEN")
    if not (sid and tok):
        return None
    try:
        from twilio.rest import Client
    except ImportError:
        return None
    return Client(sid, tok)


def _tenant_numbers() -> set:
    """E.164 phone numbers we manage (from all non-reserved clients)."""
    nums = set()
    for c in tenant.list_all():
        cid = c.get("id") or ""
        if cid.startswith("_"):
            continue
        num = (c.get("inbound_number") or "").strip()
        if num:
            nums.add(num)
    return nums


def update_twilio_webhooks(base_url: str, tw_client=None,
                           target_numbers: Optional[set] = None) -> dict:
    """PATCH every managed Twilio number to point to base_url. Returns
    `{updated: [...], skipped: [...], errors: [...]}`.

    target_numbers=None → derive from tenant YAMLs. An empty set is a
    legitimate "no targets" signal and is honored as-is."""
    tw_client = tw_client or _twilio_client()
    target = target_numbers if target_numbers is not None else _tenant_numbers()
    result = {"updated": [], "skipped": [], "errors": []}

    if tw_client is None:
        result["skipped"].append("twilio_client_unavailable")
        return result
    if not target:
        result["skipped"].append("no_tenant_numbers")
        return result

    base = base_url.rstrip("/")
    try:
        numbers = tw_client.incoming_phone_numbers.list(limit=200)
    except Exception as e:
        result["errors"].append(f"list_failed:{type(e).__name__}:{e}")
        return result

    for n in numbers:
        pn = getattr(n, "phone_number", None) or ""
        if pn not in target:
            result["skipped"].append(pn)
            continue
        try:
            n.update(
                voice_url=f"{base}/voice/incoming",
                voice_method="POST",
                status_callback=f"{base}/voice/status",
                status_callback_method="POST",
                sms_url=f"{base}/sms/incoming",
                sms_method="POST",
            )
            result["updated"].append(pn)
        except Exception as e:
            result["errors"].append(f"{pn}:{type(e).__name__}:{e}")
    return result


def _spawn_cloudflared(exe: str, local_url: str) -> subprocess.Popen:
    args = [exe, "tunnel", "--no-autoupdate", "--url", local_url]
    log.info("starting cloudflared: %s", " ".join(args))
    return subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # line-buffered
    )


def watch_and_update(proc: subprocess.Popen, *,
                     dry_run: bool = False,
                     on_url: Optional[callable] = None) -> int:
    """Stream cloudflared's output; capture the URL; update Twilio on change."""
    current_url: Optional[str] = None
    assert proc.stdout is not None
    try:
        for raw in iter(proc.stdout.readline, ""):
            line = raw.rstrip()
            if line:
                print(line, flush=True)
            url = extract_url(line)
            if url and url != current_url:
                current_url = url
                persist_url(url)
                log.info("tunnel URL captured: %s", url)
                if on_url:
                    on_url(url)
                if not dry_run:
                    r = update_twilio_webhooks(url)
                    log.info("twilio webhook update: %s", r)
        return proc.wait()
    except KeyboardInterrupt:
        log.info("received Ctrl-C — terminating cloudflared")
        try:
            proc.terminate()
        except Exception:
            pass
        return 130


def _cli(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python scripts/reclaim_tunnel.py")
    p.add_argument("--port", type=int, default=8765,
                   help="local app port cloudflared should expose (default 8765)")
    p.add_argument("--exe", default=_DEFAULT_EXE,
                   help=f"cloudflared binary path (default: {_DEFAULT_EXE})")
    p.add_argument("--dry-run", action="store_true",
                   help="capture URL and write tunnel_url.txt, but don't touch Twilio")
    p.add_argument("--once", action="store_true",
                   help="exit after the first URL is captured (useful for smoke tests)")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    proc = _spawn_cloudflared(args.exe, f"http://localhost:{args.port}")

    captured = {"url": None}

    def _on_url(url):
        captured["url"] = url
        if args.once:
            try:
                proc.terminate()
            except Exception:
                pass

    rc = watch_and_update(proc, dry_run=args.dry_run, on_url=_on_url)
    if captured["url"]:
        log.info("exited with captured URL: %s", captured["url"])
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
