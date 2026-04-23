"""Interactive onboarding for a new tenant.

Two entry points:
    python -m src.onboarding new         — full interactive Q&A
    python -m src.onboarding new-demo    — short-form disposable tenant

The wizard writes `clients/<id>.yaml` and prints:
  1. Any `.env` changes needed (missing secrets)
  2. Twilio webhook URLs to set (reads PUBLIC_BASE_URL or the tunnel
     hint file; else prints a placeholder)
  3. Client portal URL with a fresh signed token (if CLIENT_PORTAL_SECRET
     is set; otherwise explains how)
  4. A test curl command to verify tenant routing

Designed so tests can drive it headless: all input() / print() calls
route through injectable readers/writers.
"""
from __future__ import annotations

import argparse
import os
import random
import re
import secrets
import string
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import yaml

from src import tenant

_ROOT = Path(__file__).parent.parent
CLIENTS_DIR = _ROOT / "clients"
_TUNNEL_HINT = _ROOT / "data" / "tunnel_url.txt"


# ── validators ─────────────────────────────────────────────────────────

_E164 = re.compile(r"^\+\d{7,15}$")
_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


def v_nonempty(value: str) -> Optional[str]:
    if not (value or "").strip():
        return "value is required"
    return None


def v_e164(value: str) -> Optional[str]:
    if not value:
        return None  # optional by default; caller decides required-ness
    if not _E164.match(value):
        return "expected E.164 format like +17175551234"
    return None


def v_e164_required(value: str) -> Optional[str]:
    if not value:
        return "value is required (E.164, e.g. +17175551234)"
    return v_e164(value)


def v_snake_id(value: str) -> Optional[str]:
    if not value:
        return "id is required"
    if not _ID.match(value):
        return "id must be snake_case: a-z, 0-9, underscores, start with a letter"
    return None


def v_positive_number(value: str) -> Optional[str]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "expected a number"
    if f < 0:
        return "must be ≥ 0"
    return None


def v_timezone(value: str) -> Optional[str]:
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError:  # pragma: no cover
        return None
    try:
        ZoneInfo(value)
    except Exception:
        return "unknown timezone (try America/New_York, Europe/London, etc.)"
    return None


# ── I/O plumbing ───────────────────────────────────────────────────────

def _ask(prompt: str, *, default: Optional[str] = None,
         validator: Optional[Callable[[str], Optional[str]]] = None,
         reader: Callable[[str], str] = input,
         writer: Callable[..., None] = print) -> str:
    label = f"{prompt}"
    if default is not None:
        label += f" [{default}]"
    label += ": "
    while True:
        raw = reader(label)
        val = (raw or "").strip()
        if not val and default is not None:
            val = default
        if validator:
            err = validator(val)
            if err:
                writer(f"  ! {err}", file=sys.stderr)
                continue
        return val


# ── core wizard ────────────────────────────────────────────────────────

def _default_demo_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    tail = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    return f"demo_{stamp}_{tail}"


