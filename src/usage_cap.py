"""V3.11 — Hard usage cap per tenant.

When `plan.hard_cap_calls` is set on a tenant's YAML and the current
month's total_calls exceeds it, /voice/incoming returns a polite
"we're at capacity" message and hangs up. Protects the agency from a
runaway client who's blown past their billing tier (especially on a
trial plan).

Feature-flagged + global kill switch aware.
"""
from __future__ import annotations

import os
from typing import Optional


def _enforcement_active() -> bool:
    global_on = os.environ.get("MARGIN_PROTECTION_ENABLED", "true").lower() != "false"
    feature_on = os.environ.get("ENFORCE_USAGE_HARD_CAP", "true").lower() == "true"
    return global_on and feature_on


def cap_for(client: Optional[dict]) -> int:
    """Return the plan's hard_cap_calls (0 = no cap)."""
    if not client:
        return 0
    plan = client.get("plan") or {}
    try:
        return int(plan.get("hard_cap_calls") or 0)
    except (TypeError, ValueError):
        return 0


def is_capped(client: Optional[dict]) -> dict:
    """Return {capped, current, cap, enforcement_active}.

    `capped=True` only when enforcement is active AND current calls >= cap.
    In shadow mode we report the same `current` + `cap` so the admin
    can see how close a tenant is to the edge.
    """
    if not client:
        return {"capped": False, "current": 0, "cap": 0,
                "enforcement_active": False}
    from src import usage
    cap = cap_for(client)
    enforce = _enforcement_active()
    if cap <= 0:
        return {"capped": False, "current": 0, "cap": 0,
                "enforcement_active": enforce}
    summary = usage.monthly_summary(client["id"])
    current = int(summary.get("total_calls") or 0)
    return {
        "capped": enforce and (current >= cap),
        "current": current,
        "cap": cap,
        "enforcement_active": enforce,
    }


def capped_message(client: dict) -> str:
    """Polite caller message when the cap fires."""
    name = (client or {}).get("name") or "our office"
    return (
        f"Thanks for calling {name}. We've reached this month's call "
        f"capacity — please call back tomorrow, or leave a message with "
        f"your name and number and we'll follow up."
    )
