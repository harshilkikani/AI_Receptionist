"""V9.1 — demo-tenant activity seeder.

The marketing-demo tenant (septic_pro) needs a populated portal so
prospects don't land on an empty state. This module seeds a small set
of plausible calls + SMS threads on first boot.

Design constraints:
  - Idempotent. Running twice doesn't double up — seeded rows use a
    fixed `DEMO_` call_sid prefix; a SELECT on the prefix gates the
    write.
  - Marked clearly. Every seeded row has either call_sid LIKE 'DEMO_%'
    or call_sid LIKE 'SMS_DEMO_%' so an operator inspecting the DB
    sees the source immediately.
  - Tenant-scoped. Touches only `septic_pro`. ace_hvac (the real live
    tenant) is never written to.
  - Realistic timing. Calls are dated to the last 5 days with varied
    times of day, so the Today feed has something fresh and the
    Conversations list has range.
  - Mixed channels. Two SMS-only threads, two voice-only, one mixed —
    exactly the scenarios the brief asks the portal to showcase.

To re-seed (e.g., after schema migrations or to refresh): clear the
DEMO_ rows manually and re-boot, or call `purge_then_seed()`.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger("demo_seed")

TENANT_ID = "septic_pro"

# Each scenario is one phone-number partner with a varied story.
# Times are offsets from "now" so the data stays fresh on every boot.
# Phone numbers are 555-01XX (officially reserved for fiction by NANP).
#
# V10.1 — `caller_id`, `last_name`, `address`, `scenario_hint` added
# so the combined-demo chat at / can use these SAME personas as its
# caller list. Same phone in chat = same phone in portal = same
# DiceBear avatar = same partner card. Unified end-to-end identity.
_SCENARIOS = [
    {
        # Overflow emergency that got transferred — 6 hours ago.
        "phone": "+15550101001",
        "caller_id": "marcus",
        "first_name": "Marcus",
        "last_name": "Reilly",
        "address": "412 Maple Lane, Lancaster",
        "scenario_hint": "Sewage backup emergency — try saying your toilets are overflowing.",
        "voice": {
            "minutes_ago": 6 * 60,
            "duration_s": 47,
            "outcome": "emergency_transfer",
            "emergency": True,
            "summary": "Sewage overflow in finished basement — transferred to Bob.",
            "turns": [
                ("user",      "Hey, my toilets are backing up and there's "
                              "stuff coming up in the basement"),
                ("assistant", "That sounds serious — I'm flagging this as "
                              "an emergency. Address?"),
                ("user",      "412 Maple Lane, Lancaster"),
                ("assistant", "Got it. Bob is on call — he'll ring you in "
                              "under a minute."),
            ],
        },
        "sms": None,
    },
    {
        # Routine pump-out booking — yesterday afternoon.
        "phone": "+15550101002",
        "caller_id": "sarah",
        "first_name": "Sarah",
        "last_name": "Wong",
        "address": "412 Oak Street, Lancaster",
        "scenario_hint": "Returning customer booking a routine pump-out.",
        "voice": {
            "minutes_ago": 26 * 60,
            "duration_s": 92,
            "outcome": "normal",
            "emergency": False,
            "summary": "Booking — routine pump-out next Tuesday afternoon.",
            "turns": [
                ("user",      "Hi, I need to schedule a routine pumping"),
                ("assistant", "Sure thing. Have you used us before?"),
                ("user",      "Yes, you came out two years ago — 412 Oak Street"),
                ("assistant", "I see you. Tuesday afternoon work? I have 1pm or 3pm open."),
                ("user",      "1pm please"),
                ("assistant", "Booked. We'll text the day before to confirm."),
            ],
        },
        "sms": None,
    },
    {
        # Price-shopping inquiry — 2 days ago.
        "phone": "+15550101003",
        "caller_id": "diane",
        "first_name": "Diane",
        "last_name": "Patel",
        "address": "",   # never gave it on the original call
        "scenario_hint": "Price inquiry — ask how much a pump-out costs.",
        "voice": {
            "minutes_ago": 2 * 24 * 60 + 30,
            "duration_s": 38,
            "outcome": "normal",
            "emergency": False,
            "summary": "Quote requested — pricing $475 base, sent text follow-up.",
            "turns": [
                ("user",      "How much for a pump-out?"),
                ("assistant", "Standard pump-outs start at $475 — that "
                              "covers 1000 gallons. How big is your tank?"),
                ("user",      "I don't know honestly, it's been a while"),
                ("assistant", "No worries. I can text you our service-area "
                              "rates and we can figure it out when we come "
                              "out. What's the best number for that?"),
                ("user",      "This one is fine"),
                ("assistant", "Will do. Thanks for calling."),
            ],
        },
        "sms": {
            "minutes_after_voice": 1,
            "turns": [
                ("assistant", "Standard pump-out is $475. Inspections $150 "
                              "if you want one. Talk soon —"),
            ],
        },
    },
    {
        # After-hours callback request via SMS — last night.
        "phone": "+15550101004",
        "caller_id": "ron",
        "first_name": "Ron",
        "last_name": "Albright",
        "address": "228 Riverside Drive, Lancaster",
        "scenario_hint": "After-hours quote request — ask for an estimate.",
        "voice": None,
        "sms": {
            "minutes_ago": 14 * 60,
            "turns": [
                ("user",      "Can someone call me about an estimate tomorrow?"),
                ("assistant", "Absolutely — Bob will call you between 9 and 11am tomorrow. Address?"),
                ("user",      "228 Riverside Drive, Lancaster"),
                ("assistant", "Got it. He'll have your info ready. Talk in the morning."),
            ],
        },
    },
    {
        # Wrong-number SMS — 3 days ago. Showcases the "calm filter"
        # behavior without making the portal feel cluttered.
        "phone": "+15550101005",
        "caller_id": "stranger",
        "first_name": "",
        "last_name": "",
        "address": "",
        "scenario_hint": "Wrong number — try asking for someone who doesn't work there.",
        "voice": None,
        "sms": {
            "minutes_ago": 3 * 24 * 60 + 200,
            "turns": [
                ("user",      "Hey is this dom?"),
                ("assistant", "No worries — wrong number. Have a good one."),
            ],
        },
    },
    {
        # Follow-up estimate conversation mid-day today.
        "phone": "+15550101006",
        "caller_id": "linda",
        "first_name": "Linda",
        "last_name": "Hayes",
        "address": "1100 Birch Road, Lancaster",
        "scenario_hint": "New drain field estimate — ask for a quote.",
        "voice": {
            "minutes_ago": 95,
            "duration_s": 64,
            "outcome": "normal",
            "emergency": False,
            "summary": "Estimate for new drain field — Bob to call back with quote.",
            "turns": [
                ("user",      "Looking for a quote on a new drain field"),
                ("assistant", "Got it. Address and tank size?"),
                ("user",      "1100 Birch Road, septic is from 2003"),
                ("assistant", "Noted. Bob handles the bigger jobs personally — "
                              "he'll call back today with a number. Best time?"),
                ("user",      "After 4pm"),
                ("assistant", "Perfect. We'll talk after 4."),
            ],
        },
        "sms": None,
    },
]


def refresh_timestamps() -> dict:
    """V9.6.1 — re-base every seeded DEMO_v_* call (and matching SMS
    transcript / sms rows) so they read as if they happened "minutes_ago"
    from now. Without this, the demo's "13 hours ago" / "2 days ago"
    labels age with the server uptime — which makes the activity feed
    feel stale during a live demo a day after the seed ran.

    Idempotent. Safe to call on every /demo/today request — six small
    UPDATEs against indexed columns. Returns the number of rows
    refreshed for observability."""
    from src import usage, transcripts
    now_ts = int(time.time())
    voice_n = 0
    sms_n = 0
    transcript_n = 0
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        for sc in _SCENARIOS:
            phone = sc["phone"]
            # ── Voice scenarios: shift start_ts/end_ts so the call
            # registers at the configured minutes_ago offset. ──
            v = sc.get("voice")
            if v:
                sid = "DEMO_v_" + phone.replace("+", "")
                start = now_ts - v["minutes_ago"] * 60
                end = start + v["duration_s"]
                try:
                    res = conn.execute(
                        """UPDATE calls SET start_ts = ?, end_ts = ?,
                                            month = ?
                            WHERE call_sid = ? AND client_id = ?""",
                        (start, end, usage._now_month(),
                         sid, TENANT_ID),
                    )
                    if res.rowcount:
                        voice_n += res.rowcount
                except Exception as e:
                    log.warning("refresh_timestamps voice %s: %s", sid, e)
                # Transcript turns for that call should slide too.
                try:
                    res = conn.execute(
                        """UPDATE transcripts SET ts = ?
                            WHERE call_sid = ? AND client_id = ?
                              AND role = 'user'""",
                        (start, sid, TENANT_ID),
                    )
                    if res.rowcount:
                        transcript_n += res.rowcount
                    res = conn.execute(
                        """UPDATE transcripts SET ts = ?
                            WHERE call_sid = ? AND client_id = ?
                              AND role = 'assistant'""",
                        (start + max(1, v["duration_s"] // 2),
                         sid, TENANT_ID),
                    )
                    if res.rowcount:
                        transcript_n += res.rowcount
                except Exception as e:
                    log.warning("refresh_timestamps trans-v %s: %s", sid, e)

            # ── SMS scenarios: re-anchor each turn's ts. ──
            s = sc.get("sms")
            if s:
                from memory import normalize_phone
                norm = normalize_phone(phone)
                sid = f"SMS_{norm}"
                if "minutes_ago" in s:
                    base = now_ts - s["minutes_ago"] * 60
                else:
                    v = sc.get("voice") or {}
                    v_start = now_ts - (v.get("minutes_ago") or 60) * 60
                    base = (v_start + (v.get("duration_s") or 60)
                            + s.get("minutes_after_voice", 1) * 60)
                # Re-anchor the transcript turns AND the sms rows.
                try:
                    rows = conn.execute(
                        """SELECT id, role FROM transcripts
                            WHERE call_sid = ? AND client_id = ?
                         ORDER BY ts ASC, id ASC""",
                        (sid, TENANT_ID),
                    ).fetchall()
                    for i, r in enumerate(rows):
                        new_ts = base + i * 60
                        conn.execute(
                            "UPDATE transcripts SET ts = ? WHERE id = ?",
                            (new_ts, r["id"]),
                        )
                        transcript_n += 1
                    rows = conn.execute(
                        """SELECT id FROM sms
                            WHERE call_sid = ? AND client_id = ?
                         ORDER BY ts ASC, id ASC""",
                        (sid, TENANT_ID),
                    ).fetchall()
                    for i, r in enumerate(rows):
                        new_ts = base + i * 60
                        conn.execute(
                            "UPDATE sms SET ts = ?, month = ? WHERE id = ?",
                            (new_ts, usage._now_month(), r["id"]),
                        )
                        sms_n += 1
                except Exception as e:
                    log.warning("refresh_timestamps sms %s: %s", sid, e)
        conn.close()
    return {"voice": voice_n, "sms": sms_n, "transcripts": transcript_n}


def list_personas() -> list:
    """V10.1 — the demo personas EXPOSED as caller objects shaped like
    /missed-calls returns. The combined-demo at / pulls from here so
    the chat caller's identity matches the seeded portal partner —
    same name, same phone, same DiceBear avatar.

    The 'fresh' persona at the end is a clean-slate scenario with no
    seeded history; useful for showing first-call behavior."""
    out = []
    for sc in _SCENARIOS:
        first = (sc.get("first_name") or "").strip()
        last = (sc.get("last_name") or "").strip()
        name = (f"{first} {last}".strip() or "Unknown caller")
        # Type matches the V0 chat widget's expectation. Personas with a
        # full conversation seeded look like return callers; the stranger
        # (wrong number) and any name-less persona reads as "new".
        is_return = bool(first and (sc.get("voice") or sc.get("sms")))
        out.append({
            "id":             sc.get("caller_id") or "unknown",
            "name":           name,
            "phone":          sc["phone"],
            "address":        sc.get("address") or "",
            "preview":        sc.get("scenario_hint") or "",
            "type":           "return" if is_return else "new",
            "scenario_hint":  sc.get("scenario_hint") or "",
            "equipment":      "",
        })
    # V10.1 — extra "fresh caller" so the prospect can also demo the
    # first-time-caller experience without conflicting with any seed.
    out.append({
        "id":            "fresh",
        "name":          "New caller",
        "phone":         "+15550101099",
        "address":       "",
        "preview":       "No history — ask anything.",
        "type":          "new",
        "scenario_hint": "Clean slate — try a brand-new scenario.",
        "equipment":     "",
    })
    return out


def register_personas_in_memory() -> int:
    """V10.1 — pre-create memory.json entries for every demo persona
    so /chat doesn't 404 on lookup. Phones match the seeded portal
    partners exactly, which means a chat exchange surfaces on the
    SAME partner card the prospect saw in the portal.

    Idempotent. Updates the name/phone of existing entries (in case a
    persona was renamed) without dropping their conversation history.
    Returns the number of personas touched."""
    import json as _json
    import memory
    personas = list_personas()
    touched = 0
    with memory._io_lock:
        data = memory._load_unsafe()
        for p in personas:
            cid = p["id"]
            existing = data.get(cid)
            if existing:
                existing["name"] = p["name"]
                existing["phone"] = p["phone"]
                existing["address"] = p["address"] or existing.get("address", "")
                existing["type"] = p["type"]
            else:
                data[cid] = {
                    "id":           cid,
                    "name":         p["name"],
                    "phone":        p["phone"],
                    "address":      p["address"],
                    "type":         p["type"],
                    "equipment":    "",
                    "conversation": [],
                    "history":      [],
                }
            touched += 1
        memory._atomic_write(_json.dumps(data, indent=2))
    return touched


def _already_seeded(client_id: str) -> bool:
    from src import usage
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM calls
                WHERE client_id = ?
                  AND call_sid LIKE 'DEMO_%' LIMIT 1""",
            (client_id,),
        ).fetchone()
        n = int(row["n"]) if row else 0
        conn.close()
    return n > 0


