"""V9.0 — design system components.

icon() / status_pill() / call_card() / tabs() were added so portal +
admin + landing can share the same vocabulary. Cover:
  - palette collapse (single accent across surfaces)
  - status pill plain-English mapping (no engineer-y strings leak)
  - icon library fallback for unknown names
  - call_card escaping + status integration
  - tabs aria-current marker
"""
from __future__ import annotations

import pytest

from src import design


# ── palette collapse ──────────────────────────────────────────────────

def test_css_contains_slate_blue_accent():
    css = design.css()
    assert "#1e3a8a" in css, "V9.0 accent (slate-blue) should be present"


def test_css_no_violet_marketing_brand():
    """V9.0 dropped the violet-brand surface accent. The constant
    `--brand-500: var(--accent)` aliases through, so the literal violet
    hex (#7c3aed) should no longer appear in the stylesheet."""
    css = design.css()
    assert "#7c3aed" not in css
    assert "#0d9488" not in css   # old teal too


def test_css_accent_aliases_collapse_to_single_accent():
    """Verify the per-surface aliases all resolve to the single accent
    token, not their old per-surface hex values."""
    css = design.css()
    # The alias declarations are present...
    assert "--ops-500:" in css
    assert "--client-500:" in css
    assert "--brand-500:" in css
    # ...but they all point at var(--accent), not a hex
    for alias in ("--ops-500", "--client-500", "--brand-500"):
        # Find the line: `  --ops-500: <value>;`
        prefix = f"{alias}:"
        idx = css.find(prefix)
        assert idx >= 0
        rest = css[idx + len(prefix):idx + len(prefix) + 80]
        assert "var(--accent)" in rest, f"{alias} should alias --accent, got: {rest!r}"


# ── status_pill ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, label, variant", [
    ("answered",       "Answered",      "info"),
    ("normal",         "Answered",      "info"),     # legacy mapping
    ("missed",         "Missed",        "warn"),
    ("no_answer",      "No answer",     "warn"),
    ("transferred",    "Transferred",   "good"),
    ("voicemail",      "Voicemail",     "ghost"),
    ("emergency",      "Emergency",     "bad"),
    ("callback",       "Callback sent", "info"),
    ("follow_up",      "Follow-up",     "info"),
    ("wrong_number",   "Wrong number",  "ghost"),
    ("spam",           "Filtered",      "ghost"),
    ("spam_number",    "Filtered",      "ghost"),
    ("spam_phrase",    "Filtered",      "ghost"),
    ("duration_capped", "Long call",    "ghost"),
])
def test_status_pill_known_statuses(raw, label, variant):
    out = design.status_pill(raw)
    assert label in out
    assert f"pill {variant}" in out


def test_status_pill_unknown_status_falls_back_neutrally():
    """V9.0 contract: no engineer string is ever shown raw. Unknown
    statuses get title-cased + ghost pill, never bad/warn."""
    out = design.status_pill("some_internal_state")
    assert "Some Internal State" in out
    assert "pill ghost" in out
    # Must not accidentally surface bad/warn for unknowns
    assert "pill bad" not in out
    assert "pill warn" not in out


def test_status_pill_empty_input():
    out = design.status_pill("")
    assert "Unknown" in out


def test_status_pill_includes_dot_by_default():
    """The colored dot is the V9.0 visual marker — present unless
    explicitly suppressed."""
    out = design.status_pill("answered")
    assert '<span class="dot">' in out


def test_status_pill_dot_suppressible():
    out = design.status_pill("answered", with_dot=False)
    assert '<span class="dot">' not in out


def test_status_pill_escapes_html_in_unknown():
    out = design.status_pill("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;" in out


# ── icon ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "phone", "voicemail", "mic", "calendar", "alert", "check",
    "chevron", "arrow", "transfer", "search", "settings", "user",
    "clock", "home", "list",
])
def test_icon_known_names_render_svg(name):
    out = design.icon(name)
    assert "<svg" in out
    assert "</svg>" in out
    assert 'viewBox="0 0 24 24"' in out


def test_icon_unknown_name_returns_empty():
    """No exception, no broken markup — empty string."""
    out = design.icon("not-a-real-icon-name")
    assert out == ""


def test_icon_custom_size():
    out = design.icon("phone", size=24)
    assert 'width="24"' in out
    assert 'height="24"' in out


def test_icon_has_aria_hidden():
    """Decorative — never announced to screen readers."""
    out = design.icon("phone")
    assert 'aria-hidden="true"' in out


# ── call_card ─────────────────────────────────────────────────────────

def test_call_card_minimal():
    out = design.call_card(caller="Mike R.", status="answered")
    assert "Mike R." in out
    assert "M" in out   # initial in avatar
    assert "Answered" in out
    assert 'class="call"' in out


def test_call_card_with_full_detail():
    out = design.call_card(
        caller="Sarah Wong",
        from_number="(555) 123-4567",
        when="2 hours ago",
        summary="Booking for tank pump-out next Tuesday",
        status="answered",
        duration="2m 14s",
    )
    assert "Sarah Wong" in out
    assert "(555) 123-4567" in out
    assert "2 hours ago" in out
    assert "Booking for tank pump-out" in out
    assert "2m 14s" in out


def test_call_card_emergency_uses_bad_pill():
    out = design.call_card(caller="X", status="emergency")
    assert "pill bad" in out


def test_call_card_with_href_becomes_link():
    out = design.call_card(caller="X", status="answered",
                           href="/portal/x/call/abc123")
    assert '<a class="call"' in out
    assert 'href="/portal/x/call/abc123"' in out


def test_call_card_escapes_caller_name():
    out = design.call_card(caller="<script>alert(1)</script>",
                           status="answered")
    assert "<script>" not in out


def test_call_card_escapes_summary():
    out = design.call_card(caller="X", status="answered",
                           summary='"><img src=x onerror=alert(1)>')
    assert "onerror" not in out.lower() or "&lt;" in out or "&quot;" in out


def test_call_card_unknown_caller_initial_is_question_mark():
    out = design.call_card(caller="", status="answered")
    assert "Unknown caller" in out


# ── tabs ──────────────────────────────────────────────────────────────

def test_tabs_renders_links():
    out = design.tabs([
        ("Today", "/portal/x"),
        ("Calls", "/portal/x/calls"),
        ("Settings", "/portal/x/settings"),
    ])
    assert "Today" in out
    assert 'href="/portal/x/calls"' in out
    assert 'class="tabs"' in out


def test_tabs_marks_active():
    out = design.tabs([
        ("Today", "/portal/x"),
        ("Calls", "/portal/x/calls"),
    ], active="/portal/x/calls")
    # Active link gets aria-current
    assert 'href="/portal/x/calls" aria-current="page"' in out
    # Inactive does not
    assert 'href="/portal/x" aria-current' not in out


def test_tabs_no_active_when_unmatched():
    out = design.tabs([("A", "/a"), ("B", "/b")], active="/c")
    assert "aria-current" not in out


def test_tabs_escapes_label():
    out = design.tabs([("<script>", "/x")])
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