def _collect_full(reader: Callable[[str], str],
                  writer: Callable[..., None]) -> dict:
    """Interactive Q&A for a full tenant. Returns the config dict."""
    w = writer
    w("\n── New tenant — 10 quick questions ──\n")

    cid = _ask("Client ID (snake_case, short)", validator=v_snake_id,
               reader=reader, writer=w)
    # Check collision
    existing = CLIENTS_DIR / f"{cid}.yaml"
    if existing.exists():
        raise FileExistsError(f"{existing} already exists — pick a different id "
                              "or delete the old file first.")

    name = _ask("Company name", validator=v_nonempty, reader=reader, writer=w)
    owner_name = _ask("Owner first name (or 'the owner')",
                      default="the owner", reader=reader, writer=w)
    owner_email = _ask("Owner email (for invoices + digest fallback; enter to skip)",
                       default="", reader=reader, writer=w)
    owner_cell = _ask("Owner cell (E.164, for emergency SMS + digest; enter to skip)",
                      default="", validator=v_e164, reader=reader, writer=w)
    timezone_val = _ask("Timezone (IANA; e.g. America/New_York)",
                        default="America/New_York",
                        validator=v_timezone, reader=reader, writer=w)
    inbound = _ask("Twilio inbound number (E.164, e.g. +18449403274)",
                   validator=v_e164_required, reader=reader, writer=w)
    escalation = _ask("Escalation phone (E.164, where emergencies transfer)",
                      validator=v_e164_required, reader=reader, writer=w)
    services = _ask("Services (one line)", validator=v_nonempty,
                    reader=reader, writer=w)
    pricing = _ask("Pricing summary (one line)", default="Pricing varies",
                   reader=reader, writer=w)
    area = _ask("Service area", default="local area", reader=reader, writer=w)
    hours = _ask("Hours", default="Mon-Fri 8am-5pm", reader=reader, writer=w)
    emergency_kws_raw = _ask(
        "Emergency keywords (comma separated)",
        default="flooding,burst,gas leak,no heat,fire",
        reader=reader, writer=w,
    )
    emergency_kws = [k.strip().lower() for k in emergency_kws_raw.split(",") if k.strip()]
    tier = _ask("Plan tier (starter/pro/enterprise)", default="starter",
                reader=reader, writer=w)
    monthly_price = float(_ask("Monthly plan price USD", default="297",
                               validator=v_positive_number,
                               reader=reader, writer=w))
    included_calls = int(float(_ask("Included calls per month", default="250",
                                    validator=v_positive_number,
                                    reader=reader, writer=w)))
    included_minutes = int(float(_ask("Included minutes per month", default="500",
                                      validator=v_positive_number,
                                      reader=reader, writer=w)))
    overage_rate = float(_ask("Overage rate per call (USD)", default="0.75",
                              validator=v_positive_number,
                              reader=reader, writer=w))
    language = _ask("Default language (en/es/hi/gu/...)", default="en",
                    reader=reader, writer=w)

    return {
        "id": cid,
        "name": name,
        "owner_name": owner_name,
        "owner_email": owner_email,
        "owner_cell": owner_cell,
        "timezone": timezone_val,
        "inbound_number": inbound,
        "escalation_phone": escalation,
        "services": services,
        "pricing_summary": pricing,
        "service_area": area,
        "hours": hours,
        "emergency_keywords": emergency_kws,
        "plan": {
            "tier": tier,
            "monthly_price": monthly_price,
            "included_calls": included_calls,
            "included_minutes": included_minutes,
            "overage_rate_per_call": overage_rate,
            "max_call_duration_seconds": 240,
            "max_call_duration_emergency": 360,
            "voice_tier_main": "premium",
            "voice_tier_transactional": "flash",
            "robocall_gate": False,
            "sms_max_per_call": 3,
        },
        "integrations": {"calendar": None, "crm": None},
        "default_language": language,
    }


def _build_demo(client_id: Optional[str] = None) -> dict:
    """Disposable demo tenant — random id, 1-day expiry."""
    cid = client_id or _default_demo_id()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=1)
    return {
        "id": cid,
        "name": "Demo Prospect",
        "owner_name": "the owner",
        "owner_email": "",
        "owner_cell": "",
        "timezone": "America/New_York",
        "inbound_number": "",          # operator fills in the real number
        "escalation_phone": "",
        "services": "HVAC, plumbing, general home services",
        "pricing_summary": "Service call $129. Install quotes vary by size.",
        "service_area": "local area",
        "hours": "Mon-Fri 8am-5pm, emergency 24/7",
        "emergency_keywords": ["flooding", "burst", "gas leak", "no heat", "fire"],
        "plan": {
            "tier": "starter", "monthly_price": 297,
            "included_calls": 250, "included_minutes": 500,
            "overage_rate_per_call": 0.75,
            "max_call_duration_seconds": 240, "max_call_duration_emergency": 360,
            "voice_tier_main": "premium", "voice_tier_transactional": "flash",
            "robocall_gate": False, "sms_max_per_call": 3,
        },
        "integrations": {"calendar": None, "crm": None},
        "default_language": "en",
        "demo": True,
        "demo_expires_ts": int(expires.timestamp()),
    }


# ── expiry / purge ─────────────────────────────────────────────────────

def purge_expired_demos(now: Optional[datetime] = None) -> list:
    """Move expired demo YAMLs to clients/_expired/. Returns list of removed ids."""
    now = now or datetime.now(timezone.utc)
    removed = []
    archive = CLIENTS_DIR / "_expired"
    archive.mkdir(exist_ok=True)
    for p in CLIENTS_DIR.glob("*.yaml"):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if not data.get("demo"):
            continue
        exp = int(data.get("demo_expires_ts") or 0)
        if exp and exp < int(now.timestamp()):
            dst = archive / p.name
            p.replace(dst)
            removed.append(data.get("id") or p.stem)
    if removed:
        tenant.reload()
    return removed


# ── writer ─────────────────────────────────────────────────────────────

def _write_yaml(config: dict) -> Path:
    CLIENTS_DIR.mkdir(exist_ok=True)
    path = CLIENTS_DIR / f"{config['id']}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    tenant.reload()
    return path


# ── follow-up printouts ────────────────────────────────────────────────

def _detect_base_url() -> str:
    """Prefer PUBLIC_BASE_URL env, else scripts/reclaim_tunnel.py's hint file,
    else a placeholder."""
    env = os.environ.get("PUBLIC_BASE_URL")
    if env:
        return env.rstrip("/")
    if _TUNNEL_HINT.exists():
        try:
            url = _TUNNEL_HINT.read_text(encoding="utf-8").strip()
            if url.startswith("http"):
                return url.rstrip("/")
        except OSError:
            pass
    return "<your-public-base-url>"


