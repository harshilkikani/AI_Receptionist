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


# V8.9b — memory.json access serialization. Two concurrent writers
# from V8.9b's background-thread pipeline (the synchronous /voice/gather
# path AND the worker calling memory.append_turn at the same instant)
# raced and concatenated JSON, corrupting the file. _save now writes
# to a temp file + atomic rename, AND a process-wide lock serializes
# load+save so partial state never appears on disk.
_io_lock = __import__("threading").Lock()


def _load() -> dict:
    with _io_lock:
        return _load_unsafe()


def _load_unsafe() -> dict:
    """Internal: read without the lock. Use _load() from external code."""
    if not MEMORY_FILE.exists():
        _atomic_write(json.dumps(SEED, indent=2))
        return dict(SEED)
    try:
        return json.loads(MEMORY_FILE.read_text())
    except json.JSONDecodeError as e:
        # V8.9b — corrupt memory.json (e.g. from a pre-atomic-write race)
        # would otherwise wedge every voice call. Back up the corrupt
        # file for forensics and rebuild from SEED so the demo recovers
        # automatically on next read.
        import logging, time
        log = logging.getLogger("memory")
        backup = MEMORY_FILE.with_suffix(
            f".corrupt.{int(time.time())}.json")
        try:
            MEMORY_FILE.rename(backup)
        except OSError:
            pass
        log.error("memory.json was corrupt at char %s — backed up to %s, "
                  "rebuilding from SEED", e.pos, backup.name)
        _atomic_write(json.dumps(SEED, indent=2))
        return dict(SEED)


def _atomic_write(text: str) -> None:
    """V8.9b — write via temp file + os.replace so partial writes can
    never appear on disk. Avoids the JSONDecodeError-on-read race."""
    import os
    tmp = MEMORY_FILE.with_suffix(MEMORY_FILE.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, MEMORY_FILE)


def _save(data: dict) -> None:
    with _io_lock:
        _atomic_write(json.dumps(data, indent=2))


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
    record keyed by normalized digits if none exists. Used by voice + SMS.

    V8.9b — load + check + save are now held under the IO lock so two
    concurrent webhooks for the same phone don't both create a new
    record + double-write.
    """
    digits = normalize_phone(phone)
    with _io_lock:
        data = _load_unsafe()
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
        _atomic_write(json.dumps(data, indent=2))
        return new_caller


def append_turn(caller_id: str, role: str, text: str,
                intent: Optional[str] = None,
                priority: Optional[str] = None) -> None:
    """V8.9b — atomic read-modify-write. Two concurrent appends used to
    race; the second clobbered the first."""
    with _io_lock:
        data = _load_unsafe()
        caller = data.get(caller_id)
        if not caller:
            return
        caller.setdefault("conversation", []).append({"role": role, "text": text})
        if intent:
            caller["lastIntent"] = intent
        if priority:
            caller["lastPriority"] = priority
        _atomic_write(json.dumps(data, indent=2))


def add_history_note(caller_id: str, note: str, date: str = "Today") -> None:
    with _io_lock:
        data = _load_unsafe()
        caller = data.get(caller_id)
        if not caller:
            return
        caller.setdefault("history", []).insert(0, {"date": date, "note": note})
        _atomic_write(json.dumps(data, indent=2))


def update_caller(caller_id: str, **fields) -> Optional[dict]:
    with _io_lock:
        data = _load_unsafe()
        caller = data.get(caller_id)
        if not caller:
            return None
        caller.update(fields)
        _atomic_write(json.dumps(data, indent=2))
        return caller
