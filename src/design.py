"""Shared design system for every HTML surface.

Admin, client portal, and the public landing page all pull from here so
the visual language stays consistent. Pure CSS + a small render helper —
no JavaScript, no Jinja, no frontend deps.

The design is light-mode-first with an explicit `@media (prefers-color-scheme: dark)`
block that inverts tokens for users on dark-mode systems. Tokens
(colors, spacing, typography) are CSS custom properties so future
palette tweaks touch one place.

Usage:
    from src.design import page, card, data_table, sparkline, stat_card

    body = card(data_table([...], [...]), title="Recent calls")
    return HTMLResponse(page(
        title="Receptionist admin",
        body=body,
        nav=[("Overview", "/admin"), ("Calls", "/admin/calls")],
        active="/admin",
        accent="ops",    # "ops" for admin, "client" for portal, "brand" for landing
    ))
"""
from __future__ import annotations

import html
from typing import Iterable, Optional


# ── CSS — single source of truth ───────────────────────────────────────

_CSS = r"""
:root {
  /* Neutral scale */
  --n-50:  #f8fafc;
  --n-100: #f1f5f9;
  --n-200: #e2e8f0;
  --n-300: #cbd5e1;
  --n-400: #94a3b8;
  --n-500: #64748b;
  --n-600: #475569;
  --n-700: #334155;
  --n-800: #1e293b;
  --n-900: #0f172a;

  /* Accents — one picked per surface via data-accent attr on <body> */
  --ops-500:    #2563eb;   /* blue  — operator / admin */
  --ops-100:    #dbeafe;
  --client-500: #0d9488;   /* teal  — client portal    */
  --client-100: #ccfbf1;
  --brand-500:  #7c3aed;   /* violet — public landing  */
  --brand-100:  #ede9fe;

  /* Semantic */
  --success-500: #059669;
  --success-100: #d1fae5;
  --warn-500:    #d97706;
  --warn-100:    #fed7aa;
  --danger-500:  #dc2626;
  --danger-100:  #fee2e2;

  /* Surface tokens (resolved below based on accent) */
  --bg:      var(--n-50);
  --card-bg: #ffffff;
  --fg:      var(--n-900);
  --muted:   var(--n-500);
  --border:  var(--n-200);
  --accent:  var(--ops-500);
  --accent-soft: var(--ops-100);

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

  --shadow-sm: 0 1px 2px rgba(15,23,42,.05);
  --shadow-md: 0 2px 8px rgba(15,23,42,.06), 0 1px 2px rgba(15,23,42,.04);
}

body[data-accent="client"] { --accent: var(--client-500); --accent-soft: var(--client-100); }
body[data-accent="brand"]  { --accent: var(--brand-500);  --accent-soft: var(--brand-100); }

@media (prefers-color-scheme: dark) {
  :root {
    --bg:      #0b1220;
    --card-bg: #111a2e;
    --fg:      #e6edf7;
    --muted:   #8aa0bd;
    --border:  #1e2a44;
    --ops-100:    #0c1a3a;
    --client-100: #0b2a27;
    --brand-100:  #1d163a;
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
  text-align: left; padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}
table.data th {
  font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); font-weight: 600; background: var(--n-50);
}
@media (prefers-color-scheme: dark) {
  table.data th { background: #0c1628; }
}
table.data tr:last-child td { border-bottom: none; }
table.data tr:hover td { background: var(--n-50); }
@media (prefers-color-scheme: dark) {
  table.data tr:hover td { background: #0c1a33; }
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
.pill { display: inline-block; padding: 2px 10px; border-radius: 999px;
        font-size: 11px; font-weight: 600; letter-spacing: .02em; }
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
.btn { display: inline-block; padding: 8px 14px; border-radius: var(--radius-sm);
       font-weight: 500; border: 1px solid var(--border); background: var(--card-bg);
       color: var(--fg); text-decoration: none; font-size: 13px;
       transition: background 120ms, border-color 120ms; }
.btn:hover { background: var(--n-100); text-decoration: none; }
@media (prefers-color-scheme: dark) { .btn:hover { background: #162139; } }
.btn.primary { background: var(--accent); border-color: var(--accent); color: white; }
.btn.primary:hover { filter: brightness(0.95); background: var(--accent); }

/* ── Empty state ───────────────────────────────────────────────────── */
.empty { text-align: center; padding: var(--s-6) var(--s-4); color: var(--muted); }

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
