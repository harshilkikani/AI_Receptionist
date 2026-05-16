"""Client-facing portal — /client/{client_id} routes.

One signed URL per tenant, bookmarkable. Portal shows the CLIENT's
activity — never cost/margin/revenue internals. Invoice view IS the
client's bill (their prices), which is a distinct surface.

Auth:
  - HMAC-SHA256 over "{client_id}|{issued_ts}" keyed by
    CLIENT_PORTAL_SECRET.
  - Tokens never expire; rotate by changing the secret.
  - No database of tokens. Verify by recomputing HMAC.
  - Secret unset → ALL tokens rejected (safe default, portal disabled).

V9.0 — nav restructured for non-technical operators: Today / Recent
calls / Follow-ups / Settings. Invoice still accessible at its URL but
no longer surfaced in primary nav. All outcome strings rendered via
status_pill so engineer-y vocabulary ("spam_phrase", "duration_capped")
never reaches the customer.

CLI:
    python -m src.client_portal issue <client_id>
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from src import tenant, usage
from src.design import (
    call_card, card, data_table, page, partner_photo_url, pill,
    section_caption, stat_card, stats, status_pill, icon,
)

router = APIRouter(prefix="/client", tags=["client_portal"])


# ── Token ──────────────────────────────────────────────────────────────

def _secret() -> str:
    return os.environ.get("CLIENT_PORTAL_SECRET", "") or ""


def _sign(client_id: str, issued_ts: int) -> str:
    secret = _secret()
    payload = f"{client_id}|{issued_ts}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"{issued_ts}.{sig}"


def issue_token(client_id: str, issued_ts: Optional[int] = None) -> str:
    """Mint a signed token for the client. Raises ValueError if
    CLIENT_PORTAL_SECRET is unset — no silent disable."""
    if not _secret():
        raise ValueError("CLIENT_PORTAL_SECRET is not set; refusing to issue a token.")
    return _sign(client_id, issued_ts if issued_ts is not None else int(time.time()))


def verify_token(client_id: str, token: str) -> bool:
    if not token or not _secret():
        return False
    try:
        ts_str, sig = token.split(".", 1)
        issued_ts = int(ts_str)
    except (ValueError, AttributeError):
        return False
    expected = _sign(client_id, issued_ts).split(".", 1)[1]
    return hmac.compare_digest(sig.encode("ascii"), expected.encode("ascii"))


def _require(client_id: str, t: str) -> dict:
    """Validate token + load client. 403 on ANY mismatch so a probe
    can't distinguish unknown-client from bad-token."""
    client = tenant.load_client_by_id(client_id)
    if client is None or (client.get("id") or "").startswith("_"):
        raise HTTPException(status_code=403, detail="invalid token")
    if not verify_token(client_id, t):
        raise HTTPException(status_code=403, detail="invalid token")
    return client


def portal_url(client_id: str, base_url: Optional[str] = None) -> str:
    token = issue_token(client_id)
    base = base_url or os.environ.get("PUBLIC_BASE_URL", "http://localhost:8765")
    return f"{base.rstrip('/')}/client/{client_id}?t={token}"


# ── Nav helpers ────────────────────────────────────────────────────────

def _nav(client_id: str, t: str) -> list:
    """V9.1 — consolidated to 3 primary tabs (Today / Conversations /
    Settings). Follow-ups is a section inside Today. Recent calls
    folded into Conversations (one unified communications view).
    Invoice still reachable by URL; not in the nav."""
    tq = f"?t={html.escape(t)}"
    return [
        ("Today",          f"/client/{client_id}{tq}"),
        ("Conversations",  f"/client/{client_id}/conversations{tq}"),
        ("Settings",       f"/client/{client_id}/settings{tq}"),
    ]


# ── Routes ─────────────────────────────────────────────────────────────

