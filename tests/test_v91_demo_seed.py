"""V9.1 — demo-tenant seed.

The seed must:
  - populate septic_pro with realistic-looking activity
  - never touch ace_hvac
  - be idempotent (running twice = no double rows)
  - mark all rows with a DEMO_ prefix so they're identifiable
  - render correctly in the portal
"""
from __future__ import annotations

import pytest

from src import demo_seed, transcripts, usage


# ── seed mechanics ────────────────────────────────────────────────────

def test_seed_runs_clean():
    out = demo_seed.seed_septic_pro()
    assert out.get("seeded") is True
    assert out.get("voice", 0) >= 1
    assert out.get("sms", 0) >= 1


def test_seed_is_idempotent():
    """Second run should bail without writing duplicates."""
    demo_seed.seed_septic_pro()
    out = demo_seed.seed_septic_pro()
    assert out.get("seeded") is False
    assert out.get("reason") == "already_seeded"


def test_seed_only_touches_septic_pro():
    """Critical: never write to ace_hvac (the live tenant)."""
    demo_seed.seed_septic_pro()
    # ace_hvac partner list should have no DEMO_ rows
    partners = usage.list_conversation_partners("ace_hvac")
    for p in partners:
        if p.get("last_call_sid"):
            assert not p["last_call_sid"].startswith("DEMO_"), (
                f"DEMO row leaked into ace_hvac: {p}")


def test_seed_populates_conversation_partners_for_septic_pro():
    """Brief V9.1: portal demos should look populated. The
    Conversations list must surface seeded partners."""
    demo_seed.seed_septic_pro()
    out = usage.list_conversation_partners("septic_pro", limit=20)
    assert len(out) >= 4, (
        f"V9.1 seed should produce >= 4 partners; got {len(out)}")


def test_seed_mixed_channels_present():
    """The brief asks the portal to demonstrate voice + SMS + mixed.
    Verify each appears in the seeded set."""
    demo_seed.seed_septic_pro()
    out = usage.list_conversation_partners("septic_pro", limit=20)
    channels = {p["last_channel"] for p in out}
    assert "voice" in channels
    assert "sms" in channels


def test_seed_includes_an_emergency():
    """Brief: demo should showcase emergency escalation."""
    demo_seed.seed_septic_pro()
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM calls
                WHERE client_id = 'septic_pro'
                  AND emergency = 1
                  AND call_sid LIKE 'DEMO_%'""",
        ).fetchone()
        n = int(row["n"]) if row else 0
        conn.close()
    assert n >= 1, "V9.1 demo seed should include at least one emergency"


def test_seed_includes_a_mixed_voice_and_sms_partner():
    """The brief calls for SMS + voice in one unified history;
    at least one seeded partner must have both."""
    demo_seed.seed_septic_pro()
    out = usage.list_conversation_partners("septic_pro", limit=20)
    mixed = [p for p in out if p["calls"] >= 1 and p["messages"] >= 1]
    assert len(mixed) >= 1, (
        f"V9.1 seed should include >= 1 mixed-channel partner; got: {out}")


def test_seed_transcripts_are_searchable_by_phone():
    """A partner from the seed should have a complete transcript when
    looked up by their phone number — that's what the conversation
    detail page queries."""
    demo_seed.seed_septic_pro()
    out = usage.list_conversation_partners("septic_pro", limit=20)
    assert out, "seed produced no partners"
    # Pick the most recent partner, query their thread
    phone = out[0]["phone"]
    turns = transcripts.list_by_phone("septic_pro", phone)
    assert len(turns) >= 2, (
        f"V9.1 seed should produce >= 2 turns per partner; got {len(turns)}")


# ── purge_then_seed (operator escape hatch) ──────────────────────────

def test_purge_then_seed_clears_and_repopulates():
    """Operator may want to refresh the demo state."""
    demo_seed.seed_septic_pro()
    before = len(usage.list_conversation_partners("septic_pro", limit=20))
    out = demo_seed.purge_then_seed()
    after = len(usage.list_conversation_partners("septic_pro", limit=20))
    assert out["seeded"] is True
    assert after == before, "purge_then_seed should produce the same count"


def test_force_overrides_idempotency_guard():
    """Important for tests + ops escape hatch."""
    demo_seed.seed_septic_pro()
    out = demo_seed.seed_septic_pro(force=True)
    assert out["seeded"] is True


# ── CLI ──────────────────────────────────────────────────────────────

def test_cli_seed_command_succeeds(capsys):
    rc = demo_seed._cli(["seed"])
    out = capsys.readouterr().out
    assert rc == 0
    # First call seeds, subsequent skips
    assert "seeded" in out


def test_cli_purge_command(capsys):
    demo_seed.seed_septic_pro()
    rc = demo_seed._cli(["purge"])
    assert rc == 0


def test_cli_unknown_command_errors(capsys):
    rc = demo_seed._cli(["wat"])
    assert rc == 2


# ── data quality ─────────────────────────────────────────────────────

def test_seed_has_no_engineer_strings_in_summaries():
    """All seeded call summaries must read like real-business notes."""
    demo_seed.seed_septic_pro()
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        try:
            rows = conn.execute(
                """SELECT summary FROM calls
                    WHERE client_id = 'septic_pro'
                      AND call_sid LIKE 'DEMO_%'""",
            ).fetchall()
        except Exception:
            rows = []
        conn.close()
    for r in rows:
        s = (r["summary"] if "summary" in r.keys() else "") or ""
        lo = s.lower()
        for bad in ("token", "llm", "prompt", "agent", "spam_phrase",
                    "duration_capped"):
            assert bad not in lo, (
                f"engineer-y term in seeded summary: {s!r}")
