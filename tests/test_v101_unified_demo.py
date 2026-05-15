"""V10.1 — unified demo identity (chat callers == portal partners).

User reported V9.6 had a desync: the chat caller's name/phone/avatar
didn't match the portal's seeded partner, so picking "Sarah Mitchell"
in the chat created a brand-new partner card while a different "Sarah"
already sat in the seeded portal feed.

V10.1 makes the chat caller list THE seeded demo personas:
  - Same phone in chat = same phone in portal seed
  - Same DiceBear seed (normalized digits) = same illustrated portrait
  - Chat exchange writes to the matching SMS partner card → grows
    organically, doesn't duplicate.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from src import demo_seed, usage


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    # Pretend the LLM responded so /chat lands without a network call.
    def fake_pipeline(caller, message, client=None, call_sid="", **kw):
        reply = "Got it. Bob will call you back within the hour."
        cid = (client or {}).get("id", "_default")
        if call_sid:
            from src import transcripts as _t
            _t.record_turn(call_sid, cid, "user", message,
                            intent="Scheduling")
            _t.record_turn(call_sid, cid, "assistant", reply,
                            intent="Scheduling")
        return {
            "reply": reply, "intent": "Scheduling",
            "priority": "low", "sentiment": "neutral",
            "caller": caller,
        }
    monkeypatch.setattr(main, "_run_pipeline", fake_pipeline)
    return TestClient(main.app), main


# ── list_personas ───────────────────────────────────────────────────

def test_list_personas_returns_seven_entries():
    """Six seeded scenarios + one 'fresh caller' clean-slate persona."""
    out = demo_seed.list_personas()
    assert len(out) == 7


def test_list_personas_each_has_required_chat_caller_fields():
    """Shape must match the /missed-calls contract the chat UI expects."""
    for p in demo_seed.list_personas():
        assert p["id"]
        assert p["name"]
        assert p["phone"]
        assert p["type"] in ("new", "return")
        assert "scenario_hint" in p


def test_list_personas_phones_match_seeded_scenarios():
    """Critical: the personas' phones MUST be the same phones the
    seeded portal scenarios use, or chat exchanges won't merge into
    the existing partner cards."""
    seeded_phones = {sc["phone"] for sc in demo_seed._SCENARIOS}
    persona_phones = {p["phone"] for p in demo_seed.list_personas()}
    # Every seeded phone must appear as a persona
    assert seeded_phones <= persona_phones
    # The "fresh caller" extra phone (5550101099) is the only addition
    extra = persona_phones - seeded_phones
    assert len(extra) == 1


def test_list_personas_marcus_is_emergency_return():
    """Marcus matches the seeded emergency-overflow scenario."""
    personas = demo_seed.list_personas()
    marcus = next(p for p in personas if p["id"] == "marcus")
    assert "Marcus" in marcus["name"]
    assert marcus["phone"] == "+15550101001"
    assert marcus["type"] == "return"
    assert marcus["scenario_hint"]


def test_list_personas_fresh_caller_is_clean_slate():
    personas = demo_seed.list_personas()
    fresh = next(p for p in personas if p["id"] == "fresh")
    assert fresh["type"] == "new"
    assert "No history" in fresh.get("preview", "") or fresh["address"] == ""


# ── register_personas_in_memory ─────────────────────────────────────

def test_register_personas_creates_memory_entries():
    """After registration, memory.get_caller(persona_id) returns a
    record. Required for /chat to resolve the caller."""
    import memory
    demo_seed.register_personas_in_memory()
    for p in demo_seed.list_personas():
        rec = memory.get_caller(p["id"])
        assert rec is not None
        assert rec["phone"] == p["phone"]


def test_register_personas_is_idempotent():
    """Calling twice doesn't duplicate or wipe history."""
    import memory
    demo_seed.register_personas_in_memory()
    marcus = memory.get_caller("marcus")
    # Add a fake conversation entry so we can verify it survives.
    with memory._io_lock:
        data = memory._load_unsafe()
        data["marcus"]["conversation"] = [{"role": "user", "text": "test"}]
        import json
        memory._atomic_write(json.dumps(data, indent=2))
    # Re-register; conversation should survive.
    demo_seed.register_personas_in_memory()
    marcus_after = memory.get_caller("marcus")
    assert marcus_after.get("conversation"), (
        "register_personas_in_memory must NOT clobber existing "
        "conversation history")


# ── /demo/callers endpoint ─────────────────────────────────────────

def test_demo_callers_returns_persona_list(app_client):
    client, _ = app_client
    r = client.get("/demo/callers")
    assert r.status_code == 200
    out = r.json()
    assert isinstance(out, list)
    assert len(out) >= 6
    # Marcus must be in there
    ids = {p["id"] for p in out}
    assert "marcus" in ids


def test_demo_callers_no_auth_required(app_client):
    client, _ = app_client
    r = client.get("/demo/callers")
    assert r.status_code == 200