def _seed_voice(scenario: dict, now_ts: int) -> None:
    """Write one voice scenario as a call + transcript turns."""
    from src import usage, transcripts
    v = scenario["voice"]
    phone = scenario["phone"]
    sid = "DEMO_v_" + phone.replace("+", "")
    start_ts = now_ts - v["minutes_ago"] * 60
    end_ts = start_ts + v["duration_s"]

    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        # Direct INSERT so we control start_ts/end_ts/outcome in one shot.
        # Try summary column; fall back gracefully if migration hasn't run.
        try:
            conn.execute(
                """INSERT OR REPLACE INTO calls
                     (call_sid, client_id, from_number, to_number,
                      start_ts, end_ts, duration_s, outcome, emergency,
                      summary, month)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sid, TENANT_ID, phone, "+15555550000",
                 start_ts, end_ts, v["duration_s"],
                 v["outcome"], 1 if v["emergency"] else 0,
                 v.get("summary") or "", usage._now_month()),
            )
        except Exception:
            conn.execute(
                """INSERT OR REPLACE INTO calls
                     (call_sid, client_id, from_number, to_number,
                      start_ts, end_ts, duration_s, outcome, emergency,
                      month)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sid, TENANT_ID, phone, "+15555550000",
                 start_ts, end_ts, v["duration_s"],
                 v["outcome"], 1 if v["emergency"] else 0,
                 usage._now_month()),
            )
        conn.close()

    base_ts = start_ts
    for i, (role, text) in enumerate(v["turns"]):
        transcripts.record_turn(sid, TENANT_ID, role, text,
                                  ts=base_ts + i * 8)