def _today_body(client_id: str, t: str = "", *,
                 include_invoice_link: bool = True,
                 partners_limit: int = 8,
                 industry: Optional[str] = None) -> str:
    """V9.5 / V11.0 — extracted from summary() so both the real portal
    route and the public combined-demo page at / can render the same
    content with the same components and the same data. No page
    chrome, no auth — pure body fragment.

    `include_invoice_link` controls the inline invoice button in the
    hero (demo doesn't need it). `partners_limit` caps the activity feed.

    V11.0 — when `industry` is set, the activity feed and follow-up
    surface filter to that vertical's seeded phone-range (see
    `demo_seed._INDUSTRY_PHONE_PREFIXES`). The section captions and
    stat-card labels also swap to vertical-native terminology
    (e.g. 'Recent inquiries' for real estate, 'Today's intakes' for
    legal). The monthly aggregate counts are not filtered — that
    requires a deeper usage.monthly_summary refactor; the activity
    feed is what changes per switch.
    """
    from src import industries as _industries
    industry_meta = _industries.get(industry) if industry else None
    phone_prefix = ""
    if industry:
        from src import demo_seed as _demo_seed
        phone_prefix = _demo_seed.industry_phone_prefix(industry)

    client = tenant.load_client_by_id(client_id)
    if not client:
        return '<div class="empty">Tenant not configured.</div>'
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    s = usage.monthly_summary(client_id)
    bookings = _count_bookings(client_id, month)
    tq = f"?t={html.escape(t)}" if t else ""

    # ── Recent activity (last 24h of calls + SMS, interleaved) ────────
    now_ts = int(time.time())
    today_ts = now_ts - 24 * 60 * 60
    # V11.0 — pull a wider window when filtering so the post-filter
    # list still has enough partners to feel populated.
    raw_limit = partners_limit * 8 if phone_prefix else partners_limit
    partners_today = usage.list_conversation_partners(
        client_id, limit=raw_limit, since_ts=today_ts)
    if phone_prefix:
        partners_today = [
            p for p in partners_today
            if (p.get("phone") or "").startswith(phone_prefix)
        ][:partners_limit]
    today_calls = sum(p["calls"] for p in partners_today)
    today_msgs = sum(p["messages"] for p in partners_today)
    today_emerg = _today_emergency_count(client_id, today_ts)

    activity_html = ""
    if partners_today:
        cards = []
        for p in partners_today:
            when = _human_when(p["last_ts"], now_ts)
            preview = _render_partner_preview(client_id, p["phone"], t,
                                                p.get("last_call_sid", ""))
            is_live = (now_ts - int(p["last_ts"] or 0)) <= 60
            cards.append(call_card(
                caller=_partner_label(p["phone"]),
                from_number=p["phone"],
                when=when,
                photo_url=partner_photo_url(p["phone"]),
                summary=(p.get("last_summary") or
                          ("Text message exchange"
                           if p["last_channel"] == "sms"
                           else "Voice call")),
                status="emergency" if today_emerg and p["last_channel"] == "voice"
                                   and (p.get("last_summary") or "")
                                       .lower().find("emergency") >= 0
                                   else "answered",
                preview_html=preview,
                live=is_live,
            ))
        recent_label = (
            (industry_meta or {}).get("portal_copy", {})
            .get("recent_label", "Recent activity"))
        activity_html = (
            section_caption(recent_label)
            + card("".join(cards), flush=True)
        )
    else:
        recent_label = (
            (industry_meta or {}).get("portal_copy", {})
            .get("recent_label", "Recent activity"))
        activity_html = (
            section_caption(recent_label)
            + card(
                f'<div class="empty empty-warm">'
                f'<div class="empty-icon">{icon("phone", size=20)}</div>'
                f'<div class="empty-title">All quiet right now</div>'
                f'<div class="empty-sub">Your line is on. Calls and texts '
                f'will show up here as soon as they come in.</div>'
                f'</div>',
                flush=True,
            )
        )

    # ── Follow-ups section (soft variant — context, not data) ────────
    # V11.0 — when filtering by industry, pull a wider window then
    # narrow to the industry's phone range.
    followups_raw_limit = 40 if phone_prefix else 5
    followups = _followup_candidates(client_id, limit=followups_raw_limit)
    if phone_prefix:
        followups = [
            r for r in followups
            if (r.get("from_number") or "").startswith(phone_prefix)
        ][:5]
    followups_html = ""
    if followups:
        items = []
        for r in followups:
            when = _human_when(r["start_ts"], now_ts)
            raw_phone = r.get("from_number") or ""
            items.append(call_card(
                caller=_partner_label(raw_phone),
                from_number=raw_phone,
                when=when,
                photo_url=partner_photo_url(raw_phone),
                summary=(r.get("summary")
                          if "summary" in (r.keys() if hasattr(r, "keys") else [])
                          else None) or "Short call — caller may want a follow-up",
                status="callback",
                href=(
                    f"/client/{html.escape(client_id)}/conversations/"
                    f"{html.escape(_phone_slug(raw_phone))}{tq}"
                    if (raw_phone and t) else ""
                ),
            ))
        followup_label = (
            (industry_meta or {}).get("portal_copy", {})
            .get("followup_label", "Worth a follow-up"))
        followups_html = (
            section_caption(followup_label)
            + card("".join(items), variant="soft", flush=True)
        )

    # ── Bare stat strip with 30-day sparklines (V10.3) ────────────
    try:
        daily_calls = usage.daily_call_counts(client_id, days=30)
    except Exception:
        daily_calls = []
    # Emergencies + bookings don't have their own daily helpers yet —
    # approximate as derived fractions of daily_calls so the sparklines
    # tell a coherent trend without a new backend surface.
    emerg_frac = (s["emergencies"] / max(1, s["calls_handled"])) if s["calls_handled"] else 0
    book_frac  = (bookings / max(1, s["calls_handled"])) if s["calls_handled"] else 0
    daily_emerg = [int(round(v * emerg_frac)) for v in daily_calls]
    daily_book  = [int(round(v * book_frac))  for v in daily_calls]
    # V11.0 — stat-card labels swap per vertical. Real-estate says
    # "Inquiries answered" / "Active showings", legal says "Intakes"
    # / "Time-sensitive", etc. Numbers stay aggregate.
    portal_copy = (industry_meta or {}).get("portal_copy", {})
    stat_calls_label = portal_copy.get("stat_calls") or "Calls answered"
    stat_emerg_label = portal_copy.get("stat_emergencies") or "Emergencies routed safely"
    top_stats = (
        section_caption("This month")
        + stats([
            stat_card(f"{stat_calls_label} answered" if stat_calls_label == "Calls"
                      else stat_calls_label,
                       s["calls_handled"],
                       sparkline_values=daily_calls),
            stat_card(stat_emerg_label, s["emergencies"],
                       sparkline_values=daily_emerg),
            stat_card("Bookings captured", bookings,
                       sparkline_values=daily_book),
        ])
    )

    # ── Bare typographic hero — no card chrome around the headline ───
    headline = _today_headline(today_calls, today_msgs, today_emerg)
    invoice_action = ""
    if include_invoice_link and t:
        invoice_action = (
            f'<a href="/client/{html.escape(client_id)}/invoice/{html.escape(month)}{tq}" '
            f'class="btn">'
            f'{icon("calendar", size=14)} '
            f'{html.escape(month_label_short(month))} invoice</a>'
        )
    hero = (
        f'<div class="today-hero">'
        f'<div class="today-hero-text">'
        f'<h2 class="today-headline">{html.escape(headline)}</h2>'
        # V13.0 — pre-V13.0 today-sub had a second sentence ("This
        # page updates as calls come in.") explaining live-refresh
        # behavior. Real SaaS apps don't tell you they update; they
        # just do. The Live pulse already conveys it.
        f'<p class="today-sub">Your receptionist is on the line.</p>'
        f'</div>'
        f'{invoice_action}'
        f'</div>'
    )

    return hero + activity_html + followups_html + top_stats


