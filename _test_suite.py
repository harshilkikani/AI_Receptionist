"""End-to-end test suite. Run against a live server on 127.0.0.1:8765.

Two layers:
  1. Infrastructure tests (no Claude needed) — routing, memory, validation,
     TwiML structure, error paths.
  2. LLM integration tests — only run if ANTHROPIC_API_KEY is a real key.
"""
import json
import os
import sys
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE = "http://127.0.0.1:8765"
results = []  # (name, passed, detail)


def t(name, fn):
    try:
        detail = fn()
        results.append((name, True, detail or ""))
        print(f"  PASS  {name}  {detail or ''}")
    except AssertionError as e:
        results.append((name, False, f"FAIL: {e}"))
        print(f"  FAIL  {name}  {e}")
    except Exception as e:
        results.append((name, False, f"ERROR: {type(e).__name__}: {e}"))
        print(f"  ERR   {name}  {type(e).__name__}: {e}")


def http(method, path, body=None, form=None, expect=None):
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    elif body is not None:
        data = json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
    else:
        data = None
        headers = {}
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


# ──────────── INFRASTRUCTURE TESTS (no Claude needed) ────────────

print("\n[1] Static / read endpoints")

def test_index_html():
    code, body = http("GET", "/")
    assert code == 200, code
    assert "<html" in body.lower()
    assert "Ace HVAC" in body
    return f"{len(body)}B HTML"

def test_missed_calls():
    code, body = http("GET", "/missed-calls")
    assert code == 200
    callers = json.loads(body)
    assert len(callers) == 3, f"expected 3 callers, got {len(callers)}"
    ids = {c["id"] for c in callers}
    assert ids == {"sarah", "mike", "dave"}, ids
    sarah = next(c for c in callers if c["id"] == "sarah")
    assert sarah["name"] == "Sarah Mitchell"
    assert len(sarah["history"]) == 2
    assert "Carrier" in sarah["equipment"]
    return f"3 callers (sarah/mike/dave)"

def test_memory_get_known():
    code, body = http("GET", "/memory/dave")
    assert code == 200
    c = json.loads(body)
    assert c["id"] == "dave"
    assert c["type"] == "return"
    return "dave loaded"

def test_memory_get_unknown():
    code, body = http("GET", "/memory/nobody")
    assert code == 404, f"expected 404, got {code}"
    return "404 OK"

def test_memory_post_update():
    code, body = http("POST", "/memory/mike", body={"address": "100 Test Ave", "equipment": "TBD"})
    assert code == 200
    c = json.loads(body)
    assert c["address"] == "100 Test Ave"
    assert c["equipment"] == "TBD"
    # verify persisted
    code2, body2 = http("GET", "/memory/mike")
    c2 = json.loads(body2)
    assert c2["address"] == "100 Test Ave"
    return "update + reread persistent"

def test_chat_404_unknown():
    code, _ = http("POST", "/chat", body={"caller_id": "nobody", "message": "hi"})
    assert code == 404
    return "404 OK"

def test_chat_400_invalid_body():
    code, _ = http("POST", "/chat", body={"oops": "wrong"})
    assert code == 422, f"expected 422, got {code}"
    return "422 OK"

t("GET / serves HTML", test_index_html)
t("GET /missed-calls returns 3 seeds", test_missed_calls)
t("GET /memory/{id} found", test_memory_get_known)
t("GET /memory/{id} unknown -> 404", test_memory_get_unknown)
t("POST /memory/{id} update + persist", test_memory_post_update)
t("POST /chat unknown caller -> 404", test_chat_404_unknown)
t("POST /chat invalid body -> 422", test_chat_400_invalid_body)


print("\n[2] Twilio endpoints (XML structure)")

def parse_twiml(body):
    return ET.fromstring(body)

def test_voice_incoming_known_caller():
    # Sarah's phone is (415) 555-0142
    code, body = http("POST", "/voice/incoming", form={"From": "+14155550142"})
    assert code == 200, (code, body)
    root = parse_twiml(body)
    assert root.tag == "Response"
    say = root.find("Say")
    assert say is not None
    assert "Sarah" in say.text, f"expected Sarah greeting, got {say.text!r}"
    gather = root.find("Gather")
    assert gather is not None
    assert gather.attrib.get("input") == "speech"
    return f"greets by name, gathers speech"

def test_voice_incoming_new_caller():
    code, body = http("POST", "/voice/incoming", form={"From": "+15558881234"})
    assert code == 200
    root = parse_twiml(body)
    say = root.find("Say")
    assert say is not None
    assert "Sarah" not in say.text
    assert "Ace HVAC" in say.text
    # Should have created a new caller record
    code2, body2 = http("GET", "/memory/5558881234")
    assert code2 == 200, "new caller not persisted"
    return "new caller created + greeted generically"

def test_voice_gather_empty_speech():
    # Empty SpeechResult should reprompt without calling Claude
    code, body = http("POST", "/voice/gather", form={"From": "+14155550142", "SpeechResult": ""})
    assert code == 200
    root = parse_twiml(body)
    say = root.find("Say")
    assert "didn't catch" in say.text.lower()
    gather = root.find("Gather")
    assert gather is not None
    return "reprompts on empty speech"

