"""Shared design system for every HTML surface.

Admin, client portal, and the public landing page all pull from here so
the visual language stays consistent. Pure CSS + a small render helper —
no JavaScript, no Jinja, no frontend deps.

V9.0 — ink + slate-blue. Three accents (violet/blue/teal) collapsed to
one calm slate-blue. The `accent` parameter is kept for back-compat but
all values now produce the same visual treatment; surfaces are
differentiated by content, not color. White-label per-tenant accent
override still works via `custom_accent_hex`.

The design is light-mode-first with an explicit
`@media (prefers-color-scheme: dark)` block that inverts tokens for
users on dark-mode systems. Tokens (colors, spacing, typography) are
CSS custom properties so palette tweaks touch one place.

Usage:
    from src.design import page, card, data_table, sparkline, stat_card

    body = card(data_table([...], [...]), title="Recent calls")
    return HTMLResponse(page(
        title="Receptionist admin",
        body=body,
        nav=[("Overview", "/admin"), ("Calls", "/admin/calls")],
        active="/admin",
    ))
"""
from __future__ import annotations

import html
from typing import Iterable, Optional


# ── CSS — single source of truth ───────────────────────────────────────

_CSS = r"""
:root {
  /* Neutral scale (slate) */
  --n-50:  #f8fafc;
  --n-100: #f1f5f9;
  --n-200: #e2e8f0;
  --n-300: #cbd5e1;
  --n-400: #94a3b8;
  --n-500: #64748b;
  --n-600: #475569;
  --n-700: #334155;
  --n-800: #1e293b;
  --n-900: #0b1220;   /* ink */

  /* V9.0 — single accent across every surface (slate-blue).
     The `--ops-500` / `--client-500` / `--brand-500` aliases are
     preserved so older call sites resolve identically. */
  --accent:       #1e3a8a;
  --accent-soft:  #eef2ff;
  --accent-fg:    #ffffff;
  --ops-500:      var(--accent);
  --ops-100:      var(--accent-soft);
  --client-500:   var(--accent);
  --client-100:   var(--accent-soft);
  --brand-500:    var(--accent);
  --brand-100:    var(--accent-soft);

  /* Semantic */
  --success-500: #16a34a;
  --success-100: #dcfce7;
  --warn-500:    #b45309;
  --warn-100:    #fef3c7;
  --danger-500:  #b91c1c;
  --danger-100:  #fee2e2;

  /* Surface tokens */
  --bg:      #ffffff;
  --card-bg: #ffffff;
  --fg:      var(--n-900);
  --muted:   var(--n-500);
  --border:  var(--n-200);

  /* Spacing rhythm */
  --s-1: 4px;
  --s-2: 8px;
  --s-3: 12px;
  --s-4: 16px;
  --s-5: 24px;
  --s-6: 32px;
  --s-7: 48px;
  --s-8: 64px;

  /* Type */
  --font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
          "Helvetica Neue", Arial, "Apple Color Emoji", sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
               "Liberation Mono", monospace;

  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;

  --shadow-sm: 0 1px 2px rgba(15,23,42,.04);
  --shadow-md: 0 2px 8px rgba(15,23,42,.05), 0 1px 2px rgba(15,23,42,.04);
}

/* V9.0 — accent variants kept as no-op attributes so existing surface
   markup (admin / portal / public) still validates; visual treatment is
   identical across all three. Tenant white-label still overrides via
   custom_accent_hex below. */

@media (prefers-color-scheme: dark) {
  :root {
    --bg:      #0b1220;
    --card-bg: #111a2e;
    --fg:      #e6edf7;
    --muted:   #8aa0bd;
    --border:  #1e2a44;
    --accent:       #93c5fd;
    --accent-soft:  #16213d;
    --accent-fg:    #0b1220;
    --success-100: #052e22;
    --warn-100:    #2e1f08;
    --danger-100:  #2e0d0d;
    --shadow-sm: 0 1px 2px rgba(0,0,0,.35);
    --shadow-md: 0 2px 8px rgba(0,0,0,.4), 0 1px 2px rgba(0,0,0,.3);
  }
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--fg);
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }

code, kbd, pre { font-family: var(--font-mono); font-size: 0.92em; }
pre {
  background: var(--n-100);
  padding: var(--s-3) var(--s-4);
  border-radius: var(--radius-sm);
  overflow-x: auto;
  border: 1px solid var(--border);
}
@media (prefers-color-scheme: dark) { pre { background: #0a1426; } }

/* ── Layout shell ─────────────────────────────────────────────────── */
.app {
  display: grid;
  grid-template-columns: 240px 1fr;
  min-height: 100vh;
}
@media (max-width: 820px) {
  .app { grid-template-columns: 1fr; }
  .sidebar { border-right: none; border-bottom: 1px solid var(--border); }
}
.sidebar {
  background: var(--card-bg);
  border-right: 1px solid var(--border);
  padding: var(--s-5) var(--s-4);
}
.sidebar .brand {
  display: flex; align-items: center; gap: var(--s-2);
  font-weight: 700; font-size: 15px; margin-bottom: var(--s-5);
  letter-spacing: -0.01em;
}
.sidebar .brand .dot {
  width: 10px; height: 10px; border-radius: 999px;
  background: var(--accent); display: inline-block;
}
.sidebar nav { display: flex; flex-direction: column; gap: 2px; }
.sidebar nav a {
  display: flex; align-items: center;
  padding: 9px 12px; border-radius: var(--radius-sm);
  color: var(--n-600); font-weight: 500;
  transition: background 120ms, color 120ms;
}
.sidebar nav a:hover { background: var(--n-100); color: var(--fg); text-decoration: none; }
.sidebar nav a[aria-current="page"] {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
@media (prefers-color-scheme: dark) {
  .sidebar nav a { color: var(--n-300); }
  .sidebar nav a:hover { background: #17223d; }
}

.main {
  padding: var(--s-5) var(--s-6);
  max-width: 1240px;
  width: 100%;
}
@media (max-width: 640px) {
  .main { padding: var(--s-4); }
}

header.page {
  display: flex; align-items: flex-end; justify-content: space-between;
  gap: var(--s-4); margin-bottom: var(--s-5);
  padding-bottom: var(--s-4);
  border-bottom: 1px solid var(--border);
}
header.page h1 {
  margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.01em;
}
header.page .subtitle { color: var(--muted); font-size: 13px; margin-top: 2px; }

/* ── Card ──────────────────────────────────────────────────────────── */
.card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: var(--s-5);
  margin-bottom: var(--s-5);
}
.card.flush { padding: 0; }
.card h2 {
  margin: 0 0 var(--s-4);
  font-size: 15px; font-weight: 600; letter-spacing: -0.005em;
}
.card h2.sub { font-size: 12px; text-transform: uppercase; color: var(--muted);
               letter-spacing: .05em; font-weight: 600; margin-bottom: var(--s-3); }

/* ── Tables ────────────────────────────────────────────────────────── */
table.data {
  width: 100%;
  border-collapse: collapse;
  font-variant-numeric: tabular-nums;
}
table.data th, table.data td {
  text-align: left; padding: 14px 18px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}
table.data th {
  font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); font-weight: 600; background: transparent;
  padding-top: 10px; padding-bottom: 10px;
}
table.data tr:last-child td { border-bottom: none; }
table.data tr.row-link:hover td { background: var(--n-50); cursor: pointer; }
@media (prefers-color-scheme: dark) {
  table.data tr.row-link:hover td { background: #0c1a33; }
}
table.data td.num, table.data th.num { text-align: right; }
table.data td.muted { color: var(--muted); }

/* ── Stat cards (big numbers) ──────────────────────────────────────── */
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
         gap: var(--s-4); margin-bottom: var(--s-5); }
.stat { background: var(--card-bg); border: 1px solid var(--border);
        border-radius: var(--radius-md); padding: var(--s-4) var(--s-5);
        box-shadow: var(--shadow-sm); }
.stat .label { font-size: 11px; text-transform: uppercase; color: var(--muted);
               letter-spacing: .05em; font-weight: 600; }
.stat .value { font-size: 28px; font-weight: 700; letter-spacing: -0.02em;
               margin-top: 4px; }
.stat .delta { margin-top: 4px; font-size: 12px; font-weight: 500; }
.stat .delta.up   { color: var(--success-500); }
.stat .delta.down { color: var(--danger-500); }
.stat .delta.flat { color: var(--muted); }

/* ── Pills / chips ─────────────────────────────────────────────────── */
.pill { display: inline-flex; align-items: center; gap: 6px;
        padding: 3px 10px; border-radius: 999px;
        font-size: 11px; font-weight: 600; letter-spacing: .02em;
        line-height: 1.4; white-space: nowrap; }
.pill .dot { width: 6px; height: 6px; border-radius: 999px;
             background: currentColor; display: inline-block; }
.pill.good { background: var(--success-100); color: var(--success-500); }
.pill.warn { background: var(--warn-100); color: var(--warn-500); }
.pill.bad  { background: var(--danger-100); color: var(--danger-500); }
.pill.info { background: var(--accent-soft); color: var(--accent); }
.pill.ghost { background: var(--n-100); color: var(--n-600); }
@media (prefers-color-scheme: dark) {
  .pill.ghost { background: #182338; color: #b2c2db; }
}

/* ── Sparkline inline ──────────────────────────────────────────────── */
.spark { vertical-align: middle; }
.spark path { fill: none; stroke: var(--accent); stroke-width: 1.6; }
.spark path.fill { fill: var(--accent-soft); stroke: none; opacity: .6; }

/* ── Heatmap bar ───────────────────────────────────────────────────── */
.heatbar { display: inline-block; height: 8px; background: var(--accent);
           border-radius: 2px; opacity: .2; min-width: 2px; }

/* ── Invoice (print-friendly) ──────────────────────────────────────── */
.invoice { max-width: 720px; margin: 0 auto; }
.invoice .head { display: flex; justify-content: space-between;
                 align-items: flex-start; margin-bottom: var(--s-4); gap: var(--s-4); }
.invoice table.data th { background: transparent; }
.invoice .total td { font-weight: 700; font-size: 15px; background: var(--n-100); }
@media (prefers-color-scheme: dark) { .invoice .total td { background: #0c1a33; } }

/* ── Button ────────────────────────────────────────────────────────── */
.btn { display: inline-flex; align-items: center; gap: 6px;
       padding: 9px 16px; border-radius: var(--radius-sm);
       font-weight: 500; border: 1px solid var(--border); background: var(--card-bg);
       color: var(--fg); text-decoration: none; font-size: 13px;
       cursor: pointer; line-height: 1.3;
       transition: background 120ms, border-color 120ms, transform 120ms; }
.btn:hover { background: var(--n-100); text-decoration: none; }
@media (prefers-color-scheme: dark) { .btn:hover { background: #162139; } }
.btn.primary { background: var(--accent); border-color: var(--accent);
                color: var(--accent-fg); }
.btn.primary:hover { filter: brightness(0.95); background: var(--accent); }
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* ── Empty state ───────────────────────────────────────────────────── */
.empty { text-align: center; padding: var(--s-6) var(--s-4); color: var(--muted); }

/* ── Icon (inline SVG slot) ────────────────────────────────────────── */
.icon { display: inline-flex; vertical-align: -2px;
         stroke: currentColor; fill: none; }
.icon svg { display: block; }

/* ── Call card (communications-as-primary-object) ──────────────────── */
.call { display: grid;
         grid-template-columns: 40px 1fr auto;
         gap: var(--s-4); align-items: start;
         padding: var(--s-4) var(--s-5);
         border-bottom: 1px solid var(--border); }
.call:last-child { border-bottom: none; }
.call .av { width: 40px; height: 40px; border-radius: 999px;
             background: var(--accent-soft); color: var(--accent);
             display: flex; align-items: center; justify-content: center;
             font-weight: 600; font-size: 14px; }
.call .body .who { font-weight: 600; }
.call .body .from { color: var(--muted); font-size: 12px; }
.call .body .sum { margin-top: 4px; font-size: 13px; color: var(--n-700); }
@media (prefers-color-scheme: dark) {
  .call .body .sum { color: #b7c4dc; }
}
.call .right { text-align: right; font-size: 12px; color: var(--muted);
                display: flex; flex-direction: column; gap: 4px;
                align-items: flex-end; }

/* ── Tabs ──────────────────────────────────────────────────────────── */
.tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--border);
         margin-bottom: var(--s-5); overflow-x: auto; }
.tabs a { padding: 10px 14px; color: var(--muted); font-weight: 500;
           font-size: 13px; border-bottom: 2px solid transparent;
           margin-bottom: -1px; white-space: nowrap; }
.tabs a:hover { color: var(--fg); text-decoration: none; }
.tabs a[aria-current="page"] { color: var(--fg);
                                 border-bottom-color: var(--accent); }

/* ── Footer ────────────────────────────────────────────────────────── */
footer.page { margin-top: var(--s-6); padding-top: var(--s-4);
              border-top: 1px solid var(--border); color: var(--muted);
              font-size: 12px; display: flex; justify-content: space-between;
              gap: var(--s-4); }

/* ── Print ─────────────────────────────────────────────────────────── */
@media print {
  body { background: white; color: black; }
  .sidebar, nav, .btn, footer.page { display: none; }
  .main { padding: 0; max-width: 100%; }
  .card, .stat { box-shadow: none; border-color: #ccc; }
  .app { grid-template-columns: 1fr; }
}

.muted { color: var(--muted); }
.mono  { font-family: var(--font-mono); }
.num   { text-align: right; font-variant-numeric: tabular-nums; }
.row   { display: flex; align-items: center; gap: var(--s-3); flex-wrap: wrap; }
.ml-auto { margin-left: auto; }
"""