@router.get("/{client_id}", response_class=HTMLResponse)
def summary(client_id: str, t: str = "", request: Request = None):
    """V9.1 — Today is a communications-first feed. V9.5 — body
    composition lives in _today_body() so the public demo at / can
    render the same content with the same components."""
    client = _require(client_id, t)
    body = _today_body(client_id, t=t, include_invoice_link=True)
    return HTMLResponse(page(
        title=client["name"],
        subtitle="Today",
        body=body,
        nav=_nav(client_id, t), active=f"/client/{client_id}?t={html.escape(t)}",
        accent="client",
        brand=(client.get("brand_display_name") or client["name"]),
        brand_logo_url=client.get("brand_logo_url") or None,
        custom_accent_hex=client.get("brand_accent_color") or None,
        footer_note="Questions? Reply to the email that delivered this link.",
    ))


@router.get("/{client_id}/conversations", response_class=HTMLResponse)
@router.get("/{client_id}/calls", response_class=HTMLResponse)  # legacy alias
def conversations_list(client_id: str, t: str = "", limit: int = 50):
    """V9.1 — Conversations is the unified-by-partner view: one card
    per phone number, showing the most recent activity across calls +
    SMS. Replaces the per-call list from V9.0 (the brief explicitly
    wants 'one conversation history')."""
    client = _require(client_id, t)
    partners = usage.list_conversation_partners(client_id, limit=limit)
    tq = f"?t={html.escape(t)}"
    now_ts = int(time.time())

    if not partners:
        body = card(
            f'<div class="empty empty-warm">'
            f'<div class="empty-icon">{icon("phone", size=20)}</div>'
            f'<div class="empty-title">No conversations yet</div>'
            f'<div class="empty-sub">Calls and texts will show up here '
            f'as soon as they come in.</div></div>',
            flush=True,
        )
    else:
        cards = []
        for p in partners:
            when = _human_when(p["last_ts"], now_ts)
            channel_marker = (
                "Text message" if p["last_channel"] == "sms" else "Phone call")
            chips = []
            if p["calls"]:
                chips.append(f"{p['calls']} call{'s' if p['calls'] != 1 else ''}")
            if p["messages"]:
                chips.append(f"{p['messages']} text{'s' if p['messages'] != 1 else ''}")
            count_line = " · ".join(chips)
            summary = p.get("last_summary") or (
                f"{channel_marker}" + (f" — {count_line}" if count_line else "")
            )
            preview = _render_partner_preview(
                client_id, p["phone"], t, p.get("last_call_sid", ""))
            is_live = (now_ts - int(p["last_ts"] or 0)) <= 60
            cards.append(call_card(
                caller=_partner_label(p["phone"]),
                from_number=p["phone"],
                when=when,
                photo_url=partner_photo_url(p["phone"]),
                summary=summary,
                status="answered",
                preview_html=preview,
                live=is_live,
            ))
        # V9.4 — the page header already says "Conversations". No
        # redundant card title; the list IS the page. Bare typographic
        # caption above gives partner count without competing visually.
        count_label = (
            f"{len(partners)} {'person' if len(partners) == 1 else 'people'}"
        )
        # V10.3 — inline search filters the partner list clientside.
        search_input = (
            '<div class="conv-search">'
            '<span class="conv-search-icon">'
            f'{icon("search", size=14)}'
            '</span>'
            '<input type="search" id="conv-filter" '
            'placeholder="Search by name or phone…" autocomplete="off">'
            '</div>'
            '<script>'
            '(function(){'
            'const $f = document.getElementById("conv-filter");'
            'if(!$f) return;'
            'const $cards = document.querySelectorAll("section.card.flush .call, section.card.flush details.call");'
            '$f.addEventListener("input", function(){'
            '  const q = ($f.value||"").toLowerCase().trim();'
            '  $cards.forEach(c=>{'
            '    const text = c.textContent.toLowerCase();'
            '    c.style.display = (!q || text.indexOf(q)>=0) ? "" : "none";'
            '  });'
            '});'
            '})();'
            '</script>'
        )
        body = (
            f'<div class="list-count">{html.escape(count_label)}</div>'
            + search_input
            + card("".join(cards), flush=True)
        )

    return HTMLResponse(page(
        title=client["name"],
        subtitle="Conversations",
        body=body,
        nav=_nav(client_id, t),
        active=f"/client/{client_id}/conversations?t={html.escape(t)}",
        accent="client",
        brand=(client.get("brand_display_name") or client["name"]),
        brand_logo_url=client.get("brand_logo_url") or None,
        custom_accent_hex=client.get("brand_accent_color") or None,
    ))


@router.get("/{client_id}/conversations/{phone_slug}",
            response_class=HTMLResponse)