def _seed_sms(scenario: dict, now_ts: int) -> None:
    """Write one SMS scenario as transcript turns under SMS_<digits>.

    Note: we use the canonical SID format (no extra DEMO_ infix) so the
    partner-grouping path in list_conversation_partners recognizes the
    phone correctly. Demo rows are identified by the 555-0101XX phone
    range (officially reserved for fiction by NANP), not by SID prefix.
    """
    from src import usage, transcripts
    s = scenario["sms"]
    phone = scenario["phone"]
    from memory import normalize_phone
    norm = normalize_phone(phone)
    sid = f"SMS_{norm}"

    # Anchor timestamp — either explicit, or sequenced after a voice
    # scenario for the same partner.
    if "minutes_ago" in s:
        start_ts = now_ts - s["minutes_ago"] * 60
    else:
        v = scenario.get("voice") or {}
        v_start = now_ts - (v.get("minutes_ago") or 60) * 60
        v_end = v_start + (v.get("duration_s") or 60)
        start_ts = v_end + (s.get("minutes_after_voice", 1) * 60)

    for i, (role, text) in enumerate(s["turns"]):
        ts = start_ts + i * 60
        transcripts.record_turn(sid, TENANT_ID, role, text, ts=ts)
        # Log against the SMS table too so list_conversation_partners
        # picks it up via the SMS source.
        usage.log_sms(sid, TENANT_ID, phone, text,
                       direction="inbound" if role == "user" else "outbound")


