"""V6.3 -- preflight diagnostic.

Single command (`python -m src.preflight` or GET /admin/diagnose) that
reports red/yellow/green status for every prerequisite the live demo
needs. Catches misconfigs BEFORE Twilio plays "application error" to a
real caller.

Checks (each can be 'ok' | 'warn' | 'fail'):
  - ANTHROPIC_API_KEY present (and optionally pinged)
  - TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN present (and optionally
    validated against api.twilio.com)
  - TWILIO_VERIFY_SIGNATURES sanity
  - PUBLIC_BASE_URL present, reachable shape
  - ADMIN_USER + ADMIN_PASS present
  - CLIENT_PORTAL_SECRET present + min length
  - clients/*.yaml count + at least one tenant with inbound_number
  - data/usage.db writable
  - Twilio webhook URL matches PUBLIC_BASE_URL for the live tenant

Aggregate: 'ok' if no fails, 'warn' if any warns, 'fail' if any fails.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("preflight")

_ROOT = Path(__file__).parent.parent


@dataclass
class Check:
    name: str
    status: str   # 'ok' | 'warn' | 'fail'
    message: str
    detail: Optional[str] = None


def _ok(name, msg, detail=None):
    return Check(name=name, status="ok", message=msg, detail=detail)


def _warn(name, msg, detail=None):
    return Check(name=name, status="warn", message=msg, detail=detail)


def _fail(name, msg, detail=None):
    return Check(name=name, status="fail", message=msg, detail=detail)


# ── Individual checks ───────────────────────────────────────────────────

def check_anthropic_key(*, ping: bool = False) -> Check:
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return _fail(
            "ANTHROPIC_API_KEY",
            "missing -- voice calls will hit the v6.2 failsafe TwiML",
            detail="Set ANTHROPIC_API_KEY in .env",
        )
    if not key.startswith("sk-ant-"):
        return _warn(
            "ANTHROPIC_API_KEY",
            f"set but doesn't look like a real key (got prefix {key[:7]!r})",
        )
    if not ping:
        return _ok("ANTHROPIC_API_KEY",
                   f"set (sk-ant-...{key[-4:]}); not pinged",
                   detail="Run with --ping to verify against the API")
    # Optional live ping -- 1-token "hi"
    try:
        import anthropic
        c = anthropic.Anthropic(api_key=key)
        c.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        return _ok("ANTHROPIC_API_KEY",
                   "valid; API responded to a 1-token ping")
    except Exception as e:
        return _fail("ANTHROPIC_API_KEY",
                     f"API rejected ping: {type(e).__name__}: {e}")


def check_twilio_creds(*, ping: bool = False) -> Check:
    sid = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
    tok = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
    if not sid or not tok:
        return _fail(
            "TWILIO_CREDENTIALS",
            "missing -- signature verification + outbound SMS off",
            detail="Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in .env",
        )
    if not sid.startswith("AC"):
        return _warn("TWILIO_CREDENTIALS",
                     f"SID doesn't start with 'AC': {sid[:6]}...")
    if not ping:
        return _ok("TWILIO_CREDENTIALS",
                   f"set (AC...{sid[-4:]}); not pinged",
                   detail="Run with --ping to verify against Twilio API")
    try:
        import base64
        auth = base64.b64encode(f"{sid}:{tok}".encode()).decode()
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
            headers={"Authorization": f"Basic {auth}"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            if r.status == 200:
                return _ok("TWILIO_CREDENTIALS",
                           "valid; api.twilio.com accepted the auth")
            return _fail("TWILIO_CREDENTIALS",
                         f"unexpected response {r.status}")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return _fail("TWILIO_CREDENTIALS",
                         "Twilio rejected the auth (401)")
        return _fail("TWILIO_CREDENTIALS", f"HTTP error {e.code}")
    except Exception as e:
        return _warn("TWILIO_CREDENTIALS",
                     f"couldn't reach Twilio: {type(e).__name__}: {e}")


def check_signature_mode() -> Check:
    raw = (os.environ.get("TWILIO_VERIFY_SIGNATURES") or "").strip().lower()
    if raw not in ("true", "false", ""):
        return _warn("TWILIO_VERIFY_SIGNATURES",
                     f"unexpected value {raw!r}; treating as true")
    enforced = raw != "false"   # default true
    if enforced:
        return _ok("TWILIO_VERIFY_SIGNATURES",
                   "enforced (recommended for production)")
    return _warn("TWILIO_VERIFY_SIGNATURES",
                 "shadow mode -- forged webhooks would be accepted",
                 detail="Flip to 'true' once webhooks are confirmed shaped right")


def check_public_base_url() -> Check:
    url = (os.environ.get("PUBLIC_BASE_URL") or "").strip()
    if not url:
        return _fail(
            "PUBLIC_BASE_URL",
            "unset -- Twilio signature verification + ElevenLabs <Play> "
            "URLs both need it",
            detail="Set to your cloudflared tunnel URL "
                   "(scripts/reclaim_tunnel.py prints it)",
        )
    if not url.startswith(("http://", "https://")):
        return _fail("PUBLIC_BASE_URL",
                     f"doesn't look like a URL: {url[:48]!r}")
    if url.startswith("http://"):
        return _warn("PUBLIC_BASE_URL",
                     "http (not https) -- Twilio webhook is fine but "
                     "cloudflared usually provides https")
    return _ok("PUBLIC_BASE_URL", url)


def check_admin_creds() -> Check:
    user = (os.environ.get("ADMIN_USER") or "").strip()
    pw = (os.environ.get("ADMIN_PASS") or "").strip()
    if not user and not pw:
        return _warn(
            "ADMIN_CREDENTIALS",
            "unset -- /admin is open to anyone with the URL",
            detail="Safe for localhost; set before exposing via tunnel",
        )
    if not user or not pw:
        return _fail("ADMIN_CREDENTIALS",
                     "only one of ADMIN_USER / ADMIN_PASS set")
    if len(pw) < 8:
        return _warn("ADMIN_CREDENTIALS",
                     f"password is {len(pw)} chars -- short")
    return _ok("ADMIN_CREDENTIALS", "set")


def check_portal_secret() -> Check:
    s = (os.environ.get("CLIENT_PORTAL_SECRET") or "").strip()
    if not s:
        return _fail(
            "CLIENT_PORTAL_SECRET",
            "unset -- /client/{id}?t=... tokens cannot be issued",
            detail="Set to 32+ random chars; rotate by changing the value",
        )
    if len(s) < 16:
        return _warn("CLIENT_PORTAL_SECRET",
                     f"only {len(s)} chars -- recommend 32+")
    return _ok("CLIENT_PORTAL_SECRET", f"set ({len(s)} chars)")


def check_tenants() -> Check:
    """Need at least one tenant with an inbound_number for live demo
    routing."""
    try:
        from src import tenant
        clients = [c for c in tenant.list_all()
                   if not (c.get("id") or "").startswith("_")]
    except Exception as e:
        return _fail("TENANTS",
                     f"failed to load clients/*.yaml: {type(e).__name__}: {e}")
    routable = [c for c in clients if (c.get("inbound_number") or "").strip()]
    if not clients:
        return _fail("TENANTS", "no clients/*.yaml found")
    if not routable:
        return _warn("TENANTS",
                     f"{len(clients)} tenants, none with inbound_number")
    names = ", ".join(f"{c['id']}->{c.get('inbound_number')}" for c in routable)
    return _ok("TENANTS",
               f"{len(routable)}/{len(clients)} routable",
               detail=names)


def check_usage_db_writable() -> Check:
    try:
        from src import usage
        usage._ensure_parent_dir()
        conn = sqlite3.connect(usage.DB_PATH, isolation_level=None)
        conn.execute("CREATE TABLE IF NOT EXISTS _preflight_probe (x INTEGER)")
        conn.execute("DROP TABLE _preflight_probe")
        conn.close()
        return _ok("USAGE_DB", f"writable at {usage.DB_PATH}")
    except Exception as e:
        return _fail("USAGE_DB",
                     f"can't write: {type(e).__name__}: {e}")


def check_twilio_webhook_url(*, ping: bool = False) -> Check:
    """If PUBLIC_BASE_URL is set + Twilio creds valid, compare the URL
    Twilio has configured for our live number against PUBLIC_BASE_URL.
    Mismatched = the live demo silently broken."""
    if not ping:
        return _ok("TWILIO_WEBHOOK_URL",
                   "skipped (use --ping to compare against Twilio API)")
    base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    sid = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
    tok = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
    if not base or not sid or not tok:
        return _warn(
            "TWILIO_WEBHOOK_URL",
            "can't check -- needs PUBLIC_BASE_URL + Twilio creds")
    try:
        from src import tenant
        routable = [c for c in tenant.list_all()
                    if (c.get("inbound_number") or "").strip()
                    and not (c.get("id") or "").startswith("_")]
    except Exception as e:
        return _warn("TWILIO_WEBHOOK_URL",
                     f"can't load tenants: {e}")
    if not routable:
        return _warn("TWILIO_WEBHOOK_URL",
                     "no routable tenants to check")

    import base64
    auth = base64.b64encode(f"{sid}:{tok}".encode()).decode()
    expected_root = f"{base}/voice/incoming"
    mismatches = []
    for client in routable:
        num = client.get("inbound_number") or ""
        try:
            req = urllib.request.Request(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
                f"/IncomingPhoneNumbers.json?PhoneNumber={num}",
                headers={"Authorization": f"Basic {auth}"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
            entries = data.get("incoming_phone_numbers") or []
            if not entries:
                mismatches.append(f"{num}: not in Twilio account")
                continue
            configured = entries[0].get("voice_url") or ""
            if configured.rstrip("/").startswith(base.rstrip("/")):
                continue
            mismatches.append(
                f"{num}: Twilio has {configured!r}, expected prefix {base!r}")
        except Exception as e:
            mismatches.append(f"{num}: lookup failed ({type(e).__name__})")
    if mismatches:
        return _fail("TWILIO_WEBHOOK_URL",
                     "Twilio webhook URL drift -- run scripts/reclaim_tunnel.py",
                     detail="; ".join(mismatches))
    return _ok("TWILIO_WEBHOOK_URL",
               f"all routable numbers point at {expected_root}")


# ── Aggregate ──────────────────────────────────────────────────────────

def run_all(*, ping: bool = False) -> dict:
    """Run every check. Returns
       {summary: 'ok'|'warn'|'fail', counts: {...}, checks: [Check, ...]}"""
    checks = [
        check_anthropic_key(ping=ping),
        check_twilio_creds(ping=ping),
        check_signature_mode(),
        check_public_base_url(),
        check_admin_creds(),
        check_portal_secret(),
        check_tenants(),
        check_usage_db_writable(),
        check_twilio_webhook_url(ping=ping),
    ]
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    summary = ("fail" if counts["fail"]
               else "warn" if counts["warn"]
               else "ok")
    return {
        "summary": summary,
        "counts": counts,
        "checks": [asdict(c) for c in checks],
    }


# ── CLI ────────────────────────────────────────────────────────────────

_COLORS = {
    "ok":   "\033[32m",   # green
    "warn": "\033[33m",   # yellow
    "fail": "\033[31m",   # red
    "_off": "\033[0m",
}

_SYMBOLS = {"ok": "[OK]", "warn": "[!!]", "fail": "[X]"}


def _render(result: dict, color: bool = True) -> str:
    lines = []
    for c in result["checks"]:
        sym = _SYMBOLS.get(c["status"], "?")
        if color:
            col = _COLORS.get(c["status"], "")
            off = _COLORS["_off"]
            lines.append(f"  {col}{sym}{off} {c['name']:<28} {c['message']}")
        else:
            lines.append(f"  {sym} {c['name']:<28} {c['message']}")
        if c["detail"]:
            lines.append(f"      {c['detail']}")
    summary = result["summary"]
    counts = result["counts"]
    if color:
        col = _COLORS.get(summary, "")
        off = _COLORS["_off"]
        header = (f"{col}preflight {summary.upper()}{off}  "
                  f"({counts['ok']} ok, {counts['warn']} warn, "
                  f"{counts['fail']} fail)")
    else:
        header = (f"preflight {summary.upper()}  "
                  f"({counts['ok']} ok, {counts['warn']} warn, "
                  f"{counts['fail']} fail)")
    return header + "\n" + "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="Verify the AI receptionist's live-demo prerequisites.")
    p.add_argument("--ping", action="store_true",
                   help="Also ping Anthropic + Twilio APIs (uses tokens)")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON instead of human-readable output")
    p.add_argument("--no-color", action="store_true",
                   help="Suppress ANSI color codes")
    args = p.parse_args(argv)
    result = run_all(ping=args.ping)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(_render(result, color=not args.no_color))
    return 0 if result["summary"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