def conversation_detail(client_id: str, phone_slug: str, t: str = ""):
    """V9.1 — unified per-partner thread: every call + SMS exchange
    with one phone number, chronological."""
    client = _require(client_id, t)
    from src import transcripts
    # phone_slug is normalized digits. Run it back through normalize to
    # be safe against operators pasting raw URLs.
    from memory import normalize_phone
    norm = normalize_phone(phone_slug)
    if not norm:
        raise HTTPException(404, "conversation not found")
    phone = "+" + norm
    tq = f"?t={html.escape(t)}"

    turns = transcripts.list_by_phone(client_id, phone, limit=500)
    # Group turns by call_sid for distinguishable conversation blocks.
    blocks: list = []
    current: dict = {}
    for turn in turns:
        sid = turn["call_sid"]
        if not current or current["call_sid"] != sid:
            current = {"call_sid": sid, "channel": turn["channel"],
                       "turns": [], "meta": None}
            blocks.append(current)
        current["turns"].append(turn)

    # Resolve meta for each voice block.
    for blk in blocks:
        if blk["channel"] == "voice":
            blk["meta"] = transcripts.get_call_meta(blk["call_sid"])

    # V9.4 — bare typographic hero. The partner name IS the page anchor;
    # phone is secondary; "Call back" is a clear inline action.
    back_to_list = (
        f'<a href="/client/{html.escape(client_id)}/conversations{tq}" '
        f'class="back-link muted">← All conversations</a>'
    )
    head = (
        f'<div class="thread-hero">'
        f'{back_to_list}'
        f'<div class="thread-hero-row">'
        f'<div>'
        f'<h2 class="thread-hero-name">{html.escape(_partner_label(phone))}</h2>'
        f'<div class="thread-hero-phone muted">{html.escape(phone)}</div>'
        f'</div>'
        f'<a class="btn" href="tel:{html.escape(phone)}">'
        f'{icon("phone", size=14)} Call back</a>'
        f'</div></div>'
    )

    if not blocks:
        body = head + card(
            f'<div class="empty empty-warm">'
            f'<div class="empty-icon">{icon("voicemail", size=20)}</div>'
            f'<div class="empty-title">No history with this number yet</div>'
            f'<div class="empty-sub">When this caller next contacts you, '
            f'their full thread will appear here.</div></div>',
            flush=True,
        )
    else:
        thread_html = []
        for blk in blocks:
            thread_html.append(_render_thread_block(blk, client_id, t))
        body = head + card("".join(thread_html), flush=True)

    return HTMLResponse(page(
        title=client["name"],
        subtitle=_partner_label(phone),
        body=body,
        nav=_nav(client_id, t),
        active=f"/client/{client_id}/conversations?t={html.escape(t)}",
        accent="client",
        brand=(client.get("brand_display_name") or client["name"]),
        brand_logo_url=client.get("brand_logo_url") or None,
        custom_accent_hex=client.get("brand_accent_color") or None,
    ))


@router.get("/{client_id}/call/{call_sid}", response_class=HTMLResponse)
def call_detail(client_id: str, call_sid: str, t: str = ""):
    """V9.3 — single-call detail, redesigned to share the V9.2 bubble
    pattern with conversation_detail. Hero strip with name + status +
    duration, optional summary card, then the bubble timeline."""
    client = _require(client_id, t)
    from src import transcripts as _transcripts
    meta = _transcripts.get_call_meta(call_sid)
    if not meta or meta.get("client_id") != client_id:
        raise HTTPException(404, "call not found")
    turns = _transcripts.get_transcript(call_sid)

    raw_phone = meta.get("from_number") or ""
    start_dt = datetime.fromtimestamp(meta["start_ts"], tz=timezone.utc)
    when_label = start_dt.strftime("%b %d · %I:%M %p").replace(" 0", " ")
    duration = _fmt_duration(meta.get("duration_s") or 0)
    raw_outcome = (meta.get("outcome") or "").strip().lower()
    status_html = (status_pill("emergency") if meta.get("emergency")
                   else status_pill(raw_outcome or "answered"))

    # ── V9.4 — bare typographic hero, same pattern as thread detail ────
    tq = f"?t={html.escape(t)}"
    back_href = (
        f"/client/{html.escape(client_id)}/conversations/"
        f"{html.escape(_phone_slug(raw_phone))}{tq}"
    ) if raw_phone else (
        f"/client/{html.escape(client_id)}/conversations{tq}"
    )
    header = (
        f'<div class="thread-hero">'
        f'<a href="{back_href}" class="back-link muted">'
        f'← Back to conversation</a>'
        f'<div class="thread-hero-row">'
        f'<div>'
        f'<h2 class="thread-hero-name">'
        f'{html.escape(_partner_label(raw_phone) or "Caller")}</h2>'
        f'<div class="thread-hero-phone muted">'
        f'{html.escape(raw_phone)}{" · " if raw_phone else ""}'
        f'{html.escape(when_label)} · {html.escape(duration)}</div>'
        f'</div>'
        f'<div>{status_html}</div>'
        f'</div></div>'
    )

    # ── Summary (soft variant — context, not data) ────────────────────
    summary_card = ""
    summary_text = (meta.get("summary") if hasattr(meta, "get") else None)
    if summary_text:
        summary_card = (
            section_caption("Call summary")
            + card(
                f'<p style="margin:0;font-size:15px;line-height:1.55;">'
                f'{html.escape(summary_text)}</p>',
                variant="soft",
            )
        )

    # ── Recording indicator (soft variant — visibility, not action) ──
    rec_card = ""
    try:
        from src import recordings as _rec
        rec = _rec.get_recording(call_sid)
        if rec and rec.get("recording_url"):
            rec_dur = _fmt_duration(int(rec.get("recording_duration_s") or 0))
            rec_card = card(
                f'<div class="row" style="align-items:center;gap:12px;">'
                f'<div style="color:var(--accent);flex-shrink:0;">'
                f'{icon("voicemail", size=18)}</div>'
                f'<div><div style="font-weight:600;font-size:15px;">'
                f'Audio recording captured</div>'
                f'<div class="muted" style="font-size:13px;margin-top:2px;">'
                f'{html.escape(rec_dur)} — available to your account contact</div>'
                f'</div></div>',
                variant="soft",
            )
    except Exception:
        rec_card = ""

    # ── Bubble timeline (V9.2 helper) ──────────────────────────────
    if turns:
        bubble_html = _render_bubble_sequence(turns)
        conv = card(
            f'<div class="bubbles">{bubble_html}</div>',
            flush=True,
        )
    else:
        conv = card(
            '<div class="empty empty-warm">'
            f'<div class="empty-icon">{icon("voicemail", size=20)}</div>'
            '<div class="empty-title">No transcript captured</div>'
            '<div class="empty-sub">This call ended before any conversation '
            'was recorded.</div></div>',
            flush=True,
        )

    body = header + summary_card + rec_card + conv
    return HTMLResponse(page(
        title=client["name"],
        subtitle="Call detail",
        body=body,
        nav=_nav(client_id, t),
        active=f"/client/{client_id}/conversations?t={html.escape(t)}",
        accent="client",
        brand=(client.get("brand_display_name") or client["name"]),
        brand_logo_url=client.get("brand_logo_url") or None,
        custom_accent_hex=client.get("brand_accent_color") or None,
    ))


