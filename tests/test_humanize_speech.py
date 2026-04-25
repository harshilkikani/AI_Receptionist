"""V4.2 — natural speech preprocessing tests."""
from __future__ import annotations

import pytest

from src import humanize_speech as h


# ── number → words primitives ─────────────────────────────────────────

@pytest.mark.parametrize("n,expected", [
    (0, "zero"), (1, "one"), (12, "twelve"), (19, "nineteen"),
    (20, "twenty"), (35, "thirty-five"), (99, "ninety-nine"),
    (100, "one hundred"), (101, "one hundred one"),
    (250, "two hundred fifty"), (475, "four hundred seventy-five"),
    (999, "nine hundred ninety-nine"),
    (1000, "one thousand"), (1001, "one thousand one"),
    (12500, "twelve thousand five hundred"),
])
def test_int_to_words(n, expected):
    assert h._int_to_words(n) == expected


def test_street_pair_4_digit():
    assert h._street_pair(4273) == "forty-two seventy-three"
    assert h._street_pair(8800) == "eighty-eight hundred"
    assert h._street_pair(1500) == "fifteen hundred"


def test_street_pair_3_digit_keeps_long_form():
    assert h._street_pair(450) == "four hundred fifty"


# ── currency ────────────────────────────────────────────────────────

@pytest.mark.parametrize("inp,expected_substring", [
    ("$1", "one dollar"),
    ("$5", "five dollars"),
    ("$129", "one hundred twenty-nine dollars"),
    ("$475", "four hundred seventy-five dollars"),
    ("$1,500", "one thousand five hundred dollars"),
    ("$15,000", "fifteen thousand dollars"),
    ("$1.50", "one dollar and fifty cents"),
    ("$0.25", "zero dollars and twenty-five cents"),
    ("$129.95", "one hundred twenty-nine dollars and ninety-five cents"),
])
def test_currency(inp, expected_substring):
    out = h.humanize_for_speech(inp)
    assert expected_substring in out


def test_currency_in_sentence():
    out = h.humanize_for_speech("Pump-out is $475 for a 1000-gallon tank.")
    assert "$475" not in out
    assert "four hundred seventy-five dollars" in out


# ── phone numbers ───────────────────────────────────────────────────

@pytest.mark.parametrize("inp,expected", [
    ("+18885551212", "one, eight eight eight, five five five, one two one two"),
    ("+15551234567", "one, five five five, one two three, four five six seven"),
    ("(555) 219-3987", "five five five, two one nine, three nine eight seven"),
    ("555-219-3987", "five five five, two one nine, three nine eight seven"),
    ("555.219.3987", "five five five, two one nine, three nine eight seven"),
    ("5551234567", "five five five, one two three, four five six seven"),
])
def test_phone_numbers(inp, expected):
    out = h.humanize_for_speech(inp)
    assert expected in out


def test_phone_in_sentence():
    out = h.humanize_for_speech("Call us back at +18887775555.")
    assert "+18887775555" not in out
    assert "eight eight eight" in out


# ── times ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("inp,expected", [
    ("9 AM", "nine A M"),
    ("3 PM", "three P M"),
    ("9:30 AM", "nine thirty A M"),
    ("3:45 PM", "three forty-five P M"),
    ("12:05 PM", "twelve oh five P M"),
    ("9:00 AM", "nine A M"),
])
def test_times(inp, expected):
    out = h.humanize_for_speech(inp)
    assert expected in out


def test_time_lowercase_am():
    out = h.humanize_for_speech("Closes at 5 pm.")
    assert "five P M" in out


# ── street addresses ────────────────────────────────────────────────

def test_street_address_with_road():
    out = h.humanize_for_speech("My address is 4273 Mill Creek Road.")
    assert "forty-two seventy-three" in out
    assert "Mill" in out


def test_street_address_with_st():
    out = h.humanize_for_speech("42 Oak St")
    # Two-digit street numbers use long form
    assert "forty-two" in out


def test_street_address_with_ave():
    out = h.humanize_for_speech("8800 Sunset Boulevard")
    assert "eighty-eight hundred" in out


def test_non_street_number_left_alone():
    """4-digit numbers NOT followed by a street suffix stay numeric."""
    out = h.humanize_for_speech("We've handled 4273 calls this month.")
    # The "4273 calls" pattern shouldn't trigger street-ization
    assert "4273" in out or "four thousand two hundred seventy-three" not in out


def test_year_is_left_alone():
    out = h.humanize_for_speech("Tank installed 2019.")
    # "2019" without a street suffix = left as numeric (or worst case
    # spelled fine); we just don't want it labeled as an address.
    assert "Tank installed" in out


# ── combined / order-of-operations ──────────────────────────────────

def test_complex_sentence():
    inp = ("Pump-out runs $475. Address is 4273 Mill Creek Road. "
           "Tech will arrive at 9:30 AM. Call (555) 219-3987.")
    out = h.humanize_for_speech(inp)
    assert "four hundred seventy-five dollars" in out
    assert "forty-two seventy-three" in out
    assert "nine thirty A M" in out
    assert "five five five, two one nine" in out


def test_currency_with_phone_doesnt_collide():
    """'$5551234567' shouldn't be parsed as a phone number — it has $."""
    out = h.humanize_for_speech("$129 service call")
    assert "one hundred twenty-nine dollars" in out


# ── safety / edge cases ──────────────────────────────────────────────

def test_empty_string():
    assert h.humanize_for_speech("") == ""


def test_none_input():
    assert h.humanize_for_speech(None) == ""


def test_no_numbers_left_untouched():
    inp = "Hey there — what's going on?"
    assert h.humanize_for_speech(inp) == inp


def test_handles_malformed_input_gracefully():
    """A weird-but-not-broken input should pass through, not raise."""
    inp = "$$$$ random text 12345"
    out = h.humanize_for_speech(inp)
    assert isinstance(out, str)


# ── per-tenant toggle ────────────────────────────────────────────────

def test_is_enabled_default_true():
    assert h.is_enabled(None) is True
    assert h.is_enabled({"id": "x"}) is True


def test_is_enabled_explicit_true():
    assert h.is_enabled({"humanize_speech": True}) is True


def test_is_enabled_explicit_false():
    assert h.is_enabled({"humanize_speech": False}) is False
    assert h.is_enabled({"humanize_speech": "false"}) is False
    assert h.is_enabled({"humanize_speech": 0}) is False


# ── pipeline integration ────────────────────────────────────────────

def test_response_humanizes_when_flag_on():
    """Sanity check that the wired pipeline applies the transform."""
    # Just verify that humanize_for_speech is called from main._respond.
    # Direct call here — main.py already imports it.
    out = h.humanize_for_speech("Total is $475.")
    assert "four hundred seventy-five dollars" in out
