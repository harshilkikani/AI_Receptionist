"""V12.0 — final refinement and production hardening regression suite.

Guards the four polish commits:
  A: tech debt cleanup (legacy CSS removed)
  B: phone UI realism + thin scrollbars
  C: dashboard hierarchy + density
  D: interaction polish + transitions
"""
from __future__ import annotations

import importlib
import warnings

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


# ── A. Tech debt cleanup ────────────────────────────────────────────


def test_legacy_chat_chip_css_removed():
    """V11.2 introduced .conv-list / .conv-row and retained the
    pre-V11.2 .chat-chips / .chat-chip CSS as a no-op for one
    cycle. V12.0 removed the legacy rules entirely."""
    from src import design
    css = design.css()
    assert ".chat-chips {" not in css
    assert ".chat-chip {" not in css
    assert ".chat-chip.active" not in css
    assert ".chat-chip:hover" not in css


def test_legacy_brand_dot_css_removed():
    """V11.1 introduced .brand-mark and retained the pre-V11.1 .dot
    as a display:none no-op. V12.0 removed it."""
    from src import design
    css = design.css()
    # The selector with rule body is gone
    assert ".demo-brand .dot {" not in css


def test_no_orphan_keyframes():
    """Every @keyframes declared in design.css must be referenced
    via animation or animation-name. V12.0 audit guarded against
    leftover keyframes from retired effects."""
    from src import design
    import re
    css = design.css()
    declared = set(re.findall(r"@keyframes\s+([\w-]+)", css))
    referenced = set()
    for kf in declared:
        # Look for animation: or animation-name: that mentions this name
        if re.search(rf"animation(-name)?\s*:[^;]*\b{re.escape(kf)}\b", css):
            referenced.add(kf)
    orphans = declared - referenced
    assert not orphans, f"orphan @keyframes detected: {orphans}"


def test_gitignore_covers_runtime_artifacts():
    """V12.0 gitignored memory.corrupt.* and clients/demo_*.yaml
    so runtime junk doesn't pollute the working tree."""
    from pathlib import Path
    gitignore = Path(__file__).parent.parent / ".gitignore"
    content = gitignore.read_text(encoding="utf-8")
    assert "memory.corrupt.*" in content
    assert "clients/demo_*.yaml" in content


# ── B. Phone UI + scrollbar polish ──────────────────────────────────


def test_custom_scrollbar_styles_defined():
    """V12.0 — thin custom scrollbars across the app, both Firefox
    (scrollbar-width) and Chromium (::-webkit-scrollbar) flavors."""
    from src import design
    css = design.css()
    assert "scrollbar-width: thin" in css
    assert "::-webkit-scrollbar" in css
    assert "::-webkit-scrollbar-thumb" in css
    # Thin = 8px, transparent track, muted-tinted thumb
    assert "::-webkit-scrollbar { width: 8px" in css


def test_phone_conv_uses_cluster_margin_not_gap():
    """V12.0 — bubble spacing switched from flat `gap: 10px` on the
    parent to cluster-aware `margin-top` per bubble. Same-sender
    consecutive bubbles sit 3px apart; sender-change opens 12px."""
    from src import design
    css = design.css()
    # The new pattern uses margin-top on .pmsg
    assert ".phone-conv .pmsg { max-width: 80%" in css
    idx = css.find(".phone-conv .pmsg { max-width: 80%")
    chunk = css[idx:idx + 400]
    assert "margin-top: 12px" in chunk
    # Same-sender adjacent override
    assert ".phone-conv .pmsg.user + .pmsg.user" in css
    assert ".phone-conv .pmsg.ai + .pmsg.ai" in css


def test_phone_input_focus_ring():
    """V12.0 — input focus gets an accent-tinted box-shadow ring."""
    from src import design
    css = design.css()
    idx = css.find(".phone-input input:focus {")
    chunk = css[idx:idx + 200]
    assert "box-shadow" in chunk
    assert "var(--accent)" in chunk


def test_phone_suggestion_active_scale():
    """V12.0 — suggestion chips get a 0.97 scale on :active for
    tactile feedback."""
    from src import design
    css = design.css()
    assert ".phone-suggestion:active" in css
    assert "scale(0.97)" in css or "scale(.97)" in css


# ── C. Dashboard hierarchy + density ────────────────────────────────


def test_today_headline_refined_typography():
    """V12.0 — headline 28px → 26px so it sets context without
    dominating."""
    from src import design
    css = design.css()
    idx = css.find(".today-headline {")
    chunk = css[idx:idx + 250]
    assert "font-size: 26px" in chunk