@router.get("/{client_id}/invoice/{month}", response_class=HTMLResponse)
def invoice_view(client_id: str, month: str, t: str = ""):
    client = _require(client_id, t)
    try:
        from src import invoices as _inv
        invoice = _inv.generate_invoice(client, month)
        body = card(_inv.render_invoice_html(invoice))
    except ImportError:
        body = card(_fallback_invoice_body(client, month))
    return HTMLResponse(page(
        title=f"Invoice · {month}",
        subtitle=f"For {client['name']}",
        body=body,
        nav=_nav(client_id, t),
        active=f"/client/{client_id}/invoice/{html.escape(month)}?t={html.escape(t)}",
        accent="client",
        brand=(client.get("brand_display_name") or client["name"]),
        brand_logo_url=client.get("brand_logo_url") or None,
        custom_accent_hex=client.get("brand_accent_color") or None,
        footer_note="Print-friendly · Ctrl+P or ⌘P",
    ))


# ── V9.0 — Follow-ups + Settings ──────────────────────────────────────

# Callers who didn't get a real conversation in the last week (no
# answer / silence / wrong-number / spam are excluded since those need
# no follow-up). Emergencies are excluded — they already went to the
# operator's cell. The result is "calls that came in, were handled, and
# might benefit from a human touch."
_FOLLOWUP_WINDOW_SECONDS = 7 * 24 * 60 * 60


def _followup_candidates(client_id: str, limit: int = 50) -> list:
    """Heuristic: short answered calls in the last 7 days that aren't
    emergencies and didn't go to spam. The operator decides who to
    actually call back; we just surface the candidates."""
    cutoff = int(time.time()) - _FOLLOWUP_WINDOW_SECONDS
    out = []
    for r in usage.recent_calls(client_id=client_id, limit=200):
        if r["start_ts"] < cutoff:
            continue
        if r.get("emergency"):
            continue
        outcome = (r.get("outcome") or "").lower()
        if outcome in ("spam_number", "spam_phrase", "wrong_number"):
            continue
        # Short calls (< 25s) or silence-timeout calls are the typical
        # "they hung up before they got what they wanted" pattern.
        dur = int(r.get("duration_s") or 0)
        if dur >= 60 and outcome not in ("silence_timeout", "no_answer"):
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


@router.get("/{client_id}/followups", response_class=HTMLResponse)
def followups(client_id: str, t: str = ""):
    """Callers who may want a human callback — short calls, no-answer
    timeouts, the kind of thing where a 60-second human follow-up
    converts a missed lead into a booked job."""
    client = _require(client_id, t)
    candidates = _followup_candidates(client_id)
    rows = []
    for r in candidates:
        ts = datetime.fromtimestamp(r["start_ts"], tz=timezone.utc).strftime(
            "%b %d · %H:%M")
        from_n = r.get("from_number") or ""
        summary = (r.get("summary") if "summary" in (r.keys() if hasattr(r, "keys") else []) else None) or ""
        sum_cell = (
            f'<span class="muted" style="font-style:italic">{html.escape(summary)}</span>'
            if summary else '<span class="muted">—</span>'
        )
        detail = (
            f'<a href="/client/{html.escape(client_id)}/call/{html.escape(r["call_sid"])}'
            f'?t={html.escape(t)}">View →</a>'
        ) if r["call_sid"] else ""
        rows.append([
            (html.escape(ts), "muted"),
            html.escape(from_n) if from_n else "",
            (_fmt_duration(r.get("duration_s") or 0), "num muted"),
            sum_cell,
            detail,
        ])

    body = card(
        data_table(
            headers=["When", "From", ("Duration", "num"), "What they said", ""],
            rows=rows,
            empty_text=(
                "No follow-ups needed right now. Every call this week was "
                "handled cleanly."
            ),
        ),
        title="Callers worth a follow-up",
        subtitle="Short or interrupted calls in the last 7 days",
    )
    return HTMLResponse(page(
        title=client["name"],
        subtitle="Follow-ups",
        body=body,
        nav=_nav(client_id, t),
        active=f"/client/{client_id}/followups?t={html.escape(t)}",
        accent="client",
        brand=(client.get("brand_display_name") or client["name"]),
        brand_logo_url=client.get("brand_logo_url") or None,
        custom_accent_hex=client.get("brand_accent_color") or None,
    ))


