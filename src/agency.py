"""V3.9 — Agency-tier tenancy.

Agencies are resellers who manage multiple client tenants on the same
platform. An `agencies/<agency_id>.yaml` file carries:

    id: acme_ai
    name: "Acme AI Agency"
    owned_clients:
      - ace_hvac
      - septic_pro
    contact_email: "ops@acme.example"

Admin UI exposes `/admin/agency/{agency_id}` — same overview + calls
views as the root admin, but scoped to that agency's clients.

Root admin (no agency filter) still sees everything. Full auth
hierarchy with per-agency basic auth creds is out of scope here;
current shape expects a trusted operator to drill down by URL.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

_AGENCIES_DIR = Path(__file__).parent.parent / "agencies"


@lru_cache(maxsize=1)
def _all_agencies() -> dict:
    """Load every YAML under agencies/. Cached per process."""
    result = {}
    if not _AGENCIES_DIR.exists():
        return result
    for path in sorted(_AGENCIES_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not data or not isinstance(data, dict):
            continue
        aid = data.get("id")
        if not aid:
            continue
        result[aid] = data
    return result


def reload():
    _all_agencies.cache_clear()


def list_agencies() -> list:
    return list(_all_agencies().values())


def get_agency(agency_id: str) -> Optional[dict]:
    return _all_agencies().get(agency_id)


def clients_for_agency(agency_id: str) -> list:
    """Return the list of client_id strings this agency owns.
    Empty if the agency doesn't exist."""
    a = get_agency(agency_id)
    if not a:
        return []
    return list(a.get("owned_clients") or [])


def agency_owns_client(agency_id: str, client_id: str) -> bool:
    return client_id in clients_for_agency(agency_id)


def agency_for_client(client_id: str) -> Optional[str]:
    """Reverse lookup — which agency owns this client (if any)."""
    for a in _all_agencies().values():
        if client_id in (a.get("owned_clients") or []):
            return a.get("id")
    return None
