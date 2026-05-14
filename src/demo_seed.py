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
_SCENARIOS = [
    {
        # Overflow emergency that got transferred — 6 hours ago.
        "phone": "+15550101001",
        "first_name": "Marcus",
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
        "first_name": "Sarah",
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
        "first_name": "Diane",
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
        "first_name": "Ron",
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
        "first_name": "",
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
        "first_name": "Linda",
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
        return {"seeded": False, "reason": "already_seeded"}

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
