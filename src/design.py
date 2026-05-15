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

/* V12.0 — thin custom scrollbars across the app. Replaces the
   chunky OS default that broke premium feel. Two strategies (the
   web is split): Firefox honors `scrollbar-width` + `scrollbar-color`,
   Chromium/WebKit honors `::-webkit-scrollbar`. */
* {
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--muted) 35%, transparent)
                   transparent;
}
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--muted) 30%, transparent);
  border-radius: 4px;
  border: 2px solid transparent;
  background-clip: padding-box;
}
::-webkit-scrollbar-thumb:hover {
  background: color-mix(in srgb, var(--muted) 55%, transparent);
  background-clip: padding-box;
}
::-webkit-scrollbar-corner { background: transparent; }
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
/* V12.0 — soft cards (e.g. "Worth a follow-up") read as secondary
   attention. Slightly more muted, less contrast against the page bg,
   so they don't compete with the primary "Recent activity" card.
   The names inside get a soft opacity dim too. */
.card.soft {
  background: color-mix(in srgb, var(--muted) 6%, var(--card-bg));
  border: 1px solid transparent;
  box-shadow: none;
}
.card.soft .call .body .who { color: color-mix(in srgb, var(--fg) 88%, var(--muted)); }
.card.soft .call .body .sum {
  color: color-mix(in srgb, var(--n-700) 80%, var(--muted));
}
@media (prefers-color-scheme: dark) {
  .card.soft { background: #0c1628; border-color: #1a2541; }
  .card.soft .call .body .who { color: #b8c5d8; }
  .card.soft .call .body .sum { color: #8ea3c1; }
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
/* V11.1 — section caption typography refined. Slightly smaller, tighter
   letter-spacing, less prominent — quiets the eye between content
   sections instead of competing with them. Vertical rhythm tightened
   from 28/12 to 24/10 for premium density. */
.section-caption { font-size: 11.5px; font-weight: 600;
                    color: var(--muted); text-transform: uppercase;
                    letter-spacing: 0.07em;
                    margin: 24px 4px 10px; }
.section-caption:first-child { margin-top: 6px; }

/* V9.4 — Today hero: bare typographic intro, no card chrome.
   V12.0 — headline 28px → 26px, sub 15px → 14.5px. The hero should
   set context, not dominate the page. Tighter letter-spacing on the
   headline + slightly more breathing in the sub line-height. */
.today-hero { display: flex; align-items: flex-end;
               justify-content: space-between; gap: 24px;
               margin: 0 0 8px; flex-wrap: wrap; }
.today-hero-text { min-width: 0; max-width: 720px; }
.today-headline { font-size: 26px; font-weight: 700;
                   letter-spacing: -0.022em; line-height: 1.2;
                   color: var(--fg); margin: 0; }
.today-sub { color: var(--muted); font-size: 14.5px;
              margin: 8px 0 0; max-width: 560px;
              line-height: 1.52; }
@media (max-width: 640px) {
  .today-hero { gap: 14px; }
  .today-headline { font-size: 22px; }
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
/* V11.1 — stat-card label refined. Smaller, tighter letter-spacing.
   The value (big number) is the focal point; the label should read
   as supporting metadata, not a headline. */
.stat .label { font-size: 12.5px; color: var(--muted);
                font-weight: 500; letter-spacing: -0.005em; }
.stat .value { font-size: 30px; font-weight: 700; letter-spacing: -0.022em;
               margin-top: 6px; line-height: 1.05; color: var(--fg);
               font-variant-numeric: tabular-nums; }
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
/* V9.2 / V12.0 — call card row. V12.0 tightens vertical padding
   18px → 15px for premium density. The card is the workhorse of
   the activity feed; tighter rows let more callers fit without
   feeling cramped. */
.call { display: grid;
         grid-template-columns: 44px 1fr auto;
         gap: var(--s-4); align-items: start;
         padding: 15px var(--s-5);
         border-bottom: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
         transition: background 140ms ease;
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
/* V10.5 — calmer waveform: 5 static bars. The progress bar carries
   the playback signal; bars get a steady accent-color fill on play
   but don't pulse. Reserves motion for things that matter. */
.rec-waveform {
  display: inline-flex; gap: 3px;
  align-items: center; height: 18px; flex: 1;
}
.rec-waveform span {
  display: inline-block;
  width: 3px; background: var(--n-300);
  border-radius: 1px;
  transition: background 200ms;
}
.rec-waveform span:nth-child(1) { height: 6px; }
.rec-waveform span:nth-child(2) { height: 12px; }
.rec-waveform span:nth-child(3) { height: 16px; }
.rec-waveform span:nth-child(4) { height: 10px; }
.rec-waveform span:nth-child(5) { height: 7px; }
.rec-player.playing .rec-waveform span { background: var(--accent); }
@media (prefers-color-scheme: dark) {
  .rec-waveform span { background: #2a3658; }
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

/* V10.2 — "Now" micro-badge for partners with activity in the last
   ~60s. V10.5 — dot is static (no breathing). The pulse was one of
   eight simultaneous animations on the page; reserving motion for
   the ONE canonical operator-pane Live indicator. */
.live-mini { display: inline-flex; align-items: center; gap: 5px;
              padding: 1px 7px; border-radius: 999px;
              background: var(--success-100); color: var(--success-500);
              font-size: 10.5px; font-weight: 600;
              text-transform: uppercase; letter-spacing: .04em;
              margin-left: 8px; vertical-align: 1px; }
.live-mini-dot { width: 5px; height: 5px; border-radius: 999px;
                  background: var(--success-500); }
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
/* V11.1 — refined brand mark. SVG glyph (speech bubble + three
   listening dots) replaces the pre-V11.1 plain accent dot. The
   wordmark is unchanged but spacing and weight are tightened. */
.demo-brand {
  display: inline-flex; align-items: center; gap: 9px;
  font-weight: 600; font-size: 15px; letter-spacing: -0.012em;
  color: var(--fg); text-decoration: none;
  padding: 4px 6px 4px 4px; margin-left: -6px;
  border-radius: 8px;
  transition: background 120ms;
}
.demo-brand:hover { background: var(--n-100); }
.demo-brand .brand-mark {
  width: 24px; height: 24px; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: 7px;
  transition: transform 180ms cubic-bezier(0.16, 1, 0.3, 1);
}
.demo-brand:hover .brand-mark { transform: rotate(-3deg) scale(1.04); }
.demo-brand .brand-mark svg { width: 16px; height: 16px; }
.demo-brand .brand-word {
  /* Subtle gradient text — premium-mark treatment without garish color */
  background: linear-gradient(180deg,
              var(--fg) 0%,
              color-mix(in srgb, var(--fg) 80%, var(--muted)) 100%);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent;
}
@media (prefers-color-scheme: dark) {
  .demo-brand .brand-mark { background: rgba(96, 165, 250, 0.12); }
  .demo-brand:hover { background: rgba(255,255,255,0.04); }
}
/* V11.1 → V12.0 — legacy `.dot` brand element CSS removed. The
   accent-dot mark was replaced by the V11.1 .brand-mark SVG glyph
   and the V11.1 .dot rule was kept as a display:none no-op. As of
   V12.0 the rule is dead code. */
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

/* V11.2 — iMessage-pattern conversation list (replaces the V10.1
   horizontal .chat-chips row). Vertical list of caller rows with
   avatar + name + phone + recent message preview + relative time.
   The pre-V11.2 .chat-chips / .chat-chip CSS is retained below as a
   no-op for any historical markup paths. */
.conv-list {
  flex: 1; overflow-y: auto;
  display: flex; flex-direction: column;
  background: var(--card-bg);
  padding: 4px 0;
}
.conv-list::-webkit-scrollbar { width: 6px; }
.conv-list::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--muted) 28%, transparent);
  border-radius: 3px;
}
.conv-row {
  appearance: none; background: transparent;
  border: none; cursor: pointer;
  display: flex; align-items: center; gap: 12px;
  padding: 11px 16px;
  text-align: left;
  border-bottom: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
  transition: background 120ms ease;
  font-family: inherit; color: var(--fg);
}
.conv-row:last-child { border-bottom: none; }
.conv-row:hover {
  background: color-mix(in srgb, var(--fg) 4%, var(--card-bg));
}
.conv-row.active {
  background: color-mix(in srgb, var(--accent) 8%, var(--card-bg));
}
.conv-row-avatar {
  width: 42px; height: 42px;
  border-radius: 50%;
  background: var(--n-200); color: var(--muted);
  display: inline-flex; align-items: center; justify-content: center;
  position: relative; overflow: hidden;
  flex-shrink: 0;
  font-size: 15px; font-weight: 600;
}
.conv-row-avatar-initial {
  position: absolute; inset: 0;
  display: inline-flex; align-items: center; justify-content: center;
  color: var(--muted);
}
.conv-row-avatar img {
  width: 100%; height: 100%; object-fit: cover;
  position: relative; z-index: 1;
}
@media (prefers-color-scheme: dark) {
  .conv-row-avatar { background: #243152; }
  .conv-row-avatar-initial { color: #94a3b8; }
}
.conv-row-body {
  flex: 1 1 auto; min-width: 0;
  display: flex; flex-direction: column; gap: 2px;
}
.conv-row-top {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 8px;
}
.conv-row-name {
  font-size: 14.5px; font-weight: 600;
  letter-spacing: -0.008em;
  color: var(--fg);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.conv-row-when {
  font-size: 11.5px; color: var(--muted);
  flex-shrink: 0; font-variant-numeric: tabular-nums;
}
.conv-row-phone {
  font-size: 11.5px; color: var(--muted);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.005em;
}
.conv-row-preview {
  font-size: 12.5px; color: var(--muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  margin-top: 1px;
  position: relative;
}
.conv-row-dot {
  display: inline-block;
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--accent);
  margin-left: 6px; vertical-align: middle;
}
.conv-list-empty {
  padding: 28px 16px; color: var(--muted); font-size: 12px;
  text-align: center;
}

/* V11.2 — phone-bar in two flavors. List mode shows brand + tagline
   ("Messages"). Thread mode shows back arrow + caller avatar + name +
   phone — like the top of an iMessage thread. JS toggles
   data-mode on .phone-shell to switch. */
.phone-shell[data-mode="list"] .bar-thread { display: none; }
.phone-shell[data-mode="thread"] .bar-list { display: none; }
.phone-shell[data-mode="list"] .phone-conv,
.phone-shell[data-mode="list"] .phone-suggestions,
.phone-shell[data-mode="list"] .phone-input {
  display: none;
}
.phone-shell[data-mode="thread"] .conv-list { display: none; }
.bar-thread {
  display: flex; align-items: center; gap: 10px;
}
.phone-back {
  background: transparent; border: none; cursor: pointer;
  width: 30px; height: 30px; border-radius: 8px;
  color: var(--accent);
  display: inline-flex; align-items: center; justify-content: center;
  margin-left: -6px;
  transition: background 120ms;
}
.phone-back:hover {
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}
.bar-thread-avatar {
  width: 34px; height: 34px;
  border-radius: 50%;
  background: var(--n-200); color: var(--muted);
  display: inline-flex; align-items: center; justify-content: center;
  position: relative; overflow: hidden;
  font-size: 13px; font-weight: 600; flex-shrink: 0;
}
.bar-thread-avatar-initial {
  position: absolute; inset: 0;
  display: inline-flex; align-items: center; justify-content: center;
  color: var(--muted);
}
.bar-thread-avatar img {
  width: 100%; height: 100%; object-fit: cover;
  position: relative; z-index: 1;
}
@media (prefers-color-scheme: dark) {
  .bar-thread-avatar { background: #243152; }
  .bar-thread-avatar-initial { color: #94a3b8; }
}
.bar-thread-info {
  display: flex; flex-direction: column; min-width: 0;
}
.bar-thread-name {
  font-size: 14px; font-weight: 600; color: var(--fg);
  letter-spacing: -0.008em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.bar-thread-phone {
  font-size: 11.5px; color: var(--muted);
  font-variant-numeric: tabular-nums; letter-spacing: 0.005em;
}

/* V10.1 → V11.2 → V12.0 — .chat-chips / .chat-chip CSS removed.
   Replaced by .conv-list / .conv-row in V11.2; the legacy block was
   retained as a no-op through V11.2 and is dead code as of V12.0. */

/* V12.0 — phone-conv vertical rhythm. Replaces the pre-V12.0 flat
   10px gap between all bubbles with iMessage-pattern clustering:
   adjacent same-sender bubbles sit 3px apart (one cluster), and
   sender-change introduces a 12px gap (cluster break). Achieved by
   removing `gap` and using margin-top on each bubble with an
   adjacent-sibling override for same-sender continuation. */
.phone-conv { flex: 1; padding: 18px 16px 14px;
               overflow-y: auto;
               display: flex; flex-direction: column; }

/* V12.0 — suggestion chips spacing refined. Pre-V12.0 used a 6px
   gap which felt cramped between pills. Bumped to 7px. Padding-top
   reduced to 6px (the conversation already has 14px bottom padding,
   stacking added 22px which felt loose). */
.phone-suggestions { display: flex; gap: 7px; padding: 6px 14px 0;
                      flex-wrap: wrap; flex-shrink: 0; }
.phone-suggestion { font-size: 12px; padding: 6px 11px;
                     border-radius: 999px;
                     background: var(--accent-soft); color: var(--accent);
                     border: none; cursor: pointer; font-weight: 500;
                     letter-spacing: -0.005em;
                     transition: background 140ms, transform 80ms; }
.phone-suggestion:hover {
  background: color-mix(in srgb, var(--accent) 14%, var(--accent-soft));
}
.phone-suggestion:active { transform: scale(0.97); }

/* V12.0 — phone input refined to iMessage proportions. Slimmer
   container padding (10px vs 12px), 36px send-button to match the
   input height, no longer a "form with input + button" but a tight
   chat composer. */
.phone-input { border-top: 1px solid var(--border);
                padding: 10px 12px 12px;
                display: flex; gap: 8px; align-items: center;
                background: var(--card-bg); flex-shrink: 0; }
.phone-input input { flex: 1; padding: 9px 14px;
                      border: 1px solid var(--border);
                      border-radius: 999px;
                      font-family: var(--font); font-size: 14px;
                      background: var(--bg); color: var(--fg);
                      outline: none;
                      transition: border-color 140ms, box-shadow 140ms; }
.phone-input input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent);
}
.phone-input button { border: none; background: var(--accent);
                       color: var(--accent-fg);
                       width: 36px; height: 36px;
                       border-radius: 999px;
                       cursor: pointer; flex-shrink: 0;
                       display: inline-flex; align-items: center; justify-content: center;
                       font-size: 18px; font-weight: 600;
                       transition: background 140ms, transform 80ms;
                       box-shadow: 0 1px 3px color-mix(in srgb, var(--accent) 25%, transparent); }
.phone-input button:hover {
  background: color-mix(in srgb, var(--fg) 8%, var(--accent));
}
.phone-input button:active { transform: scale(0.96); }
.phone-input button:disabled { background: var(--n-300);
                                cursor: not-allowed; }

/* Phone-shell-internal chat bubbles. Same vocabulary as .bubble but
   scoped under .phone-conv so the spacing reads chat-app-native.
   V12.0 — cluster spacing replaces flat-gap layout. */
.phone-conv .pmsg { max-width: 80%; padding: 9px 14px;
                     border-radius: 18px; font-size: 14px;
                     line-height: 1.45;
                     margin-top: 12px;
                     white-space: pre-wrap; word-wrap: break-word; }
.phone-conv .pmsg:first-child { margin-top: 0; }
/* V12.0 — cluster continuation: same-sender consecutive bubbles
   sit close together (3px gap) and share rounded-corner treatment
   so the tail-curve only appears on the last bubble in the cluster.
   iMessage pattern. */
.phone-conv .pmsg.user + .pmsg.user,
.phone-conv .pmsg.ai + .pmsg.ai { margin-top: 3px; }
.phone-conv .pmsg.user + .pmsg.user { border-bottom-right-radius: 18px; }
.phone-conv .pmsg.ai + .pmsg.ai { border-bottom-left-radius: 18px; }
/* Restore the tail curve on the *last* bubble in a cluster — the
   one with no same-sender sibling after it. */
.phone-conv .pmsg.user:not(:has(+ .pmsg.user)) { border-bottom-right-radius: 6px; }
.phone-conv .pmsg.ai:not(:has(+ .pmsg.ai)) { border-bottom-left-radius: 6px; }
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
  padding: 10px 12px 11px; border-radius: 14px;
  max-width: 92%; align-self: flex-start;
  font-size: 13px; line-height: 1.42;
  border-bottom-left-radius: 4px;
}
@media (prefers-color-scheme: dark) {
  .owner-sms { background: #1a2541; color: #e6edf7; }
}
/* V11.1 — sms-head: avatar · sender · timestamp on one flex row.
   Replaces the pre-V11.1 stacked "sender / body / timestamp" layout
   which felt vertically dense and lacked person-identity. */
.owner-sms .sms-head {
  display: flex; align-items: center; gap: 7px;
  margin-bottom: 5px;
}
.owner-sms .sms-av {
  width: 18px; height: 18px; border-radius: 50%;
  background: var(--n-200); color: var(--muted);
  display: inline-flex; align-items: center; justify-content: center;
  flex-shrink: 0; overflow: hidden; position: relative;
  font-size: 9.5px; font-weight: 600;
}
.owner-sms .sms-av-initial {
  position: absolute; inset: 0;
  display: inline-flex; align-items: center; justify-content: center;
  color: var(--muted);
}
.owner-sms .sms-av img {
  width: 100%; height: 100%; object-fit: cover;
  position: relative; z-index: 1;
}
@media (prefers-color-scheme: dark) {
  .owner-sms .sms-av { background: #243152; color: #94a3b8; }
  .owner-sms .sms-av-initial { color: #94a3b8; }
}
.owner-sms .sms-from {
  font-size: 11px; color: var(--muted);
  font-weight: 600;
  flex: 0 0 auto;
}
.owner-sms .sms-ts {
  font-size: 10.5px; color: var(--muted);
  margin-left: auto;
  letter-spacing: 0.01em;
}
.owner-sms .sms-body {
  font-size: 13px; line-height: 1.42;
}
.owner-sms.urgent { background: var(--danger-100);
                     color: var(--danger-500); }
.owner-sms.urgent .sms-from { color: var(--danger-500); }
.owner-sms.urgent .sms-ts { color: rgba(220, 38, 38, 0.65); }
.owner-sms.urgent .sms-av {
  background: rgba(220, 38, 38, 0.12);
}
@media (prefers-color-scheme: dark) {
  .owner-sms.urgent { background: #2e0d0d; color: #fb7185; }
  .owner-sms.urgent .sms-from { color: #fb7185; }
  .owner-sms.urgent .sms-ts { color: rgba(251,113,133,0.65); }
  .owner-sms.urgent .sms-av { background: rgba(251,113,133,0.18); }
}
/* V10.5 / V11.1 — refined Read indicator: single muted check glyph,
   no "Read" text. Once the visual is established by the seeded
   bubbles the prospect parses subsequent appearances instantly. */
.owner-sms .sms-read {
  font-size: 0; color: var(--muted);
  margin-top: 4px;
  display: inline-flex; align-items: center;
  opacity: 0; transition: opacity 280ms ease;
}
.owner-sms .sms-read.shown { opacity: 0.7; }
.owner-sms .sms-read svg { width: 11px; height: 11px;
                            stroke: currentColor; fill: none;
                            stroke-width: 2.2; stroke-linecap: round;
                            stroke-linejoin: round; }
.owner-sms.urgent .sms-read svg { color: rgba(220, 38, 38, 0.6); }
@media (prefers-color-scheme: dark) {
  .owner-sms.urgent .sms-read svg { color: rgba(251,113,133,0.55); }
}
/* New-arrival animation when an SMS slides in. */
.owner-sms.just-arrived {
  animation: sms-arrive 360ms cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes sms-arrive {
  from { opacity: 0; transform: translateY(-6px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* V10.4 / V10.5 — "Updated Xs ago" indicator. Pre-V10.5 it was
   always visible and updated every second — that's another always-on
   ticker. Now hidden by default and only revealed when the pane
   label is hovered, so the prospect can ANSWER the question "is this
   live?" without it constantly demanding attention. */
.refresh-indicator {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 1px 8px; border-radius: 999px;
  background: var(--n-100); color: var(--muted);
  font-size: 10.5px; font-weight: 500;
  text-transform: none; letter-spacing: 0;
  font-variant-numeric: tabular-nums;
  margin-left: 8px;
  opacity: 0; transition: opacity 160ms ease;
}
.demo-pane-operator .pane-label:hover .refresh-indicator,
.demo-pane-operator .pane-label:focus-within .refresh-indicator {
  opacity: 1;
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

/* V10.5 — demo drawer. Replaces V10.4's floating control bar +
   inline tenant switcher. Single ⋯ button in the top bar opens an
   aside panel from the right with industry switcher, pause-refresh,
   and reset. Keeps demo-mechanic UI out of the operational view. */
.demo-drawer-toggle {
  margin-left: 10px;
  width: 32px; height: 32px;
  border-radius: 999px; border: 1px solid var(--border);
  background: var(--card-bg); color: var(--muted);
  display: inline-flex; align-items: center; justify-content: center;
  cursor: pointer; padding: 0;
  transition: background 120ms, color 120ms;
}
.demo-drawer-toggle:hover { background: var(--n-100); color: var(--fg); }
.demo-drawer {
  position: fixed; top: 0; right: 0; bottom: 0; z-index: 60;
  width: 320px; max-width: calc(100vw - 32px);
  background: var(--card-bg); border-left: 1px solid var(--border);
  box-shadow: -16px 0 32px rgba(15,23,42,0.10);
  transform: translateX(100%);
  transition: transform 220ms cubic-bezier(0.16, 1, 0.3, 1);
  display: flex; flex-direction: column;
}
.demo-drawer.open { transform: translateX(0); }
/* V11.1 — drawer modernization. Single-icon title row, refined
   spacing, divider between groups, quieter footer. iMessage-pattern
   visual restraint applied to settings UI. */
.demo-drawer-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px 14px;
  border-bottom: 1px solid var(--border);
}
.demo-drawer-title-row {
  display: inline-flex; align-items: center; gap: 9px;
}
.demo-drawer-mark {
  width: 22px; height: 22px; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: 6px;
}
.demo-drawer-mark svg { width: 13px; height: 13px; }
.demo-drawer-title { font-size: 13.5px; font-weight: 600;
                      color: var(--fg); letter-spacing: -0.008em; }
@media (prefers-color-scheme: dark) {
  .demo-drawer-mark { background: rgba(96, 165, 250, 0.12); }
}
.demo-drawer-close {
  background: transparent; border: none; cursor: pointer;
  width: 28px; height: 28px; border-radius: 7px;
  color: var(--muted);
  display: inline-flex; align-items: center; justify-content: center;
  transition: background 120ms, color 120ms;
}
.demo-drawer-close:hover { background: var(--n-100); color: var(--fg); }
.demo-drawer-body {
  padding: 20px 20px 18px;
  display: flex; flex-direction: column; gap: 18px;
  flex: 1 1 auto; overflow-y: auto;
}
.demo-drawer .dd-row { display: flex; flex-direction: column; gap: 8px; }
.demo-drawer .dd-label {
  font-size: 11px; font-weight: 600;
  color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.06em;
  margin: 0;
}
.demo-drawer .dd-hint {
  font-size: 12px; color: var(--muted);
  margin: 0 0 4px; line-height: 1.45;
}
.demo-drawer .dd-divider {
  height: 1px; background: var(--border);
  margin: 2px -2px;
}
/* V11.1 → V11.2 — drawer dropdown polish round 2.
   Premium-SaaS dropdown treatment: solid contrast in both modes,
   distinct hover + focus rings, a properly-sized chevron, and a
   `color-scheme: light dark` hint that lets the OS-native option
   panel honor the user's color preference instead of always
   rendering as white. The native option panel itself is browser-
   controlled — we still give it explicit bg/color so engines that
   honor option CSS (Firefox, modern Edge) match the wrapper. */
.demo-drawer .dd-row .tenant-switcher {
  margin: 0;
  padding: 0;
  background: var(--card-bg);
  color: var(--fg);
  border: 1.5px solid var(--border);
  width: 100%; border-radius: 10px;
  transition: border-color 140ms ease, box-shadow 140ms ease, background 140ms ease;
  position: relative;
}
.demo-drawer .dd-row .tenant-switcher:hover {
  border-color: color-mix(in srgb, var(--accent) 50%, var(--border));
  background: color-mix(in srgb, var(--accent) 4%, var(--card-bg));
}
.demo-drawer .dd-row .tenant-switcher:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 22%, transparent);
}
.demo-drawer .dd-row .tenant-switcher::after {
  content: "";
  position: absolute;
  right: 14px; top: 50%;
  width: 7px; height: 7px;
  border-right: 1.8px solid var(--fg);
  border-bottom: 1.8px solid var(--fg);
  transform: translateY(-65%) rotate(45deg);
  pointer-events: none;
  opacity: 0.55;
  transition: opacity 140ms;
}
.demo-drawer .dd-row .tenant-switcher:hover::after,
.demo-drawer .dd-row .tenant-switcher:focus-within::after {
  opacity: 0.9;
}
.demo-drawer .dd-row .tenant-switcher select {
  width: 100%;
  padding: 11px 36px 11px 13px;
  font-size: 14px; font-weight: 500;
  letter-spacing: -0.008em;
  color: var(--fg);
  background: transparent;
  border: none;
  cursor: pointer;
  outline: none;
  /* Helps the OS-native option panel adopt the right color theme
     (Chromium/Edge respect this for the dropdown background). */
  color-scheme: light dark;
}
.demo-drawer .dd-row .tenant-switcher select option {
  background-color: var(--card-bg);
  color: var(--fg);
  font-weight: 500;
  padding: 6px 8px;
}
@media (prefers-color-scheme: dark) {
  .demo-drawer .dd-row .tenant-switcher select option {
    background-color: #111a2e;
    color: #e6edf7;
  }
}
.demo-drawer .dd-actions { flex-direction: row; gap: 8px; }
.demo-drawer .dd-btn {
  flex: 1; padding: 10px 12px; border-radius: 9px;
  border: 1px solid var(--border); background: var(--card-bg);
  color: var(--fg); font-weight: 500; font-size: 13px;
  letter-spacing: -0.005em;
  cursor: pointer;
  transition: background 120ms, border-color 120ms, transform 80ms;
}
.demo-drawer .dd-btn:hover {
  background: var(--n-100);
  border-color: color-mix(in srgb, var(--fg) 18%, var(--border));
}
.demo-drawer .dd-btn:active { transform: scale(0.985); }
.demo-drawer .dd-btn.paused {
  background: var(--accent-soft); color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 40%, var(--accent-soft));
}
.demo-drawer .dd-btn-danger { color: var(--danger-500); }
.demo-drawer .dd-btn-danger:hover {
  background: var(--danger-100);
  border-color: color-mix(in srgb, var(--danger-500) 35%, var(--border));
}
.demo-drawer .dd-foot {
  margin-top: auto; padding-top: 14px;
  border-top: 1px solid var(--border);
  font-size: 11.5px; color: var(--muted);
  text-align: center;
  letter-spacing: 0.005em;
}
.demo-drawer .dd-foot-line {
  display: inline-flex; align-items: center; gap: 6px;
}
.demo-drawer .dd-foot kbd {
  display: inline-block; padding: 1px 6px;
  font-family: var(--font-mono); font-size: 10.5px;
  background: var(--n-100); border-radius: 4px;
  color: var(--fg); font-weight: 600;
  line-height: 1.4;
}

/* V10.5 — V10.4 incoming-call banner + live call timer removed.
   They were demo theater; restraint phase keeps the call experience
   quiet and lets the chat populate naturally on caller-select. */

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

/* V10.5 — quiet onboarding hint. Pre-V10.5 this was a bobbing
   arrow tooltip with a pulse animation; that was demo theater.
   Now it's a small inline caption in the customer pane label that
   fades away on first chip click. */
.onboard-hint {
  display: inline-flex; align-items: center;
  margin-left: 10px;
  padding: 1px 8px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent);
  font-size: 10.5px; font-weight: 500;
  text-transform: none; letter-spacing: 0;
  transition: opacity 220ms ease;
}
.onboard-hint.dismissed { opacity: 0; pointer-events: none; }

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
/* V10.5 — V10.3 .window-bar fake browser-window dots removed.
   They were skeuomorphic chrome that added no signal. The portal
   shell now opens straight into the body content. */
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


def _tenant_switcher_options() -> str:
    """V11.0 — render the tenant switcher's <option> tags dynamically
    from `src.industries.list_all()`. Each option carries data
    attributes the switcher JS reads when industry changes:
      - data-brand        : demo brand name
      - data-owner        : owner first name (for the owner phone label)
      - data-suggestions  : JSON array of full suggestion sentences
      - data-labels       : JSON array of short chip labels (parallel)
      - data-emergency-ind: vertical-appropriate urgency label
      - data-business-noun: singular business noun (showing, service call)
      - data-portal-stats : JSON object of portal stat labels per vertical
    """
    import json as _json
    from src import industries

    parts: list[str] = []
    for ind in industries.list_all():
        slug = ind["slug"]
        # Default-select septic so the pre-V11.0 demo state is
        # preserved on first load.
        # V11.0 — default opens on HVAC per the V11.0 plan (the
        # emergency moment demos strongest). Real estate is one
        # click away.
        is_default = (slug == "hvac")
        selected = " selected" if is_default else ""
        suggestions_json = _json.dumps(ind.get("suggestions") or [])
        labels_json = _json.dumps(ind.get("suggestion_labels") or [])
        portal_stats = ind.get("portal_copy") or {}
        portal_json = _json.dumps({
            "today_headline":   portal_stats.get("today_headline", "Today's calls"),
            "stat_calls":       portal_stats.get("stat_calls", "Service calls"),
            "stat_emergencies": portal_stats.get("stat_emergencies", "Emergencies"),
            "partner_term":     portal_stats.get("partner_term", "customer"),
        })
        seeded_sms_json = _json.dumps(ind.get("seeded_owner_sms") or [])
        notif_label = ind.get("notification_label", "Owner")
        parts.append(
            f'<option value="{html.escape(slug)}"{selected}'
            f' data-brand="{html.escape(ind["name"])}"'
            f' data-owner="{html.escape(ind["owner_label"])}"'
            f' data-notif-label="{html.escape(notif_label)}"'
            f' data-suggestions="{html.escape(suggestions_json)}"'
            f' data-labels="{html.escape(labels_json)}"'
            f' data-emergency-ind="{html.escape(ind.get("emergency_indicator", "Emergency"))}"'
            f' data-business-noun="{html.escape(ind.get("business_noun", "call"))}"'
            f' data-portal-stats="{html.escape(portal_json)}"'
            f' data-seeded-sms="{html.escape(seeded_sms_json)}"'
            f'>{html.escape(ind["name"])}</option>'
        )
    return "\n          ".join(parts)


def demo_page(*, title: str, body: str,
              phone_number: str = "+1 (844) 940-3274",
              tel_href: str = "tel:+18449403274") -> str:
    """V9.5 / V11.0 — public-facing combined demo shell.

    Differs from `page()`: no sidebar nav, no per-tenant brand, just a
    minimal top bar with the product mark and the live demo phone
    number. The body is expected to be a split-screen of two panes.
    Uses the same `_CSS` design tokens so the demo and the real portal
    share one visual system.

    V11.0 — the tenant switcher renders 12 industries from the registry,
    not 3 hardcoded options. Switching industry rebuilds the chat
    suggestion chips and triggers a /demo/callers?industry=X refetch.
    """
    phone_label = html.escape(phone_number)
    tel = html.escape(tel_href)
    switcher_options = _tenant_switcher_options()
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="AI Receptionist live demo — see the customer side and the operator side, in real time.">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head><body data-accent="brand" class="demo-page">
<header class="demo-top">
  <a href="/" class="demo-brand" aria-label="AI Receptionist — home">
    <span class="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none">
        <!-- V11.1 — speech-bubble with three dots: the receptionist
             is on the line, listening. Replaces the pre-V11.1 plain
             accent dot which read as a placeholder. -->
        <path d="M4 9.5a5 5 0 0 1 5-5h6a5 5 0 0 1 5 5v3a5 5 0 0 1-5 5h-4.5l-3.5 2.5v-2.6a5 5 0 0 1-3-4.4z"
              stroke="currentColor" stroke-width="1.6"
              stroke-linejoin="round" fill="none"/>
        <circle cx="9" cy="11" r="1.05" fill="currentColor"/>
        <circle cx="12" cy="11" r="1.05" fill="currentColor"/>
        <circle cx="15" cy="11" r="1.05" fill="currentColor"/>
      </svg>
    </span>
    <span class="brand-word">AI Receptionist</span>
  </a>
  <a href="{tel}" class="demo-phone-link" style="margin-left:auto;">
    <svg width="14" height="14" viewBox="0 0 24 24"
         stroke="currentColor" fill="none" stroke-width="1.75"
         stroke-linecap="round" stroke-linejoin="round">
      <path d="M6.6 10.8a13 13 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11 11 0 0 0 3.5.55 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1 11 11 0 0 0 .55 3.5 1 1 0 0 1-.25 1Z"/>
    </svg>
    <span>{phone_label}</span>
  </a>
  <!-- V10.5 — demo drawer toggle. Hides the tenant switcher, reset,
       pause-refresh, and shortcut hint behind a single discreet
       ⋯ button so the top bar reads as "brand · phone number" only. -->
  <button class="demo-drawer-toggle" id="demo-drawer-toggle"
          aria-label="Demo settings" title="Demo settings">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
      <circle cx="6" cy="12" r="1.6"/>
      <circle cx="12" cy="12" r="1.6"/>
      <circle cx="18" cy="12" r="1.6"/>
    </svg>
  </button>
</header>
<aside class="demo-drawer" id="demo-drawer" aria-hidden="true">
  <div class="demo-drawer-head">
    <div class="demo-drawer-title-row">
      <span class="demo-drawer-mark" aria-hidden="true">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor"
             stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="8" cy="8" r="6"/>
          <path d="M8 5v3l2 1.5"/>
        </svg>
      </span>
      <span class="demo-drawer-title">Demo controls</span>
    </div>
    <button class="demo-drawer-close" id="demo-drawer-close" aria-label="Close">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="2"
           stroke-linecap="round">
        <path d="M5 5l14 14M19 5L5 19"/>
      </svg>
    </button>
  </div>
  <div class="demo-drawer-body">
    <div class="dd-row">
      <label class="dd-label">Industry</label>
      <p class="dd-hint">Pick the vertical you want the demo tuned to.</p>
      <div class="tenant-switcher" id="tenant-switcher" title="Switch demo industry">
        <select aria-label="Demo industry">
          {switcher_options}
        </select>
      </div>
    </div>
    <div class="dd-divider" aria-hidden="true"></div>
    <div class="dd-row">
      <label class="dd-label">Session</label>
      <div class="dd-actions">
        <button class="dd-btn" id="dd-pause" type="button">Pause refresh</button>
        <button class="dd-btn dd-btn-danger" id="dd-reset" type="button">Reset demo</button>
      </div>
    </div>
    <div class="dd-foot">
      <span class="dd-foot-line">
        Press <kbd>?</kbd> for keyboard shortcuts
      </span>
    </div>
  </div>
</aside>
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
/* V10.5 — demo drawer wiring. Toggle button opens/closes the
   right-side aside. Click outside or Esc to close. Holds the
   tenant switcher + reset + pause-refresh out of the main view. */
(function(){{
  const $toggle = document.getElementById("demo-drawer-toggle");
  const $drawer = document.getElementById("demo-drawer");
  const $close  = document.getElementById("demo-drawer-close");
  if (!$toggle || !$drawer) return;
  function open(){{
    $drawer.classList.add("open");
    $drawer.setAttribute("aria-hidden", "false");
  }}
  function close(){{
    $drawer.classList.remove("open");
    $drawer.setAttribute("aria-hidden", "true");
  }}
  $toggle.addEventListener("click", function(e){{
    e.stopPropagation();
    $drawer.classList.contains("open") ? close() : open();
  }});
  $close.addEventListener("click", close);
  document.addEventListener("click", function(e){{
    if (!$drawer.classList.contains("open")) return;
    if ($drawer.contains(e.target) || $toggle.contains(e.target)) return;
    close();
  }});
  document.addEventListener("keydown", function(e){{
    if (e.key === "Escape") close();
  }});
}})();
/* V10.3 / V10.4 / V10.5 / V11.0 — tenant switcher: real industry
   context. Lives inside the demo drawer. Renders 12 industries from
   the registry. Switching industry rebuilds the chat suggestion
   chips dynamically (4-6 per industry, parsed from data-suggestions
   JSON), updates the brand label, owner-phone label, portal stat
   labels, and re-fetches the chat caller list for that industry. */
(function(){{
  const $sw = document.getElementById("tenant-switcher");
  if (!$sw) return;
  const $sel = $sw.querySelector("select");

  function safeJSON(str, fallback){{
    if (!str) return fallback;
    try {{ return JSON.parse(str); }} catch(_) {{ return fallback; }}
  }}

  function rebuildSuggestions(suggestions, labels){{
    const $box = document.getElementById("suggestions");
    if (!$box) return;
    /* Keep the trailing "Wrong number" generic chip — it's industry-
       agnostic and reads as a calm-filter demo in every vertical. */
    const wrongMsg = "Sorry, wrong number.";
    const wrongLabel = "Wrong number";
    /* Cap visible chips at 4 so the row doesn't wrap on smaller phones.
       Suggestions list orders matter: the most demonstrative scenarios
       come first per industry. */
    const visible = Math.min(suggestions.length, 3);
    const out = [];
    for (let i = 0; i < visible; i++){{
      const label = labels[i] || suggestions[i].slice(0, 20);
      const msg = suggestions[i];
      out.push('<button class="phone-suggestion" data-msg="'
        + msg.replace(/"/g, "&quot;") + '">'
        + label.replace(/</g, "&lt;") + '</button>');
    }}
    out.push('<button class="phone-suggestion" data-msg="'
      + wrongMsg + '">' + wrongLabel + '</button>');
    $box.innerHTML = out.join("");
  }}

  function applyPortalStats(portalStats){{
    if (!portalStats) return;
    /* The portal pane's "Today's calls" headline becomes "Today's
       leads" for real estate, "Today's intakes" for legal, etc. */
    const $headline = document.querySelector(".pane-portal .pane-label");
    if ($headline && portalStats.today_headline){{
      const labelSpan = $headline.querySelector("span:first-child");
      if (labelSpan) labelSpan.textContent = portalStats.today_headline;
    }}
  }}

  function rebuildOwnerSeed(seededSms){{
    const $conv = document.getElementById("owner-conv");
    if (!$conv) return;
    /* Wipe the seeded entries but keep any dynamically-pushed ones
       (added via pushOwnerSMS during the demo session). Dynamically-
       pushed bubbles carry the .just-arrived class on entry; we keep
       those. */
    const dynamicBubbles = Array.from(
      $conv.querySelectorAll(".owner-sms"))
      .filter(el => el.dataset.dynamic === "1");
    /* V11.1 — bubble renders with customer avatar (small Pravatar +
       initial fallback), sms-head row (avatar · sender · timestamp),
       sms-body (alert text), and a muted check-only read receipt.
       iMessage-pattern grouping. */
    function _esc(s){{ return (s || "").replace(/</g, "&lt;"); }}
    function _seed(phone){{
      const d = (phone || "").replace(/\\D/g, "").replace(/^1/, "");
      return d || "x";
    }}
    const seedHTML = (seededSms || []).map(function(s){{
      const urgentClass = s.urgent ? " urgent" : "";
      const body = _esc(s.body);
      const ts = _esc(s.ts_label);
      const name = (s.customer_name || "").trim();
      const initial = (name.charAt(0) || "").toUpperCase();
      const seed = _seed(s.customer_phone);
      const avatar = seed !== "x"
        ? ('<span class="sms-av">'
            + '<span class="sms-av-initial">' + _esc(initial) + '</span>'
            + '<img src="https://i.pravatar.cc/150?u=' + seed + '" alt="" loading="lazy" '
            + 'onerror="if(this.dataset.tried!==\\'fallback\\'){{'
            + 'this.dataset.tried=\\'fallback\\';this.src=\\'https://api.dicebear.com/9.x/notionists/svg?seed=' + seed + '\\';'
            + '}}else{{this.style.display=\\'none\\';}}">'
            + '</span>')
        : '';
      return '<div class="owner-sms' + urgentClass + '">'
        + '<div class="sms-head">' + avatar
        + '<span class="sms-from">AI Receptionist</span>'
        + '<span class="sms-ts">' + ts + '</span></div>'
        + '<div class="sms-body">' + body + '</div>'
        + '<div class="sms-read shown" aria-label="Read">'
        + '<svg viewBox="0 0 12 12"><path d="M2 6l2.5 2.5L10 3"/></svg>'
        + '</div>'
        + '</div>';
    }}).join("");
    $conv.innerHTML = seedHTML;
    /* Re-append any dynamically-arrived bubbles after the new seed. */
    dynamicBubbles.forEach(function(el){{ $conv.appendChild(el); }});
  }}

  function applyIndustry(opt, opts){{
    opts = opts || {{}};
    const brand = opt.getAttribute("data-brand");
    const owner = opt.getAttribute("data-owner");
    const suggestions = safeJSON(
      opt.getAttribute("data-suggestions"), []);
    const labels = safeJSON(opt.getAttribute("data-labels"), []);
    const portalStats = safeJSON(
      opt.getAttribute("data-portal-stats"), {{}});
    const seededSms = safeJSON(
      opt.getAttribute("data-seeded-sms"), []);

    /* Brand on customer phone */
    const custBiz = document.querySelector(
      ".demo-pane-customer .phone-shell:not(.owner-shell) .phone-bar .biz");
    if (custBiz && brand) custBiz.textContent = brand;

    /* V11.1 — Owner-phone label uses the operational notification
       label ("Owner notifications" / "Manager notifications") instead
       of the pre-V11.1 personal "{{name}}'s phone" pattern. Falls back
       to owner+"'s phone" if data-notif-label isn't on the option. */
    const notifLabel = opt.getAttribute("data-notif-label");
    const ownerBiz = document.querySelector(
      ".owner-shell .phone-bar .biz");
    if (ownerBiz){{
      const labelText = notifLabel
        ? notifLabel + " notifications"
        : (owner ? owner + "'s phone" : "");
      if (labelText) ownerBiz.firstChild.textContent = labelText;
    }}

    /* Rebuild suggestion chips */
    rebuildSuggestions(suggestions, labels);

    /* Rebuild owner-phone seeded SMS bubbles. On initial load this is
       a no-op (the server-rendered seed already matches), but on
       industry switch this swaps to the new vertical's seed. */
    if (!opts.initial) rebuildOwnerSeed(seededSms);

    /* Portal stat labels */
    applyPortalStats(portalStats);

    /* Globals consumed by /chat and /demo/callers fetches */
    if (typeof window !== "undefined"){{
      window.currentIndustry = opt.value;
      window.currentOwnerName = owner || window.currentOwnerName;
    }}

    /* V11.0 — re-fetch the chat caller list for this industry.
       Skip on initial load (the loader runs separately at boot)
       so we don't double-fetch. */
    if (!opts.initial
        && typeof window !== "undefined"
        && typeof window.reloadDemoCallers === "function"){{
      window.reloadDemoCallers(opt.value);
    }}
  }}

  $sel.addEventListener("change", function(){{
    applyIndustry($sel.options[$sel.selectedIndex]);
  }});
  applyIndustry($sel.options[$sel.selectedIndex], {{initial: true}});
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
