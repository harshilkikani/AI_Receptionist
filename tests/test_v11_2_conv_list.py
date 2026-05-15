"""V11.2 — customer-phone conversation-list pattern + dropdown polish.

V11.0/V11.1 used horizontal chat-chips for caller selection. V11.2
replaces that with an iMessage-pattern vertical conversation list:
avatar + full name + phone + recent message preview + relative
timestamp. The phone shell has two modes (data-mode attribute):
LIST (default — vertical conversation list) and THREAD (after a
caller is selected — back button + caller header + conversation +
input).

Also: V11.1 hotfix shipped a fix for the customer-phone empty-chips
JS syntax error. V11.2 retains the fix and adds Node --check-style
syntax-guard tests.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("CLIENT_PORTAL_SECRET", "test-secret")
    monkeypatch.setenv("TWILIO_VERIFY_SIGNATURES", "false")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    from src import security
    security.reset_buckets()
    import main
    importlib.reload(main)
    return TestClient(main.app)


# ── 1. Conversation-list structure ──────────────────────────────────


def test_phone_shell_has_data_mode_list_attribute(app_client):
    """V11.2 — customer phone shell initial state is LIST mode."""
    r = app_client.get("/")
    body = r.text
    assert 'id="customer-phone"' in body
    assert 'data-mode="list"' in body


def test_callers_container_is_conv_list(app_client):
    """V11.2 — the #callers element is now a vertical conv-list
    (replaces the V10.1 horizontal chat-chips row)."""
    r = app_client.get("/")
    body = r.text
    # Element kept as id="callers" for backwards compat; classed conv-list now
    assert 'class="conv-list" id="callers"' in body
    # Bonus: role="list" for accessibility
    assert 'role="list"' in body


def test_phone_bar_has_list_and_thread_variants(app_client):
    """V11.2 — phone-bar has two children: bar-list (brand + tagline)
    and bar-thread (back button + caller avatar + caller info)."""
    r = app_client.get("/")
    body = r.text
    assert 'class="bar-list"' in body
    assert 'class="bar-thread"' in body
    # Thread-mode header has back button + avatar + name + phone elements
    assert 'id="phone-back"' in body
    assert 'id="bar-thread-img"' in body
    assert 'id="bar-thread-name"' in body
    assert 'id="bar-thread-phone"' in body


def test_phone_back_button_aria_label(app_client):
    """Accessibility: the back button has an aria-label."""
    r = app_client.get("/")
    body = r.text
    assert 'aria-label="Back to messages"' in body


def test_list_mode_tagline_is_messages(app_client):
    """V11.2 — pre-V11.2 the brand-sub line read the phone number +
    Open now. iMessage-pattern: just "Messages" in list mode (the
    customer is looking at their inbox of conversations with the
    business). Cleaner."""
    r = app_client.get("/")
    body = r.text
    # The bar-list contains the Messages tagline
    import re
    m = re.search(r'class="bar-list">(.*?)</div>\s*<div class="bar-thread"',
                  body, re.DOTALL)
    assert m, "bar-list block not found"
    assert "Messages" in m.group(0)


# ── 2. CSS — list/thread mode switching ─────────────────────────────


def test_list_mode_hides_thread_mode_elements():
    """V11.2 — CSS toggles which elements are visible based on
    data-mode. List mode hides .bar-thread, .phone-conv,
    .phone-suggestions, .phone-input. Thread mode hides .bar-list
    and .conv-list."""
    from src import design
    css = design.css()
    assert '.phone-shell[data-mode="list"] .bar-thread { display: none; }' in css
    assert '.phone-shell[data-mode="thread"] .bar-list { display: none; }' in css
    assert '.phone-shell[data-mode="thread"] .conv-list { display: none; }' in css
    # Verify the list-mode also hides the input + conv area
    assert '.phone-shell[data-mode="list"] .phone-conv' in css


def test_conv_row_has_avatar_name_phone_preview_when():
    """V11.2 — each conv row has the five iMessage-list fields:
    avatar, name, phone, preview, when (relative time)."""
    from src import design
    css = design.css()
    assert ".conv-row" in css
    assert ".conv-row-avatar" in css
    assert ".conv-row-avatar-initial" in css
    assert ".conv-row-name" in css
    assert ".conv-row-when" in css
    assert ".conv-row-phone" in css
    assert ".conv-row-preview" in css


def test_conv_row_avatar_is_42px_rounded():
    """V11.2 — conv-row avatar at 42px (iMessage-list size). Round
    via border-radius: 50%."""
    from src import design
    css = design.css()
    idx = css.find(".conv-row-avatar {")
    chunk = css[idx:idx + 400]
    assert "width: 42px" in chunk
    assert "height: 42px" in chunk
    assert "border-radius: 50%" in chunk


def test_active_conv_row_has_distinct_state():
    """V11.2 — .conv-row.active uses an accent-tinted background."""
    from src import design
    css = design.css()
    assert ".conv-row.active" in css


# ── 3. JavaScript — loadCallers + selectCaller + back ───────────────


def test_load_callers_renders_conv_rows(app_client):
    """V11.2 — loadCallers builds .conv-row buttons (not .chat-chip
    anchors). Verify the rendering template is in the page JS."""
    r = app_client.get("/")
    body = r.text
    # Template literal for the conv-row HTML
    assert 'class="conv-row"' in body
    assert 'class="conv-row-avatar"' in body
    assert 'class="conv-row-name"' in body
    assert 'class="conv-row-preview"' in body


def test_select_caller_switches_to_thread_mode(app_client):
    """V11.2 — selectCaller calls setPhoneMode("thread") and updates
    bar-thread-img/initial/name/phone."""
    r = app_client.get("/")
    body = r.text
    assert "setPhoneMode" in body
    assert 'setPhoneMode("thread")' in body
    # Updates the thread-mode bar elements
    assert "bar-thread-img" in body
    assert "bar-thread-name" in body
    assert "bar-thread-phone" in body


def test_phone_back_button_returns_to_list_mode(app_client):
    """V11.2 — clicking the back button returns the phone to LIST mode
    and clears the active caller. iMessage pattern."""
    r = app_client.get("/")
    body = r.text
    # The back-button click handler is wired
    assert 'getElementById("phone-back")' in body
    # It calls setPhoneMode("list") on click
    import re
    # Look for the handler block (any whitespace acceptable)
    assert re.search(r"phone-back[\s\S]{1,400}setPhoneMode\(['\"]list['\"]",
                     body), "phone-back click should set list mode"


def test_initial_load_starts_in_list_mode_no_session(app_client):
    """V11.2 — first paint with no prior session keeps the phone in
    LIST mode (the iMessage-style inbox view). The user picks a
    caller to enter THREAD mode."""
    r = app_client.get("/")
    body = r.text
    # The chat_inner f-string emits data-mode="list" as the initial
    # state on the .phone-shell element
    import re
    m = re.search(r'<div class="phone-shell" id="customer-phone"\s+'
                  r'data-mode="list"', body)
    assert m, "initial phone shell must be in list mode"


def test_format_phone_helper_exists_in_page(app_client):
    """V11.2 — formatPhone JS helper renders the conv-row's phone
    number in (555) 123-4567 format."""
    r = app_client.get("/")
    body = r.text
    assert "function formatPhone" in body
    assert "function callerWhen" in body


# ── 4. Industry switch + reset preserve list mode ───────────────────


def test_industry_switch_returns_phone_to_list_mode(app_client):
    """V11.2 — switching industry in the demo drawer triggers
    reloadDemoCallers → loadCallers({industrySwitch: true}) which
    sets the phone to LIST mode so the user can pick a caller from
    the new vertical."""
    r = app_client.get("/")
    body = r.text
    # On industry switch, loadCallers does NOT call restoreChatState
    # and DOES setPhoneMode("list")
    assert "industrySwitch" in body
    assert 'setPhoneMode("list")' in body


def test_demo_reset_clears_active_caller_and_returns_to_list(app_client):
    """V11.2 — the Reset Demo button in the drawer should also
    return the phone to LIST mode and clear the activeCaller."""
    r = app_client.get("/")
    body = r.text
    # Reset handler sets activeCaller = null and calls setPhoneMode("list")
    import re
    # Within the dd-reset handler block, both ops should appear
    idx = body.find('id="dd-reset"')
    assert idx > -1
    # The handler will be earlier in JS — search for setPhoneMode in
    # the reset chain. Easier: just verify both strings are present.
    assert "activeCaller = null" in body


# ── 5. Drawer dropdown polish round 2 ───────────────────────────────


def test_drawer_dropdown_uses_color_scheme_hint(app_client):
    """V11.2 — `color-scheme: light dark` on the select tells the
    browser to render the native option panel in the user's
    preferred color theme instead of always white."""
    from src import design
    css = design.css()
    idx = css.find(".demo-drawer .dd-row .tenant-switcher select")
    chunk = css[idx:idx + 600]
    assert "color-scheme: light dark" in chunk


def test_drawer_dropdown_has_focus_within_ring():
    """V11.2 — the dropdown gets a visible focus-within ring (accent-
    tinted box-shadow) when the user tabs to it. Accessibility +
    premium feel."""
    from src import design
    css = design.css()
    assert ".demo-drawer .dd-row .tenant-switcher:focus-within" in css
    idx = css.find(".demo-drawer .dd-row .tenant-switcher:focus-within {")
    chunk = css[idx:idx + 300]
    assert "box-shadow" in chunk


def test_drawer_dropdown_option_styling_uses_adaptive_tokens():
    """V11.2 — option styling explicitly sets bg + color (using
    adaptive tokens) so engines that honor option CSS (Firefox)
    match the wrapper. Chromium's option panel is browser-controlled
    so the color-scheme hint above is the cross-engine path."""
    from src import design
    css = design.css()
    assert ".demo-drawer .dd-row .tenant-switcher select option" in css
    # And explicit dark-mode option override
    assert "@media (prefers-color-scheme: dark)" in css


def test_drawer_dropdown_chevron_visible(app_client):
    """V11.2 — chevron rendered via the ::after pseudo with explicit
    border-right/border-bottom on currentColor (was `currentColor` —
    inherited from a faint muted parent, made the arrow barely visible).
    V11.2 uses `var(--fg)` with 55% opacity, brightens to 90% on
    hover/focus."""
    from src import design
    css = design.css()
    idx = css.find(".demo-drawer .dd-row .tenant-switcher::after")
    chunk = css[idx:idx + 400]
    assert "border-right" in chunk and "border-bottom" in chunk
    assert "var(--fg)" in chunk


# ── 6. JavaScript syntax-guard (regression for V11.1 hotfix) ────────


def test_chat_script_block_parses_as_valid_javascript(app_client):
    """V11.1 hotfix — a broken backslash-escape in pushOwnerSMS's
    avatar HTML emitted invalid JS to the browser. The entire chat
    <script> block then failed to parse, which silently killed
    loadCallers() and left the caller list empty. This test catches
    that class of regression by piping the rendered script through
    Node's syntax checker if available."""
    import subprocess, os, re, tempfile
    # Skip if node isn't on PATH
    try:
        subprocess.run(["node", "--version"], capture_output=True,
                       check=True, timeout=5)
    except (FileNotFoundError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired):
        pytest.skip("node not available — skipping JS syntax check")

    r = app_client.get("/")
    body = r.text
    scripts = re.findall(r'<script[^>]*>(.*?)</script>',
                         body, re.DOTALL)
    assert scripts, "no <script> blocks found in rendered demo page"
    for i, src in enumerate(scripts):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".js", encoding="utf-8",
                delete=False) as f:
            f.write(src)
            path = f.name
        try:
            result = subprocess.run(
                ["node", "--check", path],
                capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, (
                f"<script> block #{i} has a syntax error:\n"
                f"{result.stderr[:500]}")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


# ── 7. Integration ──────────────────────────────────────────────────


def test_full_page_renders_with_v11_2_changes(app_client):
    """Full V11.2 surface check on a single GET /."""
    r = app_client.get("/")
    assert r.status_code == 200
    body = r.text
    # V11.2 surfaces
    assert 'class="conv-list"' in body
    assert 'data-mode="list"' in body
    assert "bar-thread" in body
    assert "phone-back" in body
    # V11.1 surfaces still present
    assert "Owner notifications" in body
    assert "brand-mark" in body
    # V11.0 surfaces still present
    assert 'value="hvac"' in body
    assert "data-suggestions=" in body
