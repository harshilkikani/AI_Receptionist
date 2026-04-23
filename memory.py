"""Simple JSON-file memory store keyed by caller ID (or phone number).

All three channels — web chat, voice, SMS — read/write this same file.
"""

import json
import re
from pathlib import Path
from typing import Optional

MEMORY_FILE = Path(__file__).parent / "memory.json"

SEED = {
    # HVAC seeds — retained for the live ace_hvac tenant and legacy tests
    "sarah": {
        "id": "sarah",
        "name": "Sarah Mitchell",
        "phone": "(415) 555-0142",
        "type": "return",
        "address": "42 Oak Street",
        "equipment": "Carrier 2-stage gas furnace (installed 2019)",
        "notes": "Prefers morning appointments. Has a golden retriever — ring bell.",
        "preview": "Hi, calling about my furnace again...",
        "history": [
            {"date": "Mar 12", "note": "Annual furnace tune-up completed"},
            {"date": "Nov 03", "note": "Called about pilot light — resolved by phone"},
        ],
        "conversation": [],
        "lastIntent": None,
        "lastPriority": None,
    },
    "mike": {
        "id": "mike",
        "name": "Mike Torres",
        "phone": "(415) 555-0198",
        "type": "new",
        "address": None,
        "equipment": None,
        "notes": None,
        "preview": "Looking for an AC install quote",
        "history": [],
        "conversation": [],
        "lastIntent": None,
        "lastPriority": None,
    },
    "dave": {
        "id": "dave",
        "name": "Dave Reynolds",
        "phone": "(415) 555-0177",
        "type": "return",
        "address": "18 Elm Court",
        "equipment": "Rheem 50-gal water heater (replaced Jan 2025)",
        "notes": "Two prior water emergencies. Wife works from home — call her cell first.",
        "preview": "Hey, it's Dave again...",
        "history": [
            {"date": "Jan 08", "note": "Emergency: water heater burst — replaced same day"},
            {"date": "Jan 09", "note": "Follow-up: checked drain pan and shutoff"},
        ],
        "conversation": [],
        "lastIntent": None,
        "lastPriority": None,
    },
    # Septic seeds — primary showcase tenant (septic_pro). Used by the
    # website landing demo in index.html and the SHOWCASE_SCRIPT.md.
    "ellen": {
        "id": "ellen",
        "name": "Ellen Kovacs",
        "phone": "(717) 555-0104",
        "type": "return",
        "address": "88 Ridge Road, Willow Street",
        "equipment": "1000-gal concrete septic tank (pumped Sep 2025)",
        "notes": "Two-week panic-level backup risk if overdue. Prefers evening callbacks after 6.",
        "preview": "Need a pump-out before the holidays...",
        "history": [
            {"date": "Sep 14", "note": "Routine pump-out — tank was 70% full"},
            {"date": "Jun 02", "note": "Called about gurgling sounds — resolved, vent was clogged"},
        ],
        "conversation": [],
        "lastIntent": None,
        "lastPriority": None,
    },
    "travis": {
        "id": "travis",
        "name": "Travis Yoder",
        "phone": "(717) 555-0187",
        "type": "new",
        "address": None,
        "equipment": None,
        "notes": None,
        "preview": "New house, never pumped — what do I do?",
        "history": [],
        "conversation": [],
        "lastIntent": None,
        "lastPriority": None,
    },
    "linda": {
        "id": "linda",
        "name": "Linda Brenneman",
        "phone": "(717) 555-0122",
        "type": "return",
        "address": "3411 Mill Creek Road",
        "equipment": "1500-gal tank, drain field replaced Mar 2024",
        "notes": "Second emergency in 18 months. Husband is a contractor — knows the system.",
        "preview": "Toilets backing up again — came home to a mess",
        "history": [
            {"date": "Mar 18", "note": "Emergency: drain field failure — replaced over 3 days"},
            {"date": "Mar 21", "note": "Follow-up: field holding. Switched to bacterial additive"},
        ],
        "conversation": [],
        "lastIntent": None,
        "lastPriority": None,
    },
}


def _load() -> dict:
    if not MEMORY_FILE.exists():
        MEMORY_FILE.write_text(json.dumps(SEED, indent=2))
    return json.loads(MEMORY_FILE.read_text())


def _save(data: dict) -> None:
    MEMORY_FILE.write_text(json.dumps(data, indent=2))


def list_callers() -> list:
    return list(_load().values())


def get_caller(caller_id: str) -> Optional[dict]:
    return _load().get(caller_id)


def normalize_phone(phone: str) -> str:
    """Reduce a phone number to digits only for use as a stable caller_id.
    Strips a leading US country code so '+14155550142' matches '(415) 555-0142'."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def get_or_create_by_phone(phone: str) -> dict:
    """Look up a caller by phone number across all records; create a new
    record keyed by normalized digits if none exists. Used by voice + SMS."""
    data = _load()
    digits = normalize_phone(phone)

    # Check existing callers for a phone match
    for caller in data.values():
        if normalize_phone(caller.get("phone", "")) == digits:
            return caller

    # New caller — create record keyed by digits
    new_caller = {
        "id": digits,
        "name": "Unknown caller",
        "phone": phone,
        "type": "new",
        "address": None,
        "equipment": None,
        "notes": None,
        "preview": "(inbound phone contact)",
        "history": [],
        "conversation": [],
        "lastIntent": None,
        "lastPriority": None,
    }
    data[digits] = new_caller
    _save(data)
    return new_caller


def append_turn(caller_id: str, role: str, text: str,
                intent: Optional[str] = None,
                priority: Optional[str] = None) -> None:
    data = _load()
    caller = data.get(caller_id)
    if not caller:
        return
    caller.setdefault("conversation", []).append({"role": role, "text": text})
    if intent:
        caller["lastIntent"] = intent
    if priority:
        caller["lastPriority"] = priority
    _save(data)


def add_history_note(caller_id: str, note: str, date: str = "Today") -> None:
    data = _load()
    caller = data.get(caller_id)
    if not caller:
        return
    caller.setdefault("history", []).insert(0, {"date": date, "note": note})
    _save(data)


def update_caller(caller_id: str, **fields) -> Optional[dict]:
    data = _load()
    caller = data.get(caller_id)
    if not caller:
        return None
    caller.update(fields)
    _save(data)
    return caller