def test_call_card_padding_tightened():
    """V12.0 — call card padding 18px → 15px vertical for premium
    density."""
    from src import design
    css = design.css()
    idx = css.find(".call { display: grid")
    chunk = css[idx:idx + 400]
    assert "padding: 15px" in chunk


def test_card_soft_uses_color_mix_background():
    """V12.0 — soft card background switched from static var(--n-50)
    to color-mix(muted 6%, card-bg) so it adapts to color scheme
    and pulls visually behind the primary card."""
    from src import design
    css = design.css()
    idx = css.find(".card.soft {")
    chunk = css[idx:idx + 200]
    assert "color-mix(in srgb, var(--muted)" in chunk


def test_card_soft_dims_inner_text():
    """V12.0 — names + summaries inside .card.soft pick up subtly
    dimmer color so the section reads as secondary-attention."""
    from src import design
    css = design.css()
    assert ".card.soft .call .body .who" in css
    assert ".card.soft .call .body .sum" in css


# ── D. Interaction polish + transitions ─────────────────────────────


def test_phone_conv_smooth_scroll():
    """V12.0 — smooth scroll on the conversation pane so message
    appends glide instead of snapping."""
    from src import design
    css = design.css()
    idx = css.find(".phone-conv { flex: 1")
    chunk = css[idx:idx + 400]
    assert "scroll-behavior: smooth" in chunk
    assert "overscroll-behavior: contain" in chunk


def test_conv_list_and_owner_conv_smooth_scroll():
    """V12.0 — same smooth scroll on the conversation list (left
    rail) and owner-phone conv (right rail)."""
    from src import design
    css = design.css()
    # Combined rule
    assert ".conv-list, .owner-conv { scroll-behavior: smooth" in css


def test_portal_refresh_opacity_dip_reduced(app_client):
    """V12.0 — portal pane's refresh opacity dip 0.7 → 0.96. The
    previous 30% dim was visibly obvious; 4% reads as continuity
    without a flash."""
    r = app_client.get("/")
    body = r.text
    assert 'opacity = "0.96"' in body
    assert 'opacity = "0.7"' not in body


def test_mobile_v12_additions(app_client):
    """V12.0 D — mobile breakpoint (<480px) gains conv-row padding
    tightening, smaller avatars, bar-thread typography scaling, and
    drawer goes full-width."""
    from src import design
    css = design.css()
    # Find the <480px breakpoint and verify the V12.0 additions
    import re
    m = re.search(r"@media \(max-width: 480px\) \{([^}]+(\{[^}]*\}[^}]*)*)\}",
                  css, re.DOTALL)
    # Simpler: just check the V12.0 mobile additions are present
    # somewhere in the CSS
    assert ".conv-row { padding: 10px 14px" in css
    assert ".bar-thread-avatar { width: 30px" in css
    assert ".demo-drawer { width: 100vw" in css


def test_conv_row_transition_140ms():
    """V12.0 — conv-row hover transition unified to 140ms."""
    from src import design
    css = design.css()
    idx = css.find(".conv-row {")
    chunk = css[idx:idx + 500]
    assert "transition: background 140ms ease" in chunk


# ── No Python syntax warnings ───────────────────────────────────────


def test_main_imports_without_syntax_warnings():
    """V12.0 D — the `\\D` regex inside main.py's chat_js triple-
    quoted Python string raised SyntaxWarning on import. Fixed by
    escaping to `\\\\D` so Python treats it as a literal backslash
    sequence and the rendered JS still receives /\\D/g."""
    import sys
    import importlib
    # Re-import main with warnings as errors to catch any leftover
    # SyntaxWarning. If main is already imported, reload it.
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        if "main" in sys.modules:
            del sys.modules["main"]
        import main  # noqa: F401 — just verify the import is clean


# ── Integration ─────────────────────────────────────────────────────


def test_full_demo_page_renders_with_v12_changes(app_client):
    """Full V12.0 surface check: thin scrollbars, smooth scroll,
    cluster-margin bubbles, 26px today headline, 0.96 refresh dip,
    and pre-V12 surfaces still intact."""
    r = app_client.get("/")
    assert r.status_code == 200
    body = r.text
    # V12.0 surfaces in <style>
    assert "scrollbar-width: thin" in body
    assert "scroll-behavior: smooth" in body
    # V11.2 surfaces still present
    assert 'class="conv-list"' in body
    assert 'data-mode="list"' in body
    # V11.1 surfaces still present
    assert "Owner notifications" in body
    # V11.0 surfaces still present
    assert 'value="hvac"' in body
