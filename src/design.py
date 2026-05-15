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

  /* Surface tokens — V9.2 warmed near-white so cards can elevate. */
  --bg:      #fbfcfd;
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

  /* V9.2 — slightly stronger shadows so cards lift off the warmed bg. */
  --shadow-sm: 0 1px 2px rgba(15,23,42,.04), 0 1px 1px rgba(15,23,42,.02);
  --shadow-md: 0 4px 12px rgba(15,23,42,.06), 0 1px 2px rgba(15,23,42,.04);
  --shadow-lg: 0 12px 32px rgba(15,23,42,.08), 0 2px 6px rgba(15,23,42,.04);
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
  font-size: 15px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  font-feature-settings: "ss01", "cv11";
}
/* V9.4 — explicit type scale for content headings. Page-level H1
   lives in `header.page` below; these handle in-body section headers. */
h2, .h2 { font-size: 22px; font-weight: 600;
           letter-spacing: -0.015em; line-height: 1.25;
           color: var(--fg); margin: 0; }
h3, .h3 { font-size: 17px; font-weight: 600;
           letter-spacing: -0.005em; line-height: 1.3;
           color: var(--fg); margin: 0; }

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
.sidebar {
  background: var(--card-bg);
  border-right: 1px solid var(--border);
  padding: 28px 16px 16px;
}
/* V9.4 — sidebar brand reads as a confident workspace marker, not a
   small chip. Bigger type, slightly more space. */
.sidebar .brand {
  display: flex; align-items: center; gap: 10px;
  font-weight: 700; font-size: 16px; margin-bottom: 24px;
  letter-spacing: -0.01em; color: var(--fg);
  padding: 0 8px;
}
.sidebar .brand .dot {
  width: 10px; height: 10px; border-radius: 999px;
  background: var(--accent); display: inline-block;
  flex-shrink: 0;
}
.sidebar nav { display: flex; flex-direction: column; gap: 1px; }
.sidebar nav a {
  display: flex; align-items: center; position: relative;
  padding: 9px 12px; border-radius: 6px;
  color: var(--muted); font-weight: 500; font-size: 14px;
  transition: background 120ms, color 120ms;
}
.sidebar nav a:hover { background: var(--n-100); color: var(--fg);
                        text-decoration: none; }
/* V9.4 — Linear-style active state: subtle background + a tight
   accent bar on the left edge. Stronger weight on the label. */