def seed_septic_pro(*, force: bool = False) -> dict:
    """Idempotent seed. Returns counts.

    `force=True` skips the already-seeded guard — useful in tests.
    """
    from src import tenant
    client = tenant.load_client_by_id(TENANT_ID)
    if not client:
        log.info("demo_seed: %s tenant not present, skipping", TENANT_ID)
        return {"seeded": False, "reason": "tenant_missing"}

    if not force and _already_seeded(TENANT_ID):
        # V10.1 — even on idempotent re-runs, make sure the personas
        # exist in memory.json so the chat at / can address them. Cheap.
        try:
            register_personas_in_memory()
        except Exception as e:
            log.warning("demo persona registration failed: %s", e)
        return {"seeded": False, "reason": "already_seeded"}

    # V10.1 — register the personas in memory.json before seeding so the
    # chat callers are addressable as soon as the page renders.
    try:
        register_personas_in_memory()
    except Exception as e:
        log.warning("demo persona registration failed: %s", e)
    now_ts = int(time.time())
    voice_n = 0
    sms_n = 0
    for sc in _SCENARIOS:
        if sc.get("voice"):
            _seed_voice(sc, now_ts)
            voice_n += 1
        if sc.get("sms"):
            _seed_sms(sc, now_ts)
            sms_n += 1
    log.info("demo_seed: planted %d voice + %d sms scenarios into %s",
             voice_n, sms_n, TENANT_ID)
    return {"seeded": True, "voice": voice_n, "sms": sms_n}