def _followup_text(config: dict) -> str:
    base = _detect_base_url()
    cid = config["id"]
    lines = [
        f"Wrote clients/{cid}.yaml",
        "",
        "1) Required .env values (add any missing):",
        "   CLIENT_PORTAL_SECRET=<32+ random chars>",
        "   PUBLIC_BASE_URL=" + base,
        "   (Already-set keys like ANTHROPIC_API_KEY / TWILIO_* remain unchanged.)",
        "",
        "2) Twilio webhook URLs to set on the inbound number:",
        f"   Voice URL:        {base}/voice/incoming  (POST)",
        f"   Status callback:  {base}/voice/status    (POST)",
        f"   Messaging URL:    {base}/sms/incoming    (POST)",
        "",
    ]

    # Client portal URL (only if secret set)
    if os.environ.get("CLIENT_PORTAL_SECRET"):
        try:
            from src import client_portal
            url = client_portal.portal_url(cid, base_url=base if base.startswith("http") else None)
            lines.extend([
                "3) Client portal URL (send this to the client):",
                f"   {url}",
                "",
            ])
        except Exception as e:
            lines.extend([
                f"3) Client portal URL unavailable: {e}",
                "   Run `python -m src.client_portal issue " + cid + "` later.",
                "",
            ])
    else:
        lines.extend([
            "3) Client portal URL unavailable: CLIENT_PORTAL_SECRET is not set.",
            "   Set it in .env, then run:",
            f"   python -m src.client_portal issue {cid}",
            "",
        ])

    inbound = config.get("inbound_number") or "<inbound>"
    lines.extend([
        "4) Verify tenant routing with:",
        f"   python -c \"from src import tenant; tenant.reload(); "
        f"print(tenant.load_client_by_number('{inbound}')['id'])\"",
        "",
        "5) Curl sanity check once the server is running:",
        f"   curl -X POST {base}/voice/incoming \\",
        f'       -d "From=+15555550123" -d "To={inbound}" -d "CallSid=CA_check_{cid}"',
    ])
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────

def _cli(argv: Optional[list] = None,
         reader: Callable[[str], str] = input,
         writer: Callable[..., None] = print) -> int:
    p = argparse.ArgumentParser(prog="python -m src.onboarding")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new", help="interactive full onboarding")
    nd = sub.add_parser("new-demo", help="one-day disposable tenant for sales demos")
    nd.add_argument("--id", default=None, help="override demo id (default: random)")
    sub.add_parser("purge-expired", help="move expired demo YAMLs to clients/_expired/")
    w = sub.add_parser("welcome", help="send the welcome SMS to a client's owner_cell")
    w.add_argument("client_id")
    w.add_argument("--to", default=None,
                   help="override owner_cell (useful when rehearsing)")
    w.add_argument("--dry-run", action="store_true",
                   help="print the body but don't actually send")
    args = p.parse_args(argv)

    if args.cmd == "new":
        try:
            config = _collect_full(reader=reader, writer=writer)
        except FileExistsError as e:
            writer(str(e), file=sys.stderr)
            return 2
        path = _write_yaml(config)
        writer(_followup_text(config))
        return 0

    if args.cmd == "new-demo":
        config = _build_demo(args.id)
        path = _write_yaml(config)
        writer(f"Created demo tenant: clients/{config['id']}.yaml "
               f"(expires in 24h)")
        writer(_followup_text(config))
        return 0

    if args.cmd == "purge-expired":
        removed = purge_expired_demos()
        if removed:
            writer("Purged expired demo tenants: " + ", ".join(removed))
        else:
            writer("No expired demo tenants to purge.")
        return 0

    if args.cmd == "welcome":
        client = tenant.load_client_by_id(args.client_id)
        if client is None or (client.get("id") or "").startswith("_"):
            writer(f"Unknown or reserved client: {args.client_id}",
                   file=sys.stderr)
            return 2
        from src import owner_commands
        body = owner_commands.build_welcome_body(client)
        to = args.to or client.get("owner_cell") or ""
        if args.dry_run or not to:
            writer(f"Would send to: {to or '(no owner_cell set)'}")
            writer("---")
            writer(body)
            return 0 if to else 2
        try:
            import main as _main
            tw = _main._twilio_client()
        except Exception:
            tw = None
        result = owner_commands.send_welcome_sms(
            client, twilio_client=tw, to_override=args.to)
        import json as _json
        writer(_json.dumps({k: v for k, v in result.items() if k != "body"}))
        return 0 if result.get("sent") else 1
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