@router.get("/{client_id}/settings", response_class=HTMLResponse)
def settings(client_id: str, t: str = ""):
    """Read-only view of the tenant's current configuration.

    V9.0 — deliberately NOT editable. The brief explicitly asks to
    'aggressively reduce unnecessary settings' and prefer 'opinionated
    defaults'. Operators see what's set + a clear path to change it
    ('contact us'). Cuts the surface from a config wizard to a
    one-screen reference card.
    """
    client = _require(client_id, t)
    rows = [
        ["Business name", html.escape(client.get("name") or "—")],
        ["Owner", html.escape(client.get("owner_name") or "—")],
        ["Hours", html.escape(client.get("hours") or "—")],
        ["Transfer number",
         html.escape(client.get("escalation_phone") or "—")],
        ["Service area", html.escape(client.get("service_area") or "—")],
        ["Default language",
         html.escape((client.get("default_language") or "en").upper())],
    ]
    services = (client.get("services") or "").strip()

    # V9.4 — section captions over flush cards, soft variant for the
    # context block. No redundant card titles competing with the page
    # header.
    settings_card = (
        section_caption("Account")
        + card(data_table(headers=["", ""], rows=rows), flush=True)
    )
    services_block = ""
    if services:
        services_block = (
            section_caption("What the receptionist knows")
            + card(
                f'<p style="margin:0;line-height:1.55">{html.escape(services)}</p>',
                variant="soft",
            )
        )
    contact_note = card(
        f'<div class="row" style="align-items:flex-start;gap:12px;">'
        f'<div style="color:var(--accent);flex-shrink:0;margin-top:2px;">'
        f'{icon("settings", size=18)}</div>'
        f'<div><div style="font-weight:600;font-size:15px;">'
        f'Need a change?</div>'
        f'<p class="muted" style="margin:4px 0 0;font-size:14px;line-height:1.5">'
        f'Reply to the welcome email we sent, or call your account '
        f'contact. Changes typically apply within a few minutes.</p>'
        f'</div></div>',
        variant="soft",
    )
    body = settings_card + services_block + contact_note
    return HTMLResponse(page(
        title=client["name"],
        subtitle="Settings",
        body=body,
        nav=_nav(client_id, t),
        active=f"/client/{client_id}/settings?t={html.escape(t)}",
        accent="client",
        brand=(client.get("brand_display_name") or client["name"]),
        brand_logo_url=client.get("brand_logo_url") or None,
        custom_accent_hex=client.get("brand_accent_color") or None,
    ))


# ── helpers ────────────────────────────────────────────────────────────

def _fmt_duration(seconds: int) -> str:
    """Plain-English duration. 47s; 1m 12s; 12m 03s."""
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    return f"{m}m {s:02d}s"


def _render_partner_preview(client_id: str, phone: str,
                              t: str = "",
                              last_call_sid: str = "",
                              n_turns: int = 3) -> str:
    """V10.2 — last N turns + a "View full thread" link, for inline
    expansion in the partner call card. Empty string if no turns
    found (caller can skip the expandable variant)."""
    if not phone:
        return ""
    try:
        from src import transcripts as _t
        turns = _t.list_by_phone(client_id, phone, limit=50)
    except Exception:
        return ""
    if not turns:
        return ""
    # Last N chronologically. list_by_phone returns ASC.
    tail = turns[-n_turns:]
    bubbles = []
    for tn in tail:
        side = "in" if tn["role"] == "user" else "out"
        bubbles.append(
            f'<div class="preview-bubble {side}">'
            f'{html.escape(tn["text"])}'
            f'</div>'
        )
    bubble_html = (
        '<div class="preview-bubbles">' + "".join(bubbles) + '</div>'
    )

    # V10.3 — recording mock for voice-channel partners. Pure visual:
    # 10-bar waveform that animates for ~3.5s when the play button is
    # clicked. No actual audio (the demo doesn't have call recordings).
    has_voice = any(t.get("channel") == "voice" for t in tail)
    if has_voice:
        # Synth a plausible duration from the call meta if available;
        # falls back to a flat "0:24" so the prospect sees a label.
        duration = "0:24"
        try:
            voice_turn = next(t for t in reversed(tail)
                               if t.get("channel") == "voice")
            meta = _t.get_call_meta(voice_turn["call_sid"])
            if meta and meta.get("duration_s"):
                m, s = divmod(int(meta["duration_s"]), 60)
                duration = f"{m}:{s:02d}"
        except Exception:
            pass
        # V10.5 — calmer waveform: 5 bars (was 10), no staggered
        # animation. The bars stay static; only the progress bar
        # below animates during playback. Less attention-grabbing.
        rec_player = (
            f'<div class="rec-player">'
            f'<button class="rec-play-btn" type="button" '
            f'aria-label="Play recording"></button>'
            f'<div class="rec-waveform" aria-hidden="true">'
            + ('<span></span>' * 5) +
            f'</div>'
            f'<div class="rec-meta">Recording · {html.escape(duration)}</div>'
            f'<div class="rec-progress"><div class="rec-progress-fill"></div></div>'
            f'</div>'
        )
        bubble_html = rec_player + bubble_html

    # Foot: "View full thread" link if we have a token (real portal).
    tq = f"?t={html.escape(t)}" if t else ""
    foot_link = ""
    if t:
        foot_link = (
            f'<a href="/client/{html.escape(client_id)}/conversations/'
            f'{html.escape(_phone_slug(phone))}{tq}">View full thread →</a>'
        )
    elif last_call_sid:
        # On the demo pane (no token) the call_sid is still useful as
        # a label for the prospect.
        foot_link = (
            f'<span class="muted">'
            f'Showing last {len(tail)} message{"s" if len(tail) != 1 else ""}'
            f'</span>'
        )

    if foot_link:
        return bubble_html + f'<div class="preview-foot">{foot_link}</div>'
    return bubble_html


def month_label_short(month: str) -> str:
    """'2026-05' → 'May'. Used for the inline invoice button label."""
    try:
        dt = datetime.strptime(month, "%Y-%m")
        return dt.strftime("%b")
    except Exception:
        return "Current"


# ── V9.2 — Today emotional helpers ────────────────────────────────────