def purge_then_seed() -> dict:
    """Drop existing demo rows + re-seed. Operator escape hatch.

    Demo rows are identified by:
      - calls.call_sid starting with 'DEMO_'  (voice scenarios)
      - calls / transcripts / sms rows whose phone is in the 555-0101XX
        block (NANP fictional reserve; matches the seeded scenarios)
    """
    from src import usage
    demo_phone_prefix = "+155501010%"   # SQL LIKE — covers 555-01010X-555-01010X
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        conn.execute(
            """DELETE FROM calls WHERE client_id = ?
                AND (call_sid LIKE 'DEMO_%' OR from_number LIKE ?)""",
            (TENANT_ID, demo_phone_prefix))
        conn.execute(
            """DELETE FROM transcripts
                WHERE client_id = ?
                  AND (call_sid LIKE 'DEMO_%'
                    OR call_sid LIKE 'SMS_555010%'
                    OR call_sid LIKE 'SMS_15550101%')""",
            (TENANT_ID,))
        conn.execute(
            """DELETE FROM sms WHERE client_id = ?
                AND (call_sid LIKE 'SMS_555010%'
                  OR call_sid LIKE 'SMS_15550101%'
                  OR to_number LIKE ?)""",
            (TENANT_ID, demo_phone_prefix))
        conn.close()
    return seed_septic_pro(force=True)


# ── CLI ───────────────────────────────────────────────────────────────

def _cli(argv: Optional[list] = None) -> int:
    import sys
    args = list(argv if argv is not None else sys.argv[1:])
    cmd = args[0] if args else "seed"
    if cmd == "seed":
        r = seed_septic_pro()
        print(r)
        return 0
    if cmd == "purge":
        r = purge_then_seed()
        print(r)
        return 0
    if cmd == "force":
        r = seed_septic_pro(force=True)
        print(r)
        return 0
    print(f"usage: python -m src.demo_seed [seed|purge|force]")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
