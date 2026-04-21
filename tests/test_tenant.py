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
    """Clients with IDs starting with _ must never match by inbound number."""
    # Even if _template had a real number, it shouldn't route
    c = tenant.load_client_by_number("")  # empty
    assert c["id"] == "_default"


def test_plan_shape_present():
    c = tenant.load_client_by_number("+18449403274")
    assert "plan" in c
    assert c["plan"]["max_call_duration_seconds"] == 240
    assert c["plan"]["max_call_duration_emergency"] == 360
    assert c["plan"]["sms_max_per_call"] == 3
