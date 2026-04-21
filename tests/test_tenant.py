"""Section G — multi-tenant routing + config loading."""

from src import tenant


def test_ace_hvac_routes_by_inbound():
    c = tenant.load_client_by_number("+18449403274")
    assert c["id"] == "ace_hvac"
    assert c["name"] == "Ace HVAC & Plumbing"


def test_unknown_number_falls_back_to_default():
    c = tenant.load_client_by_number("+10000000000")
    assert c["id"] == "_default"


def test_normalized_phone_matches_across_formats():
    c1 = tenant.load_client_by_number("+18449403274")
    c2 = tenant.load_client_by_number("(844) 940-3274")
    c3 = tenant.load_client_by_number("8449403274")
    assert c1["id"] == c2["id"] == c3["id"] == "ace_hvac"


def test_template_client_does_not_route():
    """Clients with IDs starting with _ must never match by inbound number.
    Also: when phone is empty AND only one real tenant exists, fall back
    to that tenant (single-tenant convenience for web chat / tests)."""
    c = tenant.load_client_by_number("")
    # In this repo, ace_hvac is the only real tenant with an inbound number
    # (example_client has inbound_number="" by design), so empty → ace_hvac
    assert c["id"] == "ace_hvac"


def test_empty_phone_with_multiple_real_tenants_falls_to_default(tmp_path, monkeypatch):
    """If two real tenants are configured, empty phone → _default (can't
    guess which one the caller meant)."""
    # Create a fresh clients dir with TWO real tenants
    clients_dir = tmp_path / "clients"
    clients_dir.mkdir()
    (clients_dir / "_default.yaml").write_text(
        "id: _default\nname: 'fallback'\ninbound_number: ''\n"
        "plan: {max_call_duration_seconds: 240, max_call_duration_emergency: 360, sms_max_per_call: 3}\n"
    )
    (clients_dir / "a.yaml").write_text(
        "id: a\nname: 'A'\ninbound_number: '+19990001111'\n"
        "plan: {max_call_duration_seconds: 240, max_call_duration_emergency: 360, sms_max_per_call: 3}\n"
    )
    (clients_dir / "b.yaml").write_text(
        "id: b\nname: 'B'\ninbound_number: '+19990002222'\n"
        "plan: {max_call_duration_seconds: 240, max_call_duration_emergency: 360, sms_max_per_call: 3}\n"
    )
    monkeypatch.setattr(tenant, "CLIENTS_DIR", clients_dir)
    tenant.reload()
    c = tenant.load_client_by_number("")
    assert c["id"] == "_default"
    # But an unambiguous match still routes
    assert tenant.load_client_by_number("+19990002222")["id"] == "b"


def test_plan_shape_present():
    c = tenant.load_client_by_number("+18449403274")
    assert "plan" in c
    assert c["plan"]["max_call_duration_seconds"] == 240
    assert c["plan"]["max_call_duration_emergency"] == 360
    assert c["plan"]["sms_max_per_call"] == 3