def css() -> str:
    """Raw CSS string — useful if a page wants to embed it verbatim."""
    return _CSS


# ── Page shell ─────────────────────────────────────────────────────────

def _hex_soft(hex_color: str) -> str:
    """Return a light tint of the given #rrggbb hex — used as --accent-soft.
    10% over white: mix each channel toward 255 at 90%."""
    h = (hex_color or "").strip().lstrip("#")
    if len(h) != 6:
        return hex_color
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return hex_color
    r = int(r + (255 - r) * 0.88)
    g = int(g + (255 - g) * 0.88)
    b = int(b + (255 - b) * 0.88)
    return f"#{r:02x}{g:02x}{b:02x}"


def page(*, title: str, body: str,
         nav: Optional[Iterable[tuple]] = None,
         active: Optional[str] = None,
         accent: str = "ops",
         subtitle: Optional[str] = None,
         brand: Optional[str] = None,
         brand_logo_url: Optional[str] = None,
         custom_accent_hex: Optional[str] = None,
         footer_note: Optional[str] = None) -> str:
    """Produce a full HTML page with the standard shell.

    Args:
        title: page <title> + H1.
        body: HTML string for the main content area.
        nav: list of (label, href) tuples for the sidebar.
        active: href of the current page (adds aria-current="page").
        accent: "ops" | "client" | "brand" — picks the accent color.
        subtitle: small text under the H1.
        brand: sidebar brand label (default "Receptionist").
        brand_logo_url: V3.10 — optional logo image for the sidebar.
        custom_accent_hex: V3.10 — overrides the accent color with a
            tenant-provided hex like "#ff6600". The matching soft tint
            is computed automatically.
        footer_note: optional right-aligned footer text.
    """
    nav = list(nav or [])
    nav_html = ""
    if nav:
        items = []
        for label, href in nav:
            cur = ' aria-current="page"' if href == active else ""
            items.append(f'<a href="{html.escape(href)}"{cur}>{html.escape(label)}</a>')
        nav_html = f"<nav>{''.join(items)}</nav>"

    brand_label = html.escape(brand or "Receptionist")
    subtitle_html = (
        f'<div class="subtitle">{html.escape(subtitle)}</div>'
        if subtitle else ""
    )
    footer_html = (
        f'<footer class="page"><span class="muted">AI Receptionist</span>'
        f'<span class="muted">{html.escape(footer_note)}</span></footer>'
        if footer_note else ""
    )

    # V3.10 — optional logo in sidebar
    logo_html = ""
    if brand_logo_url:
        logo_html = (
            f'<img src="{html.escape(brand_logo_url)}" alt="" '
            f'style="height:24px;vertical-align:middle;margin-right:6px;">'
        )

    # V3.10 — custom accent color override. We validate the hex on input
    # to avoid CSS injection; bad input falls back to the default accent.
    extra_style = ""
    body_accent = html.escape(accent)
    if custom_accent_hex:
        import re
        if re.match(r"^#[0-9a-fA-F]{6}$", custom_accent_hex.strip()):
            soft = _hex_soft(custom_accent_hex.strip())
            extra_style = (
                f'<style>body[data-accent="custom"] {{ '
                f'--accent: {html.escape(custom_accent_hex.strip())}; '
                f'--accent-soft: {html.escape(soft)}; }}</style>'
            )
            body_accent = "custom"

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
{extra_style}
</head><body data-accent="{body_accent}">
<div class="app">
  <aside class="sidebar">
    <div class="brand">{logo_html}<span class="dot"></span>{brand_label}</div>
    {nav_html}
  </aside>
  <main class="main">
    <header class="page">
      <div>
        <h1>{html.escape(title)}</h1>
        {subtitle_html}
      </div>
    </header>
    {body}
    {footer_html}
  </main>