def _today_emergency_count(client_id: str, since_ts: int) -> int:
    """How many emergencies were routed in the last 24h. Used by the
    Today hero headline so the operator instantly sees if there's
    anything that needed escalation."""
    with usage._db_lock:
        conn = usage._connect()
        usage._init_schema(conn)
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM calls
                WHERE client_id = ?
                  AND start_ts >= ?
                  AND emergency = 1""",
            (client_id, since_ts),
        ).fetchone()
        n = int(row["n"]) if row else 0
        conn.close()
    return n


def _today_headline(calls: int, msgs: int, emergencies: int) -> str:
    """Reassuring, context-aware hero headline.

    Goal: in <3 seconds the operator subconsciously feels 'my line is
    handling things'. Specific over vague — '4 calls today' beats
    'your line is on'."""
    if calls == 0 and msgs == 0:
        return "Quiet line today."
    parts: list = []
    if calls:
        parts.append(f"{calls} call{'s' if calls != 1 else ''}")
    if msgs:
        parts.append(f"{msgs} text{'s' if msgs != 1 else ''}")
    base = " and ".join(parts) + " today"
    if emergencies == 1:
        return base + " — 1 emergency routed."
    if emergencies > 1:
        return base + f" — {emergencies} emergencies routed."
    return base + "."


# ── V9.1 — conversation-view helpers ──────────────────────────────────

def _human_when(ts: int, now_ts: int) -> str:
    """Relative time for the activity feed. '2m ago', '4h ago',
    'yesterday', 'Mon 3:15 PM', then the date for older items."""
    if not ts:
        return ""
    diff = max(0, int(now_ts) - int(ts))
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    if diff < 2 * 86400:
        return "yesterday"
    if diff < 7 * 86400:
        # %I gives zero-padded hour (12-hour clock). Strip the leading
        # zero manually so output reads "3:15 PM" not "03:15 PM".
        # Cross-platform — Windows doesn't support `%-I`.
        s = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%a %I:%M %p")
        return s.replace(" 0", " ")
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d")


def _partner_label(phone: str) -> str:
    """Look up a name from memory for this phone number; fall back to
    the formatted phone. Customer-facing — never shows internal IDs.

    V11.1 — the pre-V11.1 lookup did `get_caller(normalize_phone(phone))`
    which assumed memory.json was keyed by phone digits. It isn't —
    entries are keyed by caller_id (e.g., "re_caleb", "hvac_marcus").
    So portal cards rendered "(555) 010-3001" even when Caleb Morrison
    existed in memory. Fix: scan all callers and match by their phone
    field. O(n) but n is small (51 personas + organic callers)."""
    if not phone:
        return "Unknown caller"
    try:
        from memory import normalize_phone, list_callers
        target = normalize_phone(phone)
        if target:
            for rec in list_callers():
                if normalize_phone(rec.get("phone", "")) == target:
                    name = (rec.get("name") or "").strip()
                    if name and name != "Unknown caller":
                        return name
    except Exception:
        pass
    return _format_phone(phone)


def _format_phone(phone: str) -> str:
    """+15551234567 → (555) 123-4567; anything else passes through."""
    s = (phone or "").strip()
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
    return s or "Unknown"


def _phone_slug(phone: str) -> str:
    """URL-safe canonical form of a phone for conversation routes."""
    try:
        from memory import normalize_phone
        return normalize_phone(phone or "") or ""
    except Exception:
        return "".join(c for c in (phone or "") if c.isdigit())


def _render_thread_block(blk: dict, client_id: str, t: str) -> str:
    """One per-call block in the conversation detail thread."""
    sid = blk["call_sid"]
    channel = blk["channel"]
    meta = blk.get("meta") or {}
    turns = blk["turns"]
    when_ts = turns[0]["ts"] if turns else 0
    when_label = (
        datetime.fromtimestamp(when_ts, tz=timezone.utc).strftime("%b %d · %I:%M %p")
        if when_ts else ""
    )

    if channel == "voice":
        chip = status_pill("emergency") if meta.get("emergency") else \
            status_pill((meta.get("outcome") or "").strip().lower() or "answered")
        duration = _fmt_duration(meta.get("duration_s") or 0)
        head = (
            f'<div class="thread-head"><span class="thread-icon">'
            f'{icon("phone", size=14)}</span>'
            f'<span class="thread-meta">'
            f'<b>Voice call</b> · {html.escape(when_label)} · '
            f'<span class="muted">{html.escape(duration)}</span></span>'
            f'<span class="ml-auto">{chip}</span></div>'
        )
        deeper = (
            f'<div style="margin-top:6px"><a class="muted" href="/client/'
            f'{html.escape(client_id)}/call/{html.escape(sid)}?t={html.escape(t)}">'
            f'Open full call detail →</a></div>'
        )
    else:
        chip = status_pill("answered")
        head = (
            f'<div class="thread-head"><span class="thread-icon">'
            f'{icon("voicemail", size=14)}</span>'
            f'<span class="thread-meta">'
            f'<b>Text message</b> · {html.escape(when_label)}</span>'
            f'<span class="ml-auto">{chip}</span></div>'
        )
        deeper = ""

    msgs = _render_bubble_sequence(turns)

    return (
        f'<div class="thread-block">{head}'
        f'<div class="bubbles">{msgs}</div>'
        f'{deeper}</div>'
    )


# Gap between turns at which we insert a fresh time-chip (5 min).
_TIME_CHIP_GAP_S = 5 * 60


def _render_bubble_sequence(turns: list) -> str:
    """V9.2 — iMessage-style grouping. Consecutive same-role turns are
    visually grouped (tight 2px gap, single sender caption above the
    first); role switches break with a 14px+ gap and a fresh caption.
    A time-chip is inserted at the top of each thread and whenever a
    >5 min gap or day boundary opens.

    No per-bubble timestamps — they were noisy and low-contrast in V9.1.
    """
    parts: list = []
    prev_role: str = ""
    prev_ts: int = 0
    prev_day: str = ""
    for i, turn in enumerate(turns):
        role = turn["role"]
        side = "in" if role == "user" else "out"
        ts = int(turn["ts"] or 0)
        day = (datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
               if ts else "")
        # Time chip — at the very start, on a >5min gap, or new day.
        if i == 0 or (ts - prev_ts) > _TIME_CHIP_GAP_S or day != prev_day:
            parts.append(_render_time_chip(ts))
        # Sender caption — once per series of consecutive same-role turns.
        if role != prev_role:
            label = "Joanna" if role == "assistant" else "Caller"
            parts.append(
                f'<div class="sender-cap {side}">'
                f'{html.escape(label)}</div>'
            )
        # The bubble itself.
        next_role = turns[i + 1]["role"] if (i + 1) < len(turns) else None
        end_class = " series-end" if next_role != role else ""
        parts.append(
            f'<div class="bubble {side}{end_class}">'
            f'{html.escape(turn["text"])}</div>'
        )
        prev_role = role
        prev_ts = ts
        prev_day = day
    return "".join(parts)


def _render_time_chip(ts: int) -> str:
    """Human-readable temporal anchor between bubble groups."""
    if not ts:
        return ""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    now_dt = datetime.fromtimestamp(int(time.time()), tz=timezone.utc)
    same_day = (dt.date() == now_dt.date())
    yesterday = (dt.date() == (now_dt.date() -
                                 (now_dt.date() - now_dt.date())))
    # Compute "yesterday" properly
    from datetime import timedelta
    is_yesterday = dt.date() == (now_dt.date() - timedelta(days=1))
    time_part = dt.strftime("%I:%M %p").lstrip("0")
    if same_day:
        when = f"Today at {time_part}"
    elif is_yesterday:
        when = f"Yesterday at {time_part}"
    else:
        when = dt.strftime("%b %d at ") + time_part
    return f'<div class="time-chip">{html.escape(when)}</div>'


def _fallback_invoice_body(client: dict, month: str) -> str:
    s = usage.monthly_summary(client["id"], month=month)
    plan = client.get("plan") or {}
    monthly = float(plan.get("monthly_price") or 0)
    included = float(plan.get("included_calls") or 0)
    overage_rate = float(plan.get("overage_rate_per_call") or 0)
    overage_calls = max(0, s["calls_handled"] - int(included)) if included else 0
    overage_cost = overage_calls * overage_rate
    total = monthly + overage_cost
    rows = [
        ["Monthly service plan", ("1", "num"), (f"${monthly:.2f}", "num")],
        ["Included calls", (f"{int(included)}", "num"), ("—", "num muted")],
        ["Calls handled this month", (f"{s['calls_handled']}", "num"), ("—", "num muted")],
        [f"Overage calls ({overage_calls} × ${overage_rate:.2f})",
         (f"{overage_calls}", "num"), (f"${overage_cost:.2f}", "num")],
        ["Total", "", (f"<b>${total:.2f}</b>", "num")],
    ]
    return f"""