def test_demo_callers_registers_personas(app_client):
    """Hitting /demo/callers must ensure memory.json has the personas
    registered (so a subsequent /chat call resolves them)."""
    import memory
    client, _ = app_client
    client.get("/demo/callers")
    for cid in ("marcus", "sarah", "diane", "ron", "linda", "fresh"):
        assert memory.get_caller(cid) is not None, (
            f"persona {cid!r} not registered after /demo/callers")


# ── end-to-end: chat → portal merges by phone ──────────────────────

def test_chat_as_marcus_lands_on_marcus_partner_card(app_client):
    """The killer demo path: pick Marcus in chat → type → portal's
    Marcus card (+15550101001) gets the new SMS exchange merged in,
    NOT a separate new partner."""
    client, _ = app_client
    # Make sure personas are registered + seeded.
    demo_seed.seed_septic_pro()
    # Snapshot partner count before.
    before = usage.list_conversation_partners("septic_pro", limit=50)
    before_phones = {p["phone"] for p in before}
    assert "+15550101001" in before_phones  # Marcus already seeded

    # Hit /demo/callers so personas are registered in memory.
    client.get("/demo/callers")

    # Prospect types as Marcus.
    r = client.post("/chat", json={
        "caller_id": "marcus",
        "message": "Hey, just following up on the overflow.",
        "client_id": "septic_pro",
    })
    assert r.status_code == 200

    # Partner count for septic_pro is unchanged — Marcus already existed.
    after = usage.list_conversation_partners("septic_pro", limit=50)
    after_phones = {p["phone"] for p in after}
    new_phones = after_phones - before_phones
    assert "+15550101001" not in new_phones  # Marcus didn't duplicate
    # Marcus's message count went up (the chat exchange appended).
    marcus_after = next(p for p in after if p["phone"] == "+15550101001")
    marcus_before = next((p for p in before if p["phone"] == "+15550101001"),
                          None)
    if marcus_before:
        assert marcus_after["messages"] > marcus_before["messages"], (
            f"chat exchange did not append to Marcus's partner card "
            f"(before={marcus_before['messages']} "
            f"after={marcus_after['messages']})")


def test_chat_avatar_seed_matches_portal_avatar_seed(app_client):
    """Both sides hash the partner's normalized phone digits for the
    DiceBear seed. Same phone = same illustration."""
    from src.design import partner_photo_url
    from memory import normalize_phone
    client, _ = app_client
    client.get("/demo/callers")  # register

    # Pull the chat caller record for Marcus
    callers = client.get("/demo/callers").json()
    marcus = next(c for c in callers if c["id"] == "marcus")
    # The chat-side seed (digits-only, per the JS hashHue) must equal
    # the portal-side seed (normalize_phone via partner_photo_url).
    chat_seed = "".join(ch for ch in marcus["phone"] if ch.isdigit())
    if len(chat_seed) == 11 and chat_seed.startswith("1"):
        chat_seed_norm = chat_seed[1:]
    else:
        chat_seed_norm = chat_seed
    portal_url = partner_photo_url(marcus["phone"])
    assert chat_seed_norm in portal_url, (
        f"avatar seeds diverge — chat would render a different "
        f"portrait than the portal for {marcus['name']!r}")


def test_combined_demo_uses_demo_callers_not_missed_calls(app_client):
    """V10.1: the combined demo at / must fetch from /demo/callers
    (unified-identity), not the older /missed-calls path.

    V11.0: the fetch URL now carries an industry query param so the
    caller list filters to the active vertical. We assert the URL
    starts with /demo/callers — the query string can vary."""
    client, _ = app_client
    r = client.get("/")
    body = r.text
    # The fetch call must target /demo/callers (with or without query).
    # V11.0 — the URL is built as "/demo/callers?industry=..."; we
    # match the path prefix to be tolerant of both forms.
    assert "/demo/callers" in body
    assert 'fetch("/demo/callers")' in body \
        or '"/demo/callers?industry=' in body
    # And the old fetch call to /missed-calls must NOT appear.
    # (Code comments that mention `/missed-calls` are fine — we just
    # care that there's no live fetch to it.)
    assert 'fetch("/missed-calls")' not in body


def test_missed_calls_endpoint_still_works(app_client):
    """The /missed-calls endpoint stays available for any non-demo
    consumer (V0 web chat, admin tooling). Just not used by the new
    combined demo."""
    client, _ = app_client
    r = client.get("/missed-calls")
    assert r.status_code == 200


# ── scenario hints render in chat ──────────────────────────────────

def test_demo_page_chat_intro_uses_scenario_hint(app_client):
    """The chat introduces the persona with their scenario hint when
    selected — sets demo expectations without the prospect having to
    guess what each caller is about."""
    client, _ = app_client
    r = client.get("/")
    body = r.text
    # The JS code that emits the scenario hint must be present
    assert "scenario_hint" in body