</div>
</body></html>"""


# ── Components ─────────────────────────────────────────────────────────

def card(body: str, *, title: Optional[str] = None,
         subtitle: Optional[str] = None, flush: bool = False) -> str:
    cls = "card flush" if flush else "card"
    head = ""
    if title:
        head = f'<h2>{html.escape(title)}</h2>'
    if subtitle:
        head += f'<div class="muted" style="margin-bottom:var(--s-3)">{html.escape(subtitle)}</div>'
    return f'<section class="{cls}">{head}{body}</section>'


def data_table(headers: list, rows: list, *, empty_text: str = "No data yet.") -> str:
    """headers: list of strings OR (label, extra_class) tuples.
    rows: list of lists; each cell is either a string or a (content, cls) tuple."""
    def _render_head(h):
        if isinstance(h, tuple):
            label, cls = h
            return f'<th class="{html.escape(cls)}">{html.escape(label)}</th>'
        return f'<th>{html.escape(h)}</th>'

    def _render_cell(c):
        if isinstance(c, tuple):
            content, cls = c
            return f'<td class="{html.escape(cls)}">{content}</td>'
        return f'<td>{c}</td>'

    if not rows:
        return f'<div class="empty">{html.escape(empty_text)}</div>'
    head = "".join(_render_head(h) for h in headers)
    body = "".join(
        "<tr>" + "".join(_render_cell(c) for c in r) + "</tr>"
        for r in rows
    )
    return f'<table class="data"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def stat_card(label: str, value, *, delta: Optional[str] = None,
              direction: str = "flat") -> str:
    """direction: 'up' | 'down' | 'flat'."""
    delta_html = ""
    if delta is not None:
        direction = direction if direction in ("up", "down", "flat") else "flat"
        arrow = {"up": "▲", "down": "▼", "flat": "·"}[direction]
        delta_html = f'<div class="delta {direction}">{arrow} {html.escape(str(delta))}</div>'
    return (
        f'<div class="stat">'
        f'<div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(str(value))}</div>'
        f'{delta_html}'
        f'</div>'
    )


def stats(stats_list: list) -> str:
    """stats_list: list of pre-rendered stat_card HTML strings."""
    return f'<div class="stats">{"".join(stats_list)}</div>'


def pill(label: str, variant: str = "info") -> str:
    """variant: good | warn | bad | info | ghost"""
    variant = variant if variant in ("good", "warn", "bad", "info", "ghost") else "info"
    return f'<span class="pill {variant}">{html.escape(label)}</span>'


def sparkline(values: list, *, width: int = 80, height: int = 24) -> str:
    """Tiny SVG sparkline from a list of numbers. Gracefully handles 0/1 points."""
    if not values:
        return f'<svg class="spark" width="{width}" height="{height}"></svg>'
    vmin, vmax = min(values), max(values)
    rng = (vmax - vmin) or 1
    step = width / max(1, (len(values) - 1))
    points = [
        f"{i*step:.1f},{height - 2 - ((v - vmin)/rng) * (height - 4):.1f}"
        for i, v in enumerate(values)
    ]
    line = "M " + " L ".join(points)
    fill = f"M 0,{height} L " + " L ".join(points) + f" L {width},{height} Z"
    return (
        f'<svg class="spark" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
        f'<path class="fill" d="{fill}"/><path d="{line}"/></svg>'
    )


def heatbar(value: float, max_value: float, *, width: int = 120) -> str:
    """A single horizontal intensity bar — used for the hour-of-day heatmap."""
    if max_value <= 0:
        return f'<span class="heatbar" style="width:2px;opacity:.15"></span>'
    w = max(2, int(width * (value / max_value)))
    opacity = max(0.2, min(1.0, 0.2 + (value / max_value) * 0.8))
    return (
        f'<span class="heatbar" '
        f'style="width:{w}px;opacity:{opacity:.2f}"></span>'
    )


# ── V9.0 components ────────────────────────────────────────────────────

# Stroke-icon library. Keep this set deliberately small — operators
# don't need 200 icons, they need a calm, consistent vocabulary. Add
# only when a real surface requires one. All paths assume 24x24 viewBox
# with stroke-width 1.75; size scales via the outer wrapper.
_ICONS = {
    "phone":     'M6.6 10.8a13 13 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11 11 0 0 0 3.5.55 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1 11 11 0 0 0 .55 3.5 1 1 0 0 1-.25 1Z',
    "voicemail": 'M6 10.5a3.5 3.5 0 1 1 7 0 3.5 3.5 0 0 1-7 0Zm11.5-3.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM6 14h11.5',
    "mic":       'M12 3v10m0 0a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v4a3 3 0 0 0 3 3Zm-7-3a7 7 0 0 0 14 0M12 17v4',
    "calendar":  'M5 6h14v14H5zM5 6V4m14 2V4M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01',
    "alert":     'M12 4 2 20h20L12 4Zm0 6v4m0 3.5v.01',
    "check":     'm5 12 5 5L20 7',
    "chevron":   'm9 6 6 6-6 6',
    "arrow":     'M5 12h14M13 6l6 6-6 6',
    "transfer": 'M7 7h11l-3-3m3 3-3 3M17 17H6l3 3m-3-3 3-3',
    "search":    'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-5-5',
    "settings":  'M12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Zm8 2.5a8 8 0 0 0-.1-1.3l2-1.5-2-3.4-2.4.9a8 8 0 0 0-2.2-1.3L14.8 3h-4l-.5 2.4a8 8 0 0 0-2.2 1.3l-2.4-.9-2 3.4 2 1.5a8 8 0 0 0 0 2.6l-2 1.5 2 3.4 2.4-.9a8 8 0 0 0 2.2 1.3l.5 2.4h4l.5-2.4a8 8 0 0 0 2.2-1.3l2.4.9 2-3.4-2-1.5c.1-.4.1-.9.1-1.3Z',
    "user":      'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-8 9a8 8 0 0 1 16 0',
    "clock":     'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v5l3 2',
    "home":      'M3 11 12 3l9 8v9a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1v-9Z',
    "list":      'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
}


def icon(name: str, *, size: int = 16, cls: str = "") -> str:
    """Inline SVG icon. Returns empty string for unknown names rather
    than raising — keeps the page robust if a caller typos a name."""
    path = _ICONS.get(name)
    if not path:
        return ""
    extra = f' {cls}' if cls else ""
    return (
        f'<span class="icon{extra}" aria-hidden="true">'
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75">'
        f'<path d="{path}"/></svg></span>'
    )


# Plain-English status vocabulary. Map from internal call outcomes to
# user-facing labels and pill variants. Anything not in this map renders
# as a neutral "ghost" pill so engineer-y strings never leak through.
_STATUS_MAP = {
    "answered":      ("Answered",      "info"),
    "normal":        ("Answered",      "info"),
    "missed":        ("Missed",        "warn"),
    "no_answer":     ("No answer",     "warn"),
    "transferred":   ("Transferred",   "good"),
    "voicemail":     ("Voicemail",     "ghost"),
    "emergency":     ("Emergency",     "bad"),
    "callback":      ("Callback sent", "info"),
    "follow_up":     ("Follow-up",     "info"),
    "wrong_number":  ("Wrong number",  "ghost"),
    "spam":          ("Filtered",      "ghost"),
    "spam_number":   ("Filtered",      "ghost"),
    "spam_phrase":   ("Filtered",      "ghost"),
    "duration_capped": ("Long call",   "ghost"),
}


def status_pill(status: str, *, with_dot: bool = True) -> str:
    """Plain-English status pill. Unknown statuses fall back to a
    neutral pill with the raw (title-cased) string."""
    s = (status or "").strip().lower()
    label, variant = _STATUS_MAP.get(
        s, (s.replace("_", " ").title() or "Unknown", "ghost"))
    dot = '<span class="dot"></span>' if with_dot else ""
    return f'<span class="pill {variant}">{dot}{html.escape(label)}</span>'


def call_card(*, caller: str, from_number: str = "",
              when: str = "", summary: str = "",
              status: str = "answered", duration: Optional[str] = None,
              href: Optional[str] = None) -> str:
    """Single-call row, communications-as-primary-object pattern.
    Used on portal Today / Calls / admin recent-calls.
    `href` makes the whole card a link; omit for static rendering."""
    initial = html.escape((caller or "?")[:1].upper())
    who = html.escape(caller or "Unknown caller")
    fromn = (
        f'<span class="from">{html.escape(from_number)}</span>'
        if from_number else ""
    )
    when_html = (
        f'<div class="when">{html.escape(when)}</div>'
        if when else ""
    )
    dur_html = (
        f'<div class="dur muted">{html.escape(duration)}</div>'
        if duration else ""
    )
    sum_html = (
        f'<div class="sum">{html.escape(summary)}</div>'
        if summary else ""
    )
    inner = (
        f'<div class="av">{initial}</div>'
        f'<div class="body">'
        f'<div class="who">{who} {fromn}</div>'
        f'{sum_html}'
        f'</div>'
        f'<div class="right">{status_pill(status)}{when_html}{dur_html}</div>'
    )
    if href:
        return (
            f'<a class="call" href="{html.escape(href)}" '
            f'style="color:inherit;text-decoration:none;">{inner}</a>'
        )
    return f'<div class="call">{inner}</div>'


def tabs(items: list, *, active: Optional[str] = None) -> str:
    """items: list of (label, href). Use on portal/admin tabbed views."""
    out = []
    for label, href in items:
        cur = ' aria-current="page"' if href == active else ""
        out.append(
            f'<a href="{html.escape(href)}"{cur}>{html.escape(label)}</a>'
        )
    return f'<nav class="tabs">{"".join(out)}</nav>'