.sidebar nav a[aria-current="page"] {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
.sidebar nav a[aria-current="page"]::before {
  content: ""; position: absolute; left: -16px; top: 8px; bottom: 8px;
  width: 3px; border-radius: 0 3px 3px 0;
  background: var(--accent);
}
@media (prefers-color-scheme: dark) {
  .sidebar nav a:hover { background: #17223d; }
}

/* V9.1 — mobile bottom-tab-bar pattern. Below 640px (one-handed
   phone), the sidebar becomes a fixed bottom dock with horizontal
   tabs. The page content adds matching bottom padding so the last
   item isn't hidden under it. */
@media (max-width: 640px) {
  .app { grid-template-columns: 1fr; }
  .sidebar {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 20;
    border-right: none; border-top: 1px solid var(--border);
    background: var(--card-bg);
    padding: 4px 8px env(safe-area-inset-bottom, 8px);
    box-shadow: 0 -4px 12px rgba(15,23,42,.04);
  }
  .sidebar .brand { display: none; }
  .sidebar nav {
    flex-direction: row; gap: 0;
    justify-content: space-around;
  }
  .sidebar nav a {
    flex: 1; justify-content: center; min-width: 0;
    padding: 12px 6px; border-radius: 6px; font-size: 12px;
    font-weight: 500; text-align: center; position: relative;
  }
  /* V9.2 — active-tab indicator pip on the bottom dock. */
  .sidebar nav a[aria-current="page"] {
    background: transparent; color: var(--accent);
  }
  .sidebar nav a[aria-current="page"]::before {
    content: ""; position: absolute; top: 0; left: 50%;
    transform: translateX(-50%);
    width: 28px; height: 3px; border-radius: 0 0 3px 3px;
    background: var(--accent);
  }
  .main { padding-bottom: 88px; }
}
/* 641-820px: tablet-ish — sidebar on top, stacked */
@media (min-width: 641px) and (max-width: 820px) {
  .app { grid-template-columns: 1fr; }
  .sidebar { border-right: none; border-bottom: 1px solid var(--border); }
  .sidebar nav { flex-direction: row; flex-wrap: wrap; gap: 4px; }
  .sidebar nav a { flex: 0 0 auto; }
}

.main {
  padding: 8px 36px 48px;
  max-width: 1100px;
  width: 100%;
}
@media (max-width: 820px) {
  .main { padding: 8px 24px 48px; }
}
@media (max-width: 640px) {
  .main { padding: 4px 16px 48px; }
}

/* V9.4 — page header is now typography-led. No more border-bottom; the
   space between header and body does the visual separation. H1 takes
   real estate so it actually feels like a page anchor. */
header.page {
  display: flex; align-items: flex-end; justify-content: space-between;
  gap: var(--s-5); margin: 24px 0 32px;
}
header.page h1 {
  margin: 0; font-size: 32px; font-weight: 700;
  letter-spacing: -0.025em; line-height: 1.15;
  color: var(--fg);
}
header.page .subtitle { color: var(--muted); font-size: 13.5px;
                         margin-top: 6px; font-weight: 500;
                         letter-spacing: 0.01em; text-transform: uppercase; }
header.page .head-aside { display: flex; align-items: center;
                           gap: 10px; flex-shrink: 0; }
@media (max-width: 640px) {
  header.page { margin: 16px 0 24px; flex-wrap: wrap; }
  header.page h1 { font-size: 26px; }
}

/* ── Card variants (V9.4) ─────────────────────────────────────────── */
/* Three variants used compositionally:
   - .card.solid  (default) — white surface, soft border, soft shadow
                    for grouped data and primary content blocks.
   - .card.soft   — tinted bg with no border, for context/asides and
                    follow-up sections that should de-emphasize.
   - .card.flush  — zero padding; lets contained components own their
                    own padding (data tables, call lists, threads).
   The card() helper composes these via the `variant` kwarg. */
.card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: var(--shadow-sm);
  padding: 24px;
  margin-bottom: 20px;
}
.card.soft {
  background: var(--n-50);
  border: 1px solid transparent;
  box-shadow: none;
}
@media (prefers-color-scheme: dark) {
  .card.soft { background: #0c1628; border-color: #1a2541; }
}
.card.flush { padding: 0; }
.card h2 {
  margin: 0 0 16px;
  font-size: 17px; font-weight: 600; letter-spacing: -0.005em;
  color: var(--fg);
}
.card.flush h2 { padding: 20px 24px 0; margin-bottom: 0; }
.card.flush > .data, .card.flush > table.data { margin-top: 12px; }
.card.flush .empty { padding: 40px 24px; }
.card h2.sub { font-size: 12px; text-transform: uppercase; color: var(--muted);
               letter-spacing: .05em; font-weight: 600; margin-bottom: var(--s-3); }
/* V9.4 — section caption that lives ABOVE a card, not inside it.
   Used for the "Recent activity" / "Worth a follow-up" labels above
   flush cards on Today. Reads as a magazine section, not as a card
   header. */
.section-caption { font-size: 12px; font-weight: 600;
                    color: var(--muted); text-transform: uppercase;
                    letter-spacing: 0.06em;
                    margin: 28px 4px 12px; }
.section-caption:first-child { margin-top: 8px; }

/* V9.4 — Today hero: bare typographic intro, no card chrome. */
.today-hero { display: flex; align-items: flex-end;
               justify-content: space-between; gap: 24px;
               margin: 0 0 8px; flex-wrap: wrap; }
.today-hero-text { min-width: 0; max-width: 720px; }
.today-headline { font-size: 28px; font-weight: 700;
                   letter-spacing: -0.02em; line-height: 1.2;
                   color: var(--fg); margin: 0; }
.today-sub { color: var(--muted); font-size: 15px;
              margin: 8px 0 0; max-width: 560px;
              line-height: 1.5; }
@media (max-width: 640px) {
  .today-hero { gap: 14px; }
  .today-headline { font-size: 24px; }
  .today-sub { font-size: 14px; }
}

/* V9.4 — conversation thread hero: bare typographic, no card. */
.thread-hero { margin: 0 0 12px; }
.thread-hero .back-link { display: inline-block; margin-bottom: 12px;
                           font-size: 12px; color: var(--muted);
                           font-weight: 500; }
.thread-hero-row { display: flex; align-items: center;
                    justify-content: space-between; gap: 16px;
                    flex-wrap: wrap; }
.thread-hero-name { font-size: 28px; font-weight: 700;
                     letter-spacing: -0.02em; line-height: 1.15;
                     color: var(--fg); margin: 0; }
.thread-hero-phone { font-size: 14px; margin-top: 4px;
                      font-variant-numeric: tabular-nums; }
@media (max-width: 640px) {
  .thread-hero-name { font-size: 24px; }
}

/* V9.4 — list-count micro-label above a flush list. Bare typographic. */
.list-count { font-size: 13px; color: var(--muted);
               margin: 0 4px 12px; font-weight: 500; }

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
/* V9.2 — sentence-case label (not ALL CAPS); slightly bigger numeral.
   V9.3 — dark-mode contrast fix: use --muted (which IS inverted in
   dark mode) instead of --n-600 (which isn't). */
.stat .label { font-size: 13px; color: var(--muted);
                font-weight: 500; letter-spacing: 0; }
.stat .value { font-size: 30px; font-weight: 700; letter-spacing: -0.02em;
               margin-top: 6px; line-height: 1.1; color: var(--fg); }
.stat .delta { margin-top: 6px; font-size: 12px; font-weight: 500; }
/* V10.3 — sparkline slot at the bottom of stat cards. */
.stat .stat-spark { margin-top: 10px; opacity: 0.85;
                     display: block; line-height: 0; }
.stat .stat-spark .spark { width: 100%; max-width: 140px; }
.stat .stat-spark .spark path { stroke: var(--accent); }
.stat .stat-spark .spark path.fill { fill: var(--accent-soft); opacity: 0.7; }
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
/* V10.4 — SVG glyph slot inside status pills. Matches dot sizing
   so a status_pill with a glyph reads at the same height. */
.pill .pill-glyph { display: inline-flex; align-items: center;
                     color: currentColor; line-height: 0; }
.pill.good { background: var(--success-100); color: var(--success-500); }
.pill.warn { background: var(--warn-100); color: var(--warn-500); }
.pill.bad  { background: var(--danger-100); color: var(--danger-500); }
.pill.info { background: var(--accent-soft); color: var(--accent); }
.pill.ghost { background: var(--n-100); color: var(--n-600); }
/* V9.3 — pills had dark-on-darker color/bg pairs in dark mode that
   failed contrast. Brighten the foreground for legibility. */
@media (prefers-color-scheme: dark) {
  .pill.good  { background: #06291f; color: #4ade80; }
  .pill.warn  { background: #2e1f08; color: #fbbf24; }
  .pill.bad   { background: #2e0d0d; color: #fb7185; }
  .pill.info  { background: #16213d; color: #93c5fd; }
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

/* V9.2 — warmed empty state for the portal's communications surfaces.
   Tiny inline icon + small headline + soft sub-copy. Calm, not cold. */
.empty-warm { padding: 40px 24px; }
.empty-warm .empty-icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent);
  margin: 0 auto var(--s-3);
}
.empty-warm .empty-title { color: var(--fg);
                            font-size: 16px; font-weight: 600;
                            margin-bottom: 4px; }
.empty-warm .empty-sub { color: var(--muted); font-size: 14px;
                          line-height: 1.45; max-width: 360px;
                          margin: 0 auto; }

/* ── Icon (inline SVG slot) ────────────────────────────────────────── */
.icon { display: inline-flex; vertical-align: -2px;
         stroke: currentColor; fill: none; }
.icon svg { display: block; }

/* ── Call card (communications-as-primary-object) ──────────────────── */
.call { display: grid;
         grid-template-columns: 44px 1fr auto;
         gap: var(--s-4); align-items: start;
         padding: 18px var(--s-5);
         border-bottom: 1px solid var(--border);
         transition: background 120ms ease, box-shadow 120ms ease;
         position: relative; }
.call:last-child { border-bottom: none; }
/* V9.2 — stronger hover: subtle wash + accent edge so the row feels
   tactile, not table-ish. */
.call:hover { background: var(--accent-soft); }
.call:hover::before {
  content: ""; position: absolute; left: 0; top: 8px; bottom: 8px;
  width: 3px; border-radius: 0 3px 3px 0;
  background: var(--accent);
}
@media (prefers-color-scheme: dark) {
  .call:hover { background: #16213d; }
}
/* V9.3 — color-hashed avatars: each partner gets a stable hue so the
   conversations list reads as distinct people, not a wall of sameness.
   Hue is set inline via `style="--av-h: NNN"` from Python; the CSS
   here defines a calm saturation/lightness pair that works in light
   and dark mode. */
.call .av { width: 44px; height: 44px; border-radius: 999px;
             background: hsl(var(--av-h, 220), 70%, 95%);
             color: hsl(var(--av-h, 220), 45%, 38%);
             display: flex; align-items: center; justify-content: center;
             font-weight: 600; font-size: 15px; flex-shrink: 0;
             position: relative; overflow: hidden; }
@media (prefers-color-scheme: dark) {
  .call .av { background: hsl(var(--av-h, 220), 25%, 22%);
               color: hsl(var(--av-h, 220), 60%, 78%); }
}
/* V9.6.1 — photo avatar layer. The initial sits behind as a fallback
   visible only when the <img> fails to load (onerror sets display:none). */
.call .av-img { position: absolute; inset: 0;
                 width: 100%; height: 100%;
                 object-fit: cover; border-radius: 999px;
                 background: hsl(var(--av-h, 220), 70%, 95%); }
@media (prefers-color-scheme: dark) {
  .call .av-img { background: hsl(var(--av-h, 220), 25%, 22%); }
}
.call .av-initial { position: relative; z-index: 0; }
.call .body { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.call .body .who { font-weight: 600; font-size: 15px; color: var(--fg);
                    overflow: hidden; text-overflow: ellipsis;
                    white-space: nowrap; line-height: 1.3; }
/* V9.6.1 — phone stacks under name as a block so the column aligns
   across cards. Previously inline with margin-left, so a long name
   pushed the phone around. */
.call .body .from { display: block;
                     color: var(--muted); font-size: 13px;
                     font-weight: 400; margin: 0;
                     font-variant-numeric: tabular-nums;
                     overflow: hidden; text-overflow: ellipsis;
                     white-space: nowrap; }
.call .body .sum { margin-top: 6px; font-size: 14px; color: var(--n-700);
                    line-height: 1.45;
                    overflow: hidden; text-overflow: ellipsis;
                    display: -webkit-box; -webkit-line-clamp: 2;
                    -webkit-box-orient: vertical; }
@media (prefers-color-scheme: dark) {
  .call .body .sum { color: #c5d0e3; }
}
.call .right { text-align: right; font-size: 12px; color: var(--muted);
                display: flex; flex-direction: column; gap: 6px;
                align-items: flex-end; flex-shrink: 0; }
.call .right .when { font-size: 12px; color: var(--muted);
                      font-variant-numeric: tabular-nums; }
/* Mobile: bigger tap targets, right column shrinks but stays visible. */
@media (max-width: 640px) {
  .call { padding: 16px var(--s-4); gap: var(--s-3);
           grid-template-columns: 40px 1fr auto; }
  .call .av { width: 40px; height: 40px; font-size: 14px; }
  .call .body .who { font-size: 15px; }
  .call .body .from { font-size: 12px; }
  .call .right { font-size: 11px; }
}

/* V9.4 — back-link helper (used by thread-hero on conversation_detail
   and call_detail). The V9.3 .call-detail-head pattern was retired
   in favor of the unified .thread-hero block. */
.back-link { display: inline-block; margin-bottom: 12px;
              font-size: 12px; color: var(--muted); font-weight: 500; }
.back-link:hover { color: var(--accent); text-decoration: none; }

/* ── V10.2 — expandable call cards ────────────────────────────────── */
/* Native <details>/<summary> for inline expansion. No JS dependency
   in the real portal; the demo pane's JS still works the same. */
details.call.call-expandable { display: block; padding: 0; }
details.call.call-expandable > summary.call-summary {
  list-style: none;
  cursor: pointer;
  display: grid;
  grid-template-columns: 44px 1fr auto;
  gap: var(--s-4); align-items: start;
  padding: 18px var(--s-5);
}
details.call.call-expandable > summary.call-summary::-webkit-details-marker {
  display: none;
}
details.call.call-expandable[open] > summary.call-summary {
  border-bottom: 1px solid var(--border);
}
.call-chevron { display: inline-block; margin-left: 8px;
                 color: var(--muted); font-size: 18px; line-height: 1;
                 transition: transform 160ms ease, color 160ms; }
details.call.call-expandable[open] > summary .call-chevron {
  transform: rotate(90deg);
  color: var(--accent);
}

.call-preview { padding: 18px 24px 20px;
                 background: var(--n-50);
                 font-size: 14px; line-height: 1.5; }
@media (prefers-color-scheme: dark) {
  .call-preview { background: #0c1628; }
}
.call-preview .preview-empty { color: var(--muted); font-style: italic; }
.call-preview .preview-bubbles {
  display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px;
}
.call-preview .preview-bubble { max-width: 80%; padding: 8px 12px;
                                 border-radius: 14px; font-size: 13.5px;
                                 line-height: 1.45;
                                 white-space: pre-wrap; word-wrap: break-word; }
.call-preview .preview-bubble.in {
  background: #eef0f3; color: #0b1220; align-self: flex-start;
}
.call-preview .preview-bubble.out {
  background: var(--accent); color: var(--accent-fg);
  align-self: flex-end;
}
@media (prefers-color-scheme: dark) {
  .call-preview .preview-bubble.in { background: #1a2541; color: #e6edf7; }
}
.call-preview .preview-foot {
  display: flex; align-items: center; gap: 12px; margin-top: 12px;
  padding-top: 12px; border-top: 1px solid var(--border);
  font-size: 13px; color: var(--muted);
}
.call-preview .preview-foot a { color: var(--accent); font-weight: 500; }

/* V10.3 — recording playback mock. A small <button> inside the
   preview triggers an animated waveform for ~3.5s. No audio plays —
   the visual carries the "this was a real recorded call" signal. */
.call-preview .rec-player {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 14px; margin-bottom: 14px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 12px;
}
.rec-play-btn {
  width: 32px; height: 32px; border-radius: 999px;
  border: none; background: var(--accent); color: var(--accent-fg);
  cursor: pointer; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 12px; padding: 0;
  transition: filter 120ms;
}
.rec-play-btn:hover { filter: brightness(0.95); }
.rec-play-btn::before {
  content: ""; display: inline-block;
  width: 0; height: 0;
  border-left: 9px solid currentColor;
  border-top: 6px solid transparent;
  border-bottom: 6px solid transparent;
  margin-left: 2px;
}
.rec-player.playing .rec-play-btn::before {
  border-left: none;
  border-right: none;
  width: 9px; height: 9px;
  background: currentColor;
  margin-left: 0;
  border-radius: 1px;
}
.rec-waveform {
  display: inline-flex; gap: 2px;
  align-items: center; height: 24px; flex: 1;
}
.rec-waveform span {
  display: inline-block;
  width: 3px; height: 6px;
  background: var(--muted);
  border-radius: 1px;
  transition: height 200ms ease, background 200ms;
}
.rec-player.playing .rec-waveform span {
  background: var(--accent);
  animation: wave-pulse 0.9s ease-in-out infinite;
}
.rec-player.playing .rec-waveform span:nth-child(2)  { animation-delay: 0.05s; }
.rec-player.playing .rec-waveform span:nth-child(3)  { animation-delay: 0.10s; }
.rec-player.playing .rec-waveform span:nth-child(4)  { animation-delay: 0.15s; }
.rec-player.playing .rec-waveform span:nth-child(5)  { animation-delay: 0.20s; }
.rec-player.playing .rec-waveform span:nth-child(6)  { animation-delay: 0.25s; }
.rec-player.playing .rec-waveform span:nth-child(7)  { animation-delay: 0.30s; }
.rec-player.playing .rec-waveform span:nth-child(8)  { animation-delay: 0.35s; }
.rec-player.playing .rec-waveform span:nth-child(9)  { animation-delay: 0.40s; }
.rec-player.playing .rec-waveform span:nth-child(10) { animation-delay: 0.45s; }
@keyframes wave-pulse {
  0%, 100% { height: 4px; }
  50%      { height: 18px; }
}
.rec-meta { font-size: 12px; color: var(--muted);
             font-variant-numeric: tabular-nums;
             white-space: nowrap; flex-shrink: 0; }

/* V10.3 — Conversations list search input. Slim, focused, drops
   into the partner-count slot at the top of the list. */
.conv-search {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--card-bg);
  margin-bottom: 16px;
}
.conv-search input {
  border: none; background: transparent;
  font-family: var(--font); font-size: 14px;
  color: var(--fg); outline: none; flex: 1;
}
.conv-search input::placeholder { color: var(--muted); }
.conv-search .conv-search-icon { color: var(--muted);
                                   display: inline-flex; }

/* V10.3 — tenant switcher in the demo top bar. */
.tenant-switcher {
  display: inline-flex; align-items: center; gap: 6px;
  margin-left: 16px;
  padding: 5px 10px;
  border-radius: 999px;
  background: var(--n-100);
  font-size: 12px; font-weight: 500;
  color: var(--n-700);
  cursor: pointer; position: relative;
  border: 1px solid transparent;
}
.tenant-switcher:hover { background: var(--n-200); }
.tenant-switcher select {
  appearance: none; border: none; background: transparent;
  font-family: var(--font); font-size: 12px; font-weight: 500;
  color: var(--n-700); cursor: pointer; padding-right: 14px;
  outline: none;
}
.tenant-switcher::after {
  content: ""; position: absolute; right: 10px; top: 50%;
  transform: translateY(-25%);
  width: 5px; height: 5px;
  border-right: 1.5px solid currentColor;
  border-bottom: 1.5px solid currentColor;
  transform: translateY(-50%) rotate(45deg);
  pointer-events: none;
}
@media (prefers-color-scheme: dark) {
  .tenant-switcher { background: #182338; color: #b2c2db; }
  .tenant-switcher:hover { background: #1f2e4d; }
  .tenant-switcher select { color: #b2c2db; }
}
@media (max-width: 640px) {
  .call-preview { padding: 14px 16px 16px; }
  .call-preview .preview-bubble { font-size: 14px; max-width: 88%; }
}

/* V10.2 — "Now" pulsing micro-badge for partners with activity in
   the last ~60s. Same green palette as the operator-pane Live pulse. */
.live-mini { display: inline-flex; align-items: center; gap: 5px;
              padding: 1px 7px; border-radius: 999px;
              background: var(--success-100); color: var(--success-500);
              font-size: 10.5px; font-weight: 600;
              text-transform: uppercase; letter-spacing: .04em;
              margin-left: 8px; vertical-align: 1px; }
.live-mini-dot { width: 5px; height: 5px; border-radius: 999px;
                  background: var(--success-500);
                  box-shadow: 0 0 0 0 rgba(22,163,74,0.45);
                  animation: live-breathe 2.2s ease-in-out infinite; }
@media (prefers-color-scheme: dark) {
  .live-mini { background: #06291f; color: #4ade80; }
  .live-mini-dot { background: #4ade80; }
}

/* V10.2 — brief flash on a card that just got new activity (set by
   the demo pane's JS after a /demo/today refresh that included it). */
.call.just-updated, details.call.just-updated {
  animation: just-updated 1.6s ease-out;
}
@keyframes just-updated {
  0%   { background: var(--card-bg); }
  20%  { background: var(--accent-soft); }
  100% { background: var(--card-bg); }
}

/* ── V9.2 — communication thread bubbles ──────────────────────────── */
/* Design notes:
   - 2px gap WITHIN a sender series, 14px between role switches.
   - No per-bubble timestamps (too noisy, too low-contrast). Single
     time-chip separator appears at the top of each meaningful gap.
   - Sender caption shows once per series, not per bubble.
   - Inbound bubble warmed from cool slate-100 (#f1f5f9) to a slightly
     warmer near-neutral so it doesn't fight the white card surface. */
.thread-block { padding: var(--s-5) var(--s-5) var(--s-4);
                 border-bottom: 1px solid var(--border); }
.thread-block:last-child { border-bottom: none; }
.thread-head { display: flex; align-items: center; gap: var(--s-2);
                margin-bottom: var(--s-4); font-size: 13px;
                color: var(--muted); }
.thread-head .thread-icon { color: var(--accent); display: inline-flex; }
.thread-head .thread-meta b { color: var(--fg); font-weight: 600; }

.bubbles { display: flex; flex-direction: column; gap: 2px; margin: 0; }

/* Time-chip — anchors the eye when there's a real gap. V9.3: dark-
   mode-aware via --muted (was --n-500 which doesn't invert). */
.time-chip { align-self: center; margin: 16px auto 10px;
              padding: 2px 10px; border-radius: 999px;
              font-size: 11px; color: var(--muted);
              background: var(--n-100);
              font-weight: 500; letter-spacing: .01em; }
.time-chip:first-child { margin-top: 0; }
.thread-block .bubbles > .time-chip:first-child { margin-top: 0; }
@media (prefers-color-scheme: dark) {
  .time-chip { background: #16213d; color: #8ea4c1; }
}

/* Sender caption — once per series, aligned with bubble's outer edge.
   V9.3 fix: previous margin of 8px 12px indented captions away from
   their bubbles. Now flush with the bubble side. */
.sender-cap { font-size: 11px; color: var(--muted);
               font-weight: 600; letter-spacing: .02em;
               margin: 6px 0 4px; padding: 0 4px;
               text-transform: none; }
.sender-cap.in  { align-self: flex-start; }
.sender-cap.out { align-self: flex-end; }
.sender-cap:first-child { margin-top: 0; }
/* Reduce the gap when caption follows a time-chip — they pair. */
.time-chip + .sender-cap { margin-top: 2px; }

.bubble { max-width: 78%; padding: 9px 14px; border-radius: 16px;
           font-size: 14.5px; line-height: 1.45;
           white-space: pre-wrap; word-wrap: break-word;
           box-shadow: 0 1px 1px rgba(15,23,42,.03); }
.bubble.in  { background: #eef0f3; color: #0b1220;
               align-self: flex-start; }
.bubble.out { background: var(--accent); color: var(--accent-fg);
               align-self: flex-end; }
/* Bottom-of-series bubbles add a small bottom margin so the next
   role's content reads as a distinct group. */
.bubble.series-end { margin-bottom: 6px; }

@media (prefers-color-scheme: dark) {
  .bubble.in { background: #1a2541; color: #e6edf7; }
}

/* Mobile: full-width breathing room, slightly bigger bubble text. */
@media (max-width: 640px) {
  .thread-block { padding: var(--s-4); }
  .bubble { max-width: 88%; font-size: 15px; padding: 10px 14px; }
}

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

/* ── V9.5 — combined demo shell ───────────────────────────────────────
   Public-facing showcase at /. Strips marketing copy and shows the
   actual product in two panes (customer side + operator side). The
   design tokens are shared with the real portal so the demo IS the
   product, not a marketing rendering of it. */
body.demo-page { background: var(--bg); min-height: 100vh; }
.demo-top {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 32px;
  background: var(--card-bg);
  border-bottom: 1px solid var(--border);
}
.demo-brand {
  display: inline-flex; align-items: center; gap: 10px;
  font-weight: 700; font-size: 16px; letter-spacing: -0.01em;
  color: var(--fg); text-decoration: none;
}
.demo-brand .dot {
  width: 10px; height: 10px; border-radius: 999px;
  background: var(--accent); flex-shrink: 0;
}
.demo-phone-link {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 8px 14px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent);
  font-weight: 600; font-size: 13px;
  text-decoration: none;
  font-variant-numeric: tabular-nums;
  transition: filter 120ms;
}
.demo-phone-link:hover { filter: brightness(0.96); text-decoration: none; }

.demo-stage {
  max-width: 1280px; margin: 0 auto;
  padding: 48px 32px 80px;
  display: grid; grid-template-columns: 420px 1fr;
  gap: 36px; align-items: start;
}
.demo-pane { display: flex; flex-direction: column; }
.pane-label {
  font-size: 12px; font-weight: 600;
  color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.08em;
  margin: 0 0 14px 6px;
  display: flex; align-items: center; gap: 10px;
}
/* V9.6 — small "Live" indicator on the operator pane label. Pulses
   subtly when the pane refreshes after a chat message lands. */
.live-pulse { display: inline-flex; align-items: center; gap: 6px;
               padding: 2px 8px; border-radius: 999px;
               background: var(--success-100); color: var(--success-500);
               text-transform: none; letter-spacing: 0.01em;
               font-size: 10.5px; font-weight: 600;
               transition: filter 200ms; }
.live-pulse .live-dot { width: 6px; height: 6px; border-radius: 999px;
                         background: var(--success-500);
                         box-shadow: 0 0 0 0 rgba(22,163,74,0.5);
                         animation: live-breathe 2.2s ease-in-out infinite; }
.live-pulse-flash { filter: brightness(1.1); }
.live-pulse-flash .live-dot { animation-duration: 0.6s; }
@keyframes live-breathe {
  0%   { box-shadow: 0 0 0 0 rgba(22,163,74,0.35); }
  70%  { box-shadow: 0 0 0 8px rgba(22,163,74,0); }
  100% { box-shadow: 0 0 0 0 rgba(22,163,74,0); }
}
@media (prefers-color-scheme: dark) {
  .live-pulse { background: #06291f; color: #4ade80; }
  .live-pulse .live-dot { background: #4ade80; }
}

/* Phone shell — abstract device frame, no notch / no skeuomorphism. */
.phone-shell {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 28px;
  box-shadow: 0 32px 64px rgba(15,23,42,0.10),
              0 12px 24px rgba(15,23,42,0.04),
              0 2px 4px rgba(15,23,42,0.03);
  overflow: hidden;
  display: flex; flex-direction: column;
}
.phone-bar {
  padding: 16px 20px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--card-bg);
}
.phone-bar .biz { font-weight: 600; font-size: 15px;
                   letter-spacing: -0.005em; color: var(--fg); }
.phone-bar .biz-sub { font-size: 12px; color: var(--muted);
                       margin-top: 2px;
                       font-variant-numeric: tabular-nums; }

.phone-screen {
  display: flex; flex-direction: column;
  min-height: 580px; max-height: 700px;
  background: var(--bg);
}

/* V10.3 — iOS-style status bar at the top of each phone screen.
   9:41 time + signal + WiFi + battery icons. Cosmetic but signals
   "real device" subliminally to the prospect. */
.phone-status {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 18px 6px;
  font-size: 12px; font-weight: 600;
  color: var(--fg); letter-spacing: -0.01em;
  background: var(--card-bg);
  border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
}
.phone-status .ps-time { font-weight: 700; }
.phone-status .ps-right { display: inline-flex; align-items: center;
                           gap: 5px; }
.phone-status .ps-icon { display: inline-block;
                          width: 14px; height: 10px; }
.phone-status .ps-icon svg { width: 100%; height: 100%;
                              stroke: currentColor; fill: currentColor; }
.phone-status .ps-battery {
  display: inline-flex; align-items: center;
  width: 22px; height: 11px;
  border: 1px solid currentColor; border-radius: 3px;
  position: relative; padding: 1px;
}
.phone-status .ps-battery::after {
  content: ""; position: absolute; top: 3px; right: -3px;
  width: 2px; height: 5px;
  background: currentColor; border-radius: 0 1px 1px 0;
}
.phone-status .ps-battery .ps-bat-fill {
  display: block; width: 78%; height: 100%;
  background: currentColor; border-radius: 1px;
}

.chat-chips { display: flex; gap: 6px; padding: 12px 14px;
               border-bottom: 1px solid var(--border);
               overflow-x: auto; flex-shrink: 0;
               background: var(--card-bg); }
.chat-chips::-webkit-scrollbar { display: none; }
.chat-chip { display: inline-flex; align-items: center; gap: 6px;
              padding: 6px 10px; border-radius: 999px;
              background: var(--n-100); color: var(--n-700);
              font-size: 12px; font-weight: 500;
              border: 1px solid transparent;
              cursor: pointer; white-space: nowrap;
              text-decoration: none;
              transition: background 120ms; }
.chat-chip:hover { background: var(--n-200); text-decoration: none; }
.chat-chip.active { background: var(--accent-soft); color: var(--accent);
                     border-color: var(--accent); font-weight: 600; }
.chat-chip .av { width: 20px; height: 20px; border-radius: 999px;
                  display: inline-flex; align-items: center; justify-content: center;
                  font-size: 10px; font-weight: 700;
                  background: hsl(var(--av-h, 220), 70%, 90%);
                  color: hsl(var(--av-h, 220), 45%, 38%);
                  position: relative; overflow: hidden; flex-shrink: 0; }
.chat-chip .av-img { position: absolute; inset: 0;
                      width: 100%; height: 100%; object-fit: cover;
                      background: hsl(var(--av-h, 220), 70%, 90%); }
.chat-chip .av-initial { position: relative; z-index: 0; }
@media (prefers-color-scheme: dark) {
  .chat-chip { background: #182338; color: #b2c2db; }
  .chat-chip:hover { background: #1f2e4d; }
  .chat-chip .av { background: hsl(var(--av-h, 220), 25%, 22%);
                    color: hsl(var(--av-h, 220), 60%, 78%); }
  .chat-chip .av-img { background: hsl(var(--av-h, 220), 25%, 22%); }
}

.phone-conv { flex: 1; padding: 18px 16px;
               overflow-y: auto;
               display: flex; flex-direction: column; gap: 10px; }

.phone-suggestions { display: flex; gap: 6px; padding: 8px 14px 0;
                      flex-wrap: wrap; flex-shrink: 0; }
.phone-suggestion { font-size: 12px; padding: 6px 10px;
                     border-radius: 999px;
                     background: var(--accent-soft); color: var(--accent);
                     border: none; cursor: pointer; font-weight: 500; }
.phone-suggestion:hover { filter: brightness(0.95); }

.phone-input { border-top: 1px solid var(--border);
                padding: 12px 14px;
                display: flex; gap: 8px;
                background: var(--card-bg); flex-shrink: 0; }
.phone-input input { flex: 1; padding: 10px 14px;
                      border: 1px solid var(--border);
                      border-radius: 999px;
                      font-family: var(--font); font-size: 14px;
                      background: var(--bg); color: var(--fg);
                      outline: none;
                      transition: border-color 120ms; }
.phone-input input:focus { border-color: var(--accent); }
.phone-input button { border: none; background: var(--accent);
                       color: var(--accent-fg);
                       width: 38px; height: 38px;
                       border-radius: 999px;
                       cursor: pointer; flex-shrink: 0;
                       display: inline-flex; align-items: center; justify-content: center;
                       font-size: 18px;
                       transition: filter 120ms; }
.phone-input button:hover { filter: brightness(0.95); }
.phone-input button:disabled { background: var(--n-300);
                                cursor: not-allowed; }

/* Phone-shell-internal chat bubbles. Same vocabulary as .bubble but
   scoped under .phone-conv so the spacing reads chat-app-native. */
.phone-conv .pmsg { max-width: 80%; padding: 9px 14px;
                     border-radius: 18px; font-size: 14px;
                     line-height: 1.45;
                     white-space: pre-wrap; word-wrap: break-word; }
.phone-conv .pmsg.user { background: var(--accent); color: var(--accent-fg);
                          align-self: flex-end;
                          border-bottom-right-radius: 6px; }
.phone-conv .pmsg.ai { background: var(--n-100); color: var(--fg);
                        align-self: flex-start;
                        border-bottom-left-radius: 6px; }
@media (prefers-color-scheme: dark) {
  .phone-conv .pmsg.ai { background: #1a2541; color: #e6edf7; }
}
.phone-conv .psys { color: var(--muted); font-size: 11px;
                     align-self: center; text-align: center;
                     padding: 4px 12px; }
.phone-conv .pmsg.loading { color: var(--muted); font-style: italic; }
/* V10.3 — animated three-dot typing indicator replaces the static "…"
   loading bubble. iMessage-standard interaction signal. */
.phone-conv .pmsg.typing { padding: 12px 16px;
                            display: inline-flex; gap: 4px;
                            align-items: center; min-height: 36px;
                            background: var(--n-100); }
@media (prefers-color-scheme: dark) {
  .phone-conv .pmsg.typing { background: #1a2541; }
}
.phone-conv .pmsg.typing span { width: 6px; height: 6px;
                                 border-radius: 999px;
                                 background: var(--muted);
                                 animation: typing-bounce 1.2s ease-in-out infinite; }
.phone-conv .pmsg.typing span:nth-child(2) { animation-delay: 0.18s; }
.phone-conv .pmsg.typing span:nth-child(3) { animation-delay: 0.36s; }
@keyframes typing-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
  30%           { transform: translateY(-4px); opacity: 1; }
}

/* V10.3 — owner-SMS preview phone. Sits beneath the customer phone
   in the left column. Visualizes the briefings the AI sends to the
   business owner's personal phone — the third actor in the
   conversation (customer / AI / owner).
   Same .phone-shell chrome; .phone-conv inside uses sms-style
   bubbles all aligned left (inbound from "AI Receptionist"). */
.owner-shell { margin-top: 22px; }
.owner-shell .biz-sub { color: var(--muted); }
.owner-shell .phone-screen { min-height: 220px; max-height: 360px; }
.owner-conv { padding: 14px 14px 6px;
               overflow-y: auto; flex: 1;
               display: flex; flex-direction: column; gap: 8px; }
.owner-conv:empty::before {
  content: "Bob will see emergency briefings here.";
  color: var(--muted); font-size: 12px; align-self: center;
  margin-top: 24px;
}
.owner-sms {
  background: var(--n-100); color: var(--fg);
  padding: 8px 12px 9px; border-radius: 14px;
  max-width: 92%; align-self: flex-start;
  font-size: 13px; line-height: 1.4;
  border-bottom-left-radius: 4px;
}
@media (prefers-color-scheme: dark) {
  .owner-sms { background: #1a2541; color: #e6edf7; }
}
.owner-sms .sms-from { font-size: 11px; color: var(--muted);
                        font-weight: 600; margin-bottom: 3px; }
.owner-sms.urgent { background: var(--danger-100);
                     color: var(--danger-500); }
.owner-sms.urgent .sms-from { color: var(--danger-500); }
@media (prefers-color-scheme: dark) {
  .owner-sms.urgent { background: #2e0d0d; color: #fb7185; }
  .owner-sms.urgent .sms-from { color: #fb7185; }
}
.owner-sms .sms-ts { font-size: 10px; color: var(--muted);
                      margin-top: 4px; }
.owner-sms.urgent .sms-ts { color: rgba(251,113,133,0.7); }
/* New-arrival animation when an SMS slides in. */
.owner-sms.just-arrived {
  animation: sms-arrive 360ms cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes sms-arrive {
  from { opacity: 0; transform: translateY(-6px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* V10.4 — "Updated Xs ago" indicator inside the operator pane label.
   Subtle temporal anchor so the prospect sees the portal IS live. */
.refresh-indicator {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 1px 8px; border-radius: 999px;
  background: var(--n-100); color: var(--muted);
  font-size: 10.5px; font-weight: 500;
  text-transform: none; letter-spacing: 0;
  font-variant-numeric: tabular-nums;
  margin-left: 8px;
}
@media (prefers-color-scheme: dark) {
  .refresh-indicator { background: #182338; color: #b2c2db; }
}

/* V10.4 — keyboard shortcuts overlay. Modal opens on `?` key. */
.shortcut-overlay {
  position: fixed; inset: 0; z-index: 100;
  background: rgba(15, 23, 42, 0.55);
  display: flex; align-items: center; justify-content: center;
  opacity: 0; pointer-events: none;
  transition: opacity 160ms ease;
}
.shortcut-overlay.shown { opacity: 1; pointer-events: auto; }
.shortcut-modal {
  background: var(--card-bg); color: var(--fg);
  border-radius: 16px; padding: 24px 26px;
  max-width: 360px; width: calc(100% - 48px);
  box-shadow: 0 28px 56px rgba(15,23,42,0.24);
  transform: translateY(8px); transition: transform 200ms ease;
}
.shortcut-overlay.shown .shortcut-modal { transform: translateY(0); }
.shortcut-modal h3 {
  font-size: 15px; font-weight: 600; margin: 0 0 14px;
}
.shortcut-modal ul { list-style: none; padding: 0; margin: 0;
                      display: flex; flex-direction: column; gap: 10px; }
.shortcut-modal li {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 13px;
}
.shortcut-modal li span:last-child {
  font-family: var(--font-mono); font-size: 11px;
  background: var(--n-100); padding: 3px 7px;
  border-radius: 5px; color: var(--muted);
}
@media (prefers-color-scheme: dark) {
  .shortcut-modal li span:last-child { background: #182338; }
}
.shortcut-modal .sm-foot {
  margin-top: 18px; padding-top: 14px;
  border-top: 1px solid var(--border);
  text-align: center; font-size: 11px; color: var(--muted);
}

/* V10.4 — floating demo control. Bottom-right, low-key. */
.demo-control {
  position: fixed; right: 20px; bottom: 20px; z-index: 50;
  display: flex; gap: 8px; align-items: center;
  background: var(--card-bg); border: 1px solid var(--border);
  border-radius: 999px; padding: 6px 10px 6px 14px;
  box-shadow: 0 8px 20px rgba(15,23,42,0.08);
  font-size: 12px; color: var(--muted);
}
.demo-control button {
  border: none; cursor: pointer; padding: 5px 10px;
  border-radius: 999px; font-size: 12px; font-weight: 500;
  background: transparent; color: var(--fg);
  transition: background 120ms;
}
.demo-control button:hover { background: var(--n-100); }
.demo-control .dc-divider { width: 1px; height: 14px;
                             background: var(--border); }
.demo-control .dc-pause.paused { background: var(--accent-soft);
                                  color: var(--accent); }
@media (max-width: 480px) {
  .demo-control { bottom: 12px; right: 12px;
                   padding: 4px 8px 4px 12px; }
}

/* V10.4 — incoming-call banner. Slides down from the top of the
   customer phone when a caller is picked. Auto-accepts after 700ms.
   Sets up the "they're calling, the AI is about to pick up" beat. */
.call-banner {
  position: absolute; top: 0; left: 0; right: 0; z-index: 6;
  background: var(--accent); color: var(--accent-fg);
  padding: 12px 16px;
  display: flex; align-items: center; gap: 12px;
  font-size: 13px; font-weight: 600;
  transform: translateY(-100%);
  transition: transform 280ms cubic-bezier(0.16, 1, 0.3, 1);
  border-radius: 28px 28px 0 0;
}
.call-banner.shown { transform: translateY(0); }
.call-banner .cb-glyph {
  width: 28px; height: 28px; border-radius: 999px;
  background: rgba(255,255,255,0.18);
  display: inline-flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  animation: cb-pulse 1.1s ease-in-out infinite;
}
.call-banner .cb-text { flex: 1; line-height: 1.25; }
.call-banner .cb-name { font-weight: 700; }
.call-banner .cb-sub  { font-size: 11px; opacity: 0.8;
                          font-weight: 500; margin-top: 1px; }
@keyframes cb-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(255,255,255,0.4); }
  60%      { box-shadow: 0 0 0 8px rgba(255,255,255,0); }
}

/* V10.4 — live call timer in the phone bar. Ticks while a "call" is
   active (after the prospect accepts the incoming banner). */
.phone-bar .call-timer {
  margin-left: auto;
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 11px; color: var(--muted);
  font-variant-numeric: tabular-nums;
  opacity: 0; transition: opacity 200ms;
}
.phone-bar .call-timer.active { opacity: 1; }
.phone-bar .call-timer .ct-dot {
  width: 5px; height: 5px; border-radius: 999px;
  background: var(--success-500);
  animation: live-breathe 2.2s ease-in-out infinite;
}
@media (prefers-color-scheme: dark) {
  .phone-bar .call-timer .ct-dot { background: #4ade80; }
}

/* V10.4 — end-of-call summary card slides into the chat after the
   conversation reaches a closing signal. Different visual register
   than a normal bubble — calmer, framed, signals "this thread is
   wrapping up." */
.phone-conv .call-summary-card {
  align-self: stretch; margin: 12px 0 4px;
  padding: 14px 16px; border-radius: 14px;
  background: var(--accent-soft); border: 1px solid var(--border);
  font-size: 13px; line-height: 1.45; color: var(--fg);
  animation: cs-slide 360ms cubic-bezier(0.16, 1, 0.3, 1);
}
.phone-conv .call-summary-card .cs-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--accent);
  margin-bottom: 8px;
}
.phone-conv .call-summary-card .cs-row {
  display: flex; gap: 8px; padding: 3px 0;
}
.phone-conv .call-summary-card .cs-row b {
  min-width: 78px; font-weight: 600; color: var(--muted);
  font-size: 12px; padding-top: 1px;
}
.phone-conv .call-summary-card .cs-row span { color: var(--fg); flex: 1; }
@keyframes cs-slide {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* V10.4 — iOS-style red notification badge on the owner phone bar.
   Shows the count of unread SMS briefings. */
.owner-shell .phone-bar { position: relative; }
.owner-shell .biz-badge {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 18px; height: 18px; padding: 0 5px;
  border-radius: 999px; background: var(--danger-500);
  color: white; font-size: 10.5px; font-weight: 700;
  margin-left: 8px;
  font-variant-numeric: tabular-nums; line-height: 1;
}

/* V10.4 — recording progress bar under the waveform. Animates
   0:00 → end-of-clip during the 3.5s mock playback. */
.rec-player { flex-wrap: wrap; }
.rec-progress {
  width: 100%; height: 3px; margin-top: 8px;
  background: var(--n-200); border-radius: 999px;
  position: relative; overflow: hidden;
  flex-basis: 100%;
}
@media (prefers-color-scheme: dark) {
  .rec-progress { background: #1e2a44; }
}
.rec-progress-fill {
  position: absolute; top: 0; left: 0; bottom: 0;
  width: 0; background: var(--accent);
  border-radius: 999px;
  transition: width 100ms linear;
}
.rec-player.playing .rec-progress-fill {
  width: 100%;
  transition: width 3500ms linear;
}

/* V10.3 — first-visit onboarding pointer. Pulses over the first
   caller chip until the prospect clicks any chip. localStorage-gated
   so it doesn't reappear after dismissal. */
.onboard-pointer {
  position: absolute; left: 14px; top: 56px;
  background: var(--fg); color: var(--bg);
  padding: 8px 12px; border-radius: 8px;
  font-size: 12px; font-weight: 500;
  box-shadow: 0 6px 14px rgba(15,23,42,0.18);
  pointer-events: none; z-index: 5;
  white-space: nowrap;
  animation: pointer-bob 1.8s ease-in-out infinite;
}
.onboard-pointer::after {
  content: ""; position: absolute; top: -5px; left: 18px;
  width: 10px; height: 10px; background: var(--fg);
  transform: rotate(45deg);
}
@keyframes pointer-bob {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-3px); }
}

/* V10.3 — read receipts under the customer's outbound bubbles.
   "Delivered" appears instantly; "Read" replaces it after ~200ms
   (set by the chat JS). iMessage micro-detail. */
.phone-conv .receipt { font-size: 10.5px; color: var(--muted);
                        align-self: flex-end;
                        margin: 0 4px -2px;
                        opacity: 0; transition: opacity 200ms ease;
                        letter-spacing: .02em; }
.phone-conv .receipt.shown { opacity: 1; }
.phone-conv .receipt.read { color: var(--accent); }
.phone-conv .pmeta { display: flex; gap: 4px; flex-wrap: wrap;
                      margin-top: 6px; align-self: flex-start; }
.phone-conv .pmeta .tag { font-size: 10px; font-weight: 600;
                           padding: 2px 7px; border-radius: 999px;
                           background: var(--accent-soft);
                           color: var(--accent);
                           text-transform: lowercase;
                           letter-spacing: 0.02em; }
.phone-conv .pmeta .tag.emergency { background: var(--danger-100);
                                     color: var(--danger-500); }

/* Portal shell — minimal browser-window framing for the right pane. */
.portal-shell {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: 0 32px 64px rgba(15,23,42,0.10),
              0 12px 24px rgba(15,23,42,0.04),
              0 2px 4px rgba(15,23,42,0.03);
  overflow: hidden;
}
.window-bar {
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
  display: flex; gap: 7px; align-items: center;
  background: var(--card-bg);
}
.window-bar .dot {
  width: 11px; height: 11px; border-radius: 999px;
  display: inline-block;
}
.window-bar .dot.red { background: #ff5f57; }
.window-bar .dot.amber { background: #febc2e; }
.window-bar .dot.green { background: #28c840; }
.window-bar .url-pill {
  margin-left: auto;
  font-size: 11px; color: var(--muted);
  background: var(--n-100); padding: 3px 10px;
  border-radius: 999px;
  font-variant-numeric: tabular-nums;
}
.portal-shell-body {
  padding: 28px 32px 32px;
  max-height: 820px; overflow-y: auto;
}
/* Tone down the embedded today body — drop margins on the headline
   since the shell provides outer padding. */
.portal-shell-body .today-hero { margin-top: 0; }
.portal-shell-body .section-caption:first-of-type { margin-top: 16px; }

@media (max-width: 900px) {
  .demo-stage { grid-template-columns: 1fr; padding: 24px 16px 48px;
                  gap: 24px; }
  .demo-top { padding: 14px 16px; }
  .phone-shell { max-width: 440px; margin: 0 auto; width: 100%; }
  .portal-shell-body { padding: 20px 18px; max-height: none; }
}
/* V10.4 — under 480 the phone-shells are full width and stacked
   tighter. The owner-shell collapses its tall min-height so two
   phones + portal fit in a reasonable scroll. */
@media (max-width: 480px) {
  .demo-phone-link span:last-child { display: none; }
  .demo-phone-link { padding: 8px; }
  .demo-stage { padding: 16px 12px 40px; gap: 16px; }
  .demo-pane-customer .pane-label { margin-bottom: 8px; }
  .phone-shell { border-radius: 22px; }
  .phone-screen { min-height: 460px; max-height: 560px; }
  .owner-shell .phone-screen { min-height: 180px; max-height: 240px; }
  .pane-label { font-size: 11px; gap: 6px; }
  .live-pulse { font-size: 9.5px; padding: 1px 6px; }
  .tenant-switcher { margin-left: 8px; }
  .tenant-switcher select { font-size: 11px; }
}

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
<script>
/* V10.3 — recording-mock play button wiring. Toggles the .playing
   class on the .rec-player for ~3.5s so the waveform animates.
   No actual audio is loaded; the visual carries the signal. */
document.addEventListener("click", function(e){{
  const btn = e.target.closest(".rec-play-btn");
  if (!btn) return;
  e.preventDefault();
  const player = btn.closest(".rec-player");
  if (!player) return;
  if (player.classList.contains("playing")){{
    player.classList.remove("playing");
    return;
  }}
  player.classList.add("playing");
  setTimeout(function(){{ player.classList.remove("playing"); }}, 3500);
}});
</script>
</body></html>"""


# ── Components ─────────────────────────────────────────────────────────

def card(body: str, *, title: Optional[str] = None,
         subtitle: Optional[str] = None, flush: bool = False,
         variant: str = "solid") -> str:
    """V9.4 — three variants:
        solid (default) — white card-bg, soft border + shadow.
        soft            — tinted bg, no border, no shadow. For asides
                          and context blocks that should de-emphasize.
        flush           — kept as a separate flag for backwards-compat;
                          orthogonal to variant (you can have solid+flush
                          or soft+flush).
    """
    variant = variant if variant in ("solid", "soft") else "solid"
    classes = ["card"]
    if variant == "soft":
        classes.append("soft")
    if flush:
        classes.append("flush")
    head = ""
    if title:
        head = f'<h2>{html.escape(title)}</h2>'
    if subtitle:
        head += f'<div class="muted" style="margin-bottom:var(--s-3)">{html.escape(subtitle)}</div>'
    return f'<section class="{" ".join(classes)}">{head}{body}</section>'


def section_caption(text: str) -> str:
    """V9.4 — a magazine-style section caption that sits ABOVE a flush
    card (e.g., "Recent activity"). Doesn't add a card chrome — just a
    confident typographic anchor."""
    return f'<div class="section-caption">{html.escape(text)}</div>'


def demo_page(*, title: str, body: str,
              phone_number: str = "+1 (844) 940-3274",
              tel_href: str = "tel:+18449403274") -> str:
    """V9.5 — public-facing combined demo shell.

    Differs from `page()`: no sidebar nav, no per-tenant brand, just a
    minimal top bar with the product mark and the live demo phone
    number. The body is expected to be a split-screen of two panes.
    Uses the same `_CSS` design tokens so the demo and the real portal
    share one visual system.
    """
    phone_label = html.escape(phone_number)
    tel = html.escape(tel_href)
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="AI Receptionist live demo — see the customer side and the operator side, in real time.">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head><body data-accent="brand" class="demo-page">
<header class="demo-top">
  <a href="/" class="demo-brand">
    <span class="dot"></span><span>AI Receptionist</span>
  </a>
  <div class="tenant-switcher" id="tenant-switcher" title="Switch demo industry">
    <select aria-label="Demo industry">
      <option value="septic"      data-brand="Septic Pro" data-owner="Bob"
              data-emergency="My toilets are backing up and there's sewage in the basement!"
              data-book="Hey, I need to schedule a routine pumping."
              data-price="How much does a pump-out cost?">Septic Pro</option>
      <option value="hvac"        data-brand="Sunrise HVAC" data-owner="Mike"
              data-emergency="My furnace died overnight and it's 12 degrees inside."
              data-book="Need someone to look at my AC — not cooling well."
              data-price="What does a tune-up cost?">Sunrise HVAC</option>
      <option value="real-estate" data-brand="Lawrence Realty" data-owner="Lauren"
              data-emergency="I lost my keys, I'm locked out of the showing!"
              data-book="I saw the Birch Road listing — can I tour Saturday?"
              data-price="What's the asking price on 1100 Birch?">Lawrence Realty</option>
    </select>
  </div>
  <a href="{tel}" class="demo-phone-link" style="margin-left:auto;">
    <svg width="14" height="14" viewBox="0 0 24 24"
         stroke="currentColor" fill="none" stroke-width="1.75"
         stroke-linecap="round" stroke-linejoin="round">
      <path d="M6.6 10.8a13 13 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11 11 0 0 0 3.5.55 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1 11 11 0 0 0 .55 3.5 1 1 0 0 1-.25 1Z"/>
    </svg>
    <span>{phone_label}</span>
  </a>
</header>
{body}
<script>
/* V10.3 — recording-mock play button. Toggles .playing for 3.5s. */
document.addEventListener("click", function(e){{
  const btn = e.target.closest(".rec-play-btn");
  if (!btn) return;
  e.preventDefault();
  const player = btn.closest(".rec-player");
  if (!player) return;
  if (player.classList.contains("playing")){{
    player.classList.remove("playing"); return;
  }}
  player.classList.add("playing");
  setTimeout(function(){{ player.classList.remove("playing"); }}, 3500);
}});
/* V10.3 / V10.4 — tenant switcher: swap brand label + suggestion
   buttons + owner-phone label + chat industry context propagated to
   /chat. Genuine end-to-end industry switch even though the DB stays
   on septic_pro (the demo's marketing tenant). */
(function(){{
  const $sw = document.getElementById("tenant-switcher");
  if (!$sw) return;
  const $sel = $sw.querySelector("select");
  function applyIndustry(opt){{
    const brand = opt.getAttribute("data-brand");
    const owner = opt.getAttribute("data-owner");
    const e = opt.getAttribute("data-emergency");
    const b = opt.getAttribute("data-book");
    const p = opt.getAttribute("data-price");
    /* Customer phone .biz label */
    const custBiz = document.querySelector(
      ".demo-pane-customer .phone-shell:not(.owner-shell) .phone-bar .biz");
    if (custBiz && brand) custBiz.textContent = brand;
    /* Owner phone .biz label */
    const ownerBiz = document.querySelector(
      ".owner-shell .phone-bar .biz");
    if (ownerBiz && owner) ownerBiz.textContent = owner + "'s phone";
    /* Suggestion buttons */
    document.querySelectorAll(".phone-suggestion").forEach((btn, i)=>{{
      if (i === 0 && e) btn.dataset.msg = e;
      if (i === 1 && b) btn.dataset.msg = b;
      if (i === 2 && p) btn.dataset.msg = p;
    }});
    /* Propagate the industry + owner name to the chat JS so the
       next /chat call sends the right context. Window-level globals
       written into the demo page's main script. */
    if (typeof window !== "undefined"){{
      window.currentIndustry = opt.value;
      window.currentOwnerName = owner || window.currentOwnerName;
    }}
  }}
  $sel.addEventListener("change", function(){{
    applyIndustry($sel.options[$sel.selectedIndex]);
  }});
  /* Apply the initial selection so currentIndustry is in sync with
     whatever localStorage / URL state restored. */
  applyIndustry($sel.options[$sel.selectedIndex]);
}})();
</script>
</body></html>"""


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
              direction: str = "flat",
              sparkline_values: Optional[list] = None) -> str:
    """direction: 'up' | 'down' | 'flat'.

    V10.3 — optional `sparkline_values` (list of ints) adds a small
    30-day trend line at the bottom of the card. Operational depth
    signal without overloading the visual."""
    delta_html = ""
    if delta is not None:
        direction = direction if direction in ("up", "down", "flat") else "flat"
        arrow = {"up": "▲", "down": "▼", "flat": "·"}[direction]
        delta_html = f'<div class="delta {direction}">{arrow} {html.escape(str(delta))}</div>'
    spark_html = ""
    if sparkline_values and any(v > 0 for v in sparkline_values):
        spark_html = f'<div class="stat-spark">{sparkline(sparkline_values, width=140, height=28)}</div>'
    return (
        f'<div class="stat">'
        f'<div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(str(value))}</div>'
        f'{delta_html}'
        f'{spark_html}'
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


# V10.4 — small SVG glyphs per status. Helps at-a-glance scanning so
# the prospect doesn't have to read the label to know what happened.
# Stroke-based, currentColor-aware, 12px viewbox so they fit inside
# the pill height. Falls back to the colored dot if the status doesn't
# have a dedicated glyph.
_STATUS_GLYPH = {
    "answered":      '<svg viewBox="0 0 12 12" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6.5l2 2L9 4"/></svg>',
    "transferred":   '<svg viewBox="0 0 12 12" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4h6M6 2l2 2-2 2M10 8H4M6 10l-2-2 2-2"/></svg>',
    "emergency":     '<svg viewBox="0 0 12 12" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2L1 10h10L6 2zM6 5v2M6 8.5v.01"/></svg>',
    "missed":        '<svg viewBox="0 0 12 12" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l6 6M9 3l-6 6"/></svg>',
    "voicemail":     '<svg viewBox="0 0 12 12" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="3.5" cy="6" r="1.5"/><circle cx="8.5" cy="6" r="1.5"/><path d="M3.5 7.5h5"/></svg>',
    "callback":      '<svg viewBox="0 0 12 12" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 5.5a6 6 0 0 1 6.5-3M3 3.5v2.5h2.5M9 9.5a3 3 0 0 1-3 0M7 8.5l1 1 1.5-1.5"/></svg>',
    "follow_up":     '<svg viewBox="0 0 12 12" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 6h7M6 3l3 3-3 3"/></svg>',
    "no_answer":     '<svg viewBox="0 0 12 12" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="4.5"/><path d="M6 4v2.5l1.5 1"/></svg>',
}


def status_pill(status: str, *, with_dot: bool = True) -> str:
    """Plain-English status pill. Unknown statuses fall back to a
    neutral pill with the raw (title-cased) string.

    V10.4 — when the status has a dedicated glyph, the SVG is shown
    in place of the colored dot for at-a-glance scanning."""
    s = (status or "").strip().lower()
    label, variant = _STATUS_MAP.get(
        s, (s.replace("_", " ").title() or "Unknown", "ghost"))
    glyph = _STATUS_GLYPH.get(s, "")
    if glyph:
        marker = f'<span class="pill-glyph" aria-hidden="true">{glyph}</span>'
    elif with_dot:
        marker = '<span class="dot"></span>'
    else:
        marker = ""
    return f'<span class="pill {variant}">{marker}{html.escape(label)}</span>'


def _avatar_hue(seed: str) -> int:
    """V9.3 — stable hue (0-359) derived from the partner identity so
    each card in the Conversations list has its own quiet color. Same
    partner always gets the same color across pages. The CSS variable
    `--av-h` on `.call .av` picks the bg/fg pair."""
    import hashlib
    if not seed:
        return 220  # default slate
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return int(digest[:4], 16) % 360


# V10.3 — real-photo avatars via Pravatar (i.pravatar.cc), with the
# V9.6.1 DiceBear notionists illustration as the fallback chain.
#
# Pravatar serves curated portrait photos deterministic by `u=<seed>`.
# Same phone → same photo. Free, no API key, suitable for mockups and
# demos. The image is loaded as <img>; on load failure we fall through
# the chain: pravatar → dicebear → initial disc.
#
# Notes:
#   - Pravatar is marketed as a placeholder service for prototyping.
#     The portraits are real-people likenesses; appropriate for a sales
#     demo but a production deployment with real customer photos would
#     swap to a contracted photo source or a vetted GAN-generated set.
#   - The fallback to DiceBear is automatic in call_card's <img onerror>
#     because we emit BOTH URLs and the browser tries them in order.
DICEBEAR_BASE = "https://api.dicebear.com/9.x/notionists/svg"
PRAVATAR_BASE = "https://i.pravatar.cc/150"


def partner_photo_url(seed: str) -> str:
    """V10.3 — returns the real-photo URL (Pravatar). Falls back via
    HTML `onerror` cascade to DiceBear → initial disc. Empty seed
    returns empty string."""
    if not seed:
        return ""
    try:
        from urllib.parse import quote
        try:
            from memory import normalize_phone
            s = normalize_phone(seed) or seed
        except Exception:
            s = seed
        return f"{PRAVATAR_BASE}?u={quote(s)}"
    except Exception:
        return ""


def partner_photo_fallback_url(seed: str) -> str:
    """V10.3 — the DiceBear illustration used when the Pravatar URL
    fails to load. Wired via call_card's <img onerror> handler."""
    if not seed:
        return ""
    try:
        from urllib.parse import quote
        try:
            from memory import normalize_phone
            s = normalize_phone(seed) or seed
        except Exception:
            s = seed
        return f"{DICEBEAR_BASE}?seed={quote(s)}"
    except Exception:
        return ""


def call_card(*, caller: str, from_number: str = "",
              when: str = "", summary: str = "",
              status: str = "answered", duration: Optional[str] = None,
              href: Optional[str] = None,
              photo_url: Optional[str] = None,
              preview_html: str = "",
              live: bool = False) -> str:
    """Single-call row. Communications-as-primary-object pattern.

    V10.2 — when `preview_html` is non-empty, the card becomes a
    native `<details>` element. Click expands inline showing the
    preview (typically the last 3 turns + a "View thread" link).
    No JS needed. `live=True` adds a pulsing "Now" badge for partners
    with activity in the last ~60s.

    `href` is preserved for legacy callers, but new code should pass
    `preview_html` instead — inline expansion beats a full-page
    navigation for quick triage."""
    initial = html.escape((caller or "?")[:1].upper())
    who = html.escape(caller or "Unknown caller")
    seed = from_number or caller
    hue = _avatar_hue(seed)
    # V10.2 — partner_slug uses the normalized digits (no leading "1"
    # country code) so the demo-pane JS can match by data-partner
    # against the chat caller's normalized phone consistently.
    _digits = "".join(c for c in (from_number or "") if c.isdigit())
    if len(_digits) == 11 and _digits.startswith("1"):
        _digits = _digits[1:]
    partner_slug = _digits
    fromn_html = (
        f'<div class="from">{html.escape(from_number)}</div>'
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
    live_badge = (
        '<span class="live-mini" aria-label="recently active">'
        '<span class="live-mini-dot"></span>Now</span>'
        if live else ""
    )
    if photo_url:
        # V10.3 — graceful fallback chain. <img onerror> first swaps
        # the src to the DiceBear URL (illustrated portrait); if THAT
        # also fails, the second onerror hides the image entirely so
        # the initial-letter disc behind it stays visible.
        fallback = partner_photo_fallback_url(seed)
        if fallback:
            onerr = (
                f"if(this.dataset.tried!='fallback'){{"
                f"this.dataset.tried='fallback';"
                f"this.src='{fallback}';"
                f"}}else{{this.style.display='none';}}"
            )
        else:
            onerr = "this.style.display='none'"
        avatar = (
            f'<div class="av" style="--av-h:{hue}">'
            f'<span class="av-initial">{initial}</span>'
            f'<img class="av-img" src="{html.escape(photo_url)}" alt="" '
            f'loading="lazy" '
            f'onerror="{onerr}"></div>'
        )
    else:
        avatar = f'<div class="av" style="--av-h:{hue}">{initial}</div>'

    row_inner = (
        f'{avatar}'
        f'<div class="body">'
        f'<div class="who">{who}{live_badge}</div>'
        f'{fromn_html}'
        f'{sum_html}'
        f'</div>'
        f'<div class="right">{status_pill(status)}{when_html}{dur_html}'
        + ('<span class="call-chevron" aria-hidden="true">›</span>'
            if preview_html else '')
        + '</div>'
    )

    # V10.2 — three modes:
    #   1) preview_html provided → <details> with inline expansion
    #   2) href provided           → <a> as a navigation card
    #   3) plain                   → <div> static display
    if preview_html:
        return (
            f'<details class="call call-expandable" '
            f'data-partner="{html.escape(partner_slug)}">'
            f'<summary class="call-summary">{row_inner}</summary>'
            f'<div class="call-preview">{preview_html}</div>'
            f'</details>'
        )
    if href:
        return (
            f'<a class="call" href="{html.escape(href)}" '
            f'style="color:inherit;text-decoration:none;">{row_inner}</a>'
        )
    return f'<div class="call">{row_inner}</div>'


def tabs(items: list, *, active: Optional[str] = None) -> str:
    """items: list of (label, href). Use on portal/admin tabbed views."""
    out = []
    for label, href in items:
        cur = ' aria-current="page"' if href == active else ""
        out.append(
            f'<a href="{html.escape(href)}"{cur}>{html.escape(label)}</a>'
        )
    return f'<nav class="tabs">{"".join(out)}</nav>'