<div class="invoice">
  <div class="head">
    <div><h2 style="margin:0">Invoice — {html.escape(month)}</h2>
         <div class="muted">Billed to {html.escape(client.get('name') or '')}</div></div>
    <div style="text-align:right">
      <div class="muted">Plan</div>
      <div><b>{html.escape((plan.get('tier') or 'Standard').title())}</b></div>
    </div>
  </div>
  {data_table(headers=["Line", ("Qty", "num"), ("Amount", "num")], rows=rows)}
  <p class="muted" style="margin-top:var(--s-4);">
    Questions or disputes — reply to the email that delivered this invoice,
    or call {html.escape(client.get('owner_name') or 'the office')} directly.
  </p>
</div>
"""


def _count_bookings(client_id: str, month: str) -> int:
    from src.usage import _connect, _init_schema, _db_lock
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        row = conn.execute(
            """SELECT COUNT(DISTINCT call_sid) AS n FROM turns
                WHERE client_id=? AND month=? AND intent='Scheduling'
                  AND call_sid <> ''""",
            (client_id, month),
        ).fetchone()
        conn.close()
    return int(row["n"] or 0)


def _last_call_row(client_id: str) -> Optional[dict]:
    rows = usage.recent_calls(client_id=client_id, limit=1)
    return rows[0] if rows else None


def _top_intent_for_call(call_sid: str) -> Optional[str]:
    if not call_sid:
        return None
    from src.usage import _connect, _init_schema, _db_lock
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        row = conn.execute(
            """SELECT intent, COUNT(*) AS n FROM turns
                WHERE call_sid=? AND intent IS NOT NULL AND intent <> ''
                GROUP BY intent ORDER BY n DESC LIMIT 1""",
            (call_sid,),
        ).fetchone()
        conn.close()
    return row["intent"] if row else None


def _friendly_outcome(raw: str) -> str:
    return {
        "normal": "Handled",
        "no_answer": "No answer",
        "busy": "Busy",
        "failed": "Failed",
        "canceled": "Canceled",
        "spam_number": "Filtered (spam)",
        "spam_phrase": "Filtered (spam)",
        "silence_timeout": "No response",
        "emergency_transfer": "Emergency routed",
        "duration_capped": "Wrapped at cap",
    }.get(raw, raw or "—")


# ── CLI ────────────────────────────────────────────────────────────────

def _cli(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.client_portal")
    sub = p.add_subparsers(dest="cmd", required=True)
    issue = sub.add_parser("issue", help="print a portal URL for a client")
    issue.add_argument("client_id")
    issue.add_argument("--base-url", default=None,
                       help="override PUBLIC_BASE_URL env var")

    args = p.parse_args(argv)
    if args.cmd == "issue":
        c = tenant.load_client_by_id(args.client_id)
        if c is None or (c.get("id") or "").startswith("_"):
            print(f"Unknown client: {args.client_id}", file=sys.stderr)
            return 2
        if not _secret():
            print("CLIENT_PORTAL_SECRET is not set — refusing to mint a token.",
                  file=sys.stderr)
            print("Set it in .env, then re-run.", file=sys.stderr)
            return 2
        print(portal_url(args.client_id, base_url=args.base_url))
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