def test_voice_status_non_failure():
    # CallStatus=completed should be a no-op
    code, body = http("POST", "/voice/status",
                      form={"From": "+14155550142", "CallStatus": "completed"})
    assert code == 200
    j = json.loads(body)
    assert j.get("action") == "none"
    return "no-op on completed"

def test_auth_error_handler():
    """With placeholder key, /recover should return clean 503, not 500."""
    code, body = http("POST", "/recover/sarah")
    if code == 200:
        return "skipped (real key set)"
    assert code == 503, f"expected 503 from auth handler, got {code}"
    j = json.loads(body)
    assert j.get("error") == "anthropic_auth"
    assert "ANTHROPIC_API_KEY" in j.get("detail", "")
    return "503 + actionable detail"

t("POST /voice/incoming known caller", test_voice_incoming_known_caller)
t("POST /voice/incoming new caller (auto-create)", test_voice_incoming_new_caller)
t("POST /voice/gather empty speech reprompts", test_voice_gather_empty_speech)
t("POST /voice/status completed -> no-op", test_voice_status_non_failure)
t("Anthropic auth-error -> friendly 503", test_auth_error_handler)


# ──────────── LLM INTEGRATION TESTS ────────────

print("\n[3] LLM integration (real Claude)")

_PLACEHOLDERS = {"sk-ant-test-placeholder", "sk-ant-your-real-key", "sk-ant-..."}
_key = os.environ.get("ANTHROPIC_API_KEY", "")
REAL_KEY = _key.startswith("sk-ant-") and _key not in _PLACEHOLDERS and len(_key) > 30

if not REAL_KEY:
    print("  SKIP (no real ANTHROPIC_API_KEY in environment)")
else:
    def test_recover_sarah():
        code, body = http("POST", "/recover/sarah")
        assert code == 200, body
        j = json.loads(body)
        assert j["intent"] in ("Emergency", "Scheduling", "Quote", "Follow-up", "General")
        assert j["priority"] in ("low", "high")
        assert len(j["reply"]) > 0
        # Memory reference: should mention Sarah, or her furnace, or March 12
        text = j["reply"].lower()
        assert ("sarah" in text or "furnace" in text or "march" in text or "tune" in text), \
            f"recovery message didn't reference memory: {j['reply']}"
        return f"intent={j['intent']} reply='{j['reply'][:60]}...'"

    def test_chat_scheduling():
        code, body = http("POST", "/chat",
                          body={"caller_id": "sarah", "message": "Can I book another tune-up?"})
        assert code == 200, body
        j = json.loads(body)
        assert j["intent"] == "Scheduling", f"expected Scheduling, got {j['intent']}"
        assert j["priority"] == "low"
        return f"intent={j['intent']}"

    def test_chat_emergency():
        code, body = http("POST", "/chat",
                          body={"caller_id": "dave", "message": "my water heater just burst, water everywhere!"})
        assert code == 200, body
        j = json.loads(body)
        assert j["intent"] == "Emergency", f"expected Emergency, got {j['intent']}"
        assert j["priority"] == "high"
        # Verify history note appended
        code2, body2 = http("GET", "/memory/dave")
        c = json.loads(body2)
        assert any("Emergency contact" in h["note"] for h in c["history"]), \
            "emergency history note not appended"
        return f"intent=Emergency, history note appended"

    def test_chat_quote_new_lead():
        code, body = http("POST", "/chat",
                          body={"caller_id": "mike", "message": "How much for an AC install?"})
        assert code == 200, body
        j = json.loads(body)
        assert j["intent"] == "Quote", f"expected Quote, got {j['intent']}"
        return f"intent={j['intent']}"

    def test_voice_gather_with_speech():
        code, body = http("POST", "/voice/gather",
                          form={"From": "+14155550142", "SpeechResult": "I want to schedule a tune-up"})
        assert code == 200
        root = parse_twiml(body)
        say = root.find("Say")
        assert say is not None and len(say.text) > 0
        # Non-emergency should re-gather
        gather = root.find("Gather")
        assert gather is not None, "expected continuation Gather for non-emergency"
        return "spoken reply + re-gather"

    def test_sms_incoming():
        code, body = http("POST", "/sms/incoming",
                          form={"From": "+14155550142", "Body": "still need that appointment"})
        assert code == 200
        root = parse_twiml(body)
        msg = root.find("Message")
        assert msg is not None and len(msg.text) > 0
        return f"SMS reply: '{msg.text[:50]}...'"

    t("POST /recover/sarah (memory reference)", test_recover_sarah)
    t("POST /chat scheduling intent", test_chat_scheduling)
    t("POST /chat emergency + history append", test_chat_emergency)
    t("POST /chat quote (new lead)", test_chat_quote_new_lead)
    t("POST /voice/gather with speech", test_voice_gather_with_speech)
    t("POST /sms/incoming", test_sms_incoming)


# ──────────── SUMMARY ────────────
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"  {passed} passed, {failed} failed (out of {len(results)})")
if failed:
    print("\nFAILURES:")
    for name, ok, detail in results:
        if not ok:
            print(f"  - {name}: {detail}")
sys.exit(0 if failed == 0 else 1)
