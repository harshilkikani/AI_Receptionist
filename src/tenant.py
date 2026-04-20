"""Multi-tenant client config loader.

Loads YAML files from ./clients/ and maps inbound Twilio numbers to clients.
All other modules (llm, spam_filter, call_timer, usage) read config via
load_client_by_number() or load_default().

Changes to client YAMLs are picked up on server restart. (No hot-reload —
too much complexity for a demo.)
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

CLIENTS_DIR = Path(__file__).parent.parent / "clients"


def _normalize_phone(phone: str) -> str:
    """Reduce phone to digits, strip US country code if 11 digits starting with 1."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


@lru_cache(maxsize=1)
def _all_clients() -> dict:
    """Load every YAML under clients/ once per process start.
    Returns dict keyed by client id. Filenames starting with _ are reserved
    for templates/defaults and are loaded but not routed by inbound number
    (they won't map since they have empty inbound_number)."""
    result = {}
    if not CLIENTS_DIR.exists():
        return result
    for path in sorted(CLIENTS_DIR.glob("*.yaml")):
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or not isinstance(data, dict):
            continue
        cid = data.get("id")
        if not cid:
            continue
        result[cid] = data
    return result


def reload():
    """Clear the cache. Call from tests or admin endpoints."""
    _all_clients.cache_clear()


def load_default() -> dict:
    """Return the _default.yaml client (fallback when no match)."""
    clients = _all_clients()
    return clients.get("_default") or _fallback_hardcoded_default()


def _fallback_hardcoded_default() -> dict:
    """Used if clients/_default.yaml is missing — keeps the server alive
    with sensible defaults rather than crashing on import."""
    return {
        "id": "_default",
        "name": "this service",
        "owner_name": "the owner",
        "inbound_number": "",
        "escalation_phone": "",
        "services": "general service",
        "pricing_summary": "Pricing varies.",
        "service_area": "local area",
        "hours": "business hours",
        "emergency_keywords": ["flooding", "burst", "gas leak", "fire", "emergency"],
        "plan": {
            "tier": "starter",
            "monthly_price": 0,
            "included_calls": 0,
            "included_minutes": 0,
            "overage_rate_per_call": 0,
            "max_call_duration_seconds": 240,
            "max_call_duration_emergency": 360,
            "voice_tier_main": "premium",
            "voice_tier_transactional": "flash",
            "robocall_gate": False,
            "sms_max_per_call": 3,
        },
        "integrations": {"calendar": None, "crm": None},
        "default_language": "en",
    }


def load_client_by_number(phone: str) -> dict:
    """Return the client config that matches the given inbound (To) number.
    Falls back to default if no match."""
    if not phone:
        return load_default()
    target = _normalize_phone(phone)
    for client in _all_clients().values():
        # IDs starting with _ (e.g. _template, _default) never route
        if (client.get("id") or "").startswith("_"):
            continue
        num = client.get("inbound_number", "") or ""
        if num and _normalize_phone(num) == target:
            return client
    return load_default()


def load_client_by_id(client_id: str) -> Optional[dict]:
    """Return the client config for a given ID, or None if not found."""
    return _all_clients().get(client_id)


def list_all() -> list:
    """All clients (including _default/_template). Used by admin UI."""
    return list(_all_clients().values())
