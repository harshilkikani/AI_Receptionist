"""Admin dashboard — per-client usage, margin, analytics, evals, export.

Mounted under /admin/* by main.py. Optional HTTP Basic auth via env vars
ADMIN_USER / ADMIN_PASS. If either is unset, the admin endpoints are
accessible without auth (suitable for local-only operation).

All routes read-only. No mutations — operators edit YAML/JSON on disk
and restart.

Visual language lives in src.design — use page/card/data_table/stats/
stat_card/sparkline/heatbar/pill here. No inline CSS, no JS.
"""
from __future__ import annotations

import csv
import html
import io
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from src import tenant, usage, alerts
from src.admin_auth import check_admin_auth, auth_required
from src.design import (
    card, data_table, heatbar, page, pill, sparkline, stat_card, stats,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# Nav items shown on every admin page
NAV: list = [
    ("Overview",     "/admin"),
    ("Recent calls", "/admin/calls"),
    ("Live",         "/admin/live"),
    ("Bookings",     "/admin/bookings"),
    ("Analytics",    "/admin/analytics"),
    ("Evals",        "/admin/evals"),
    ("Export CSV",   "/admin/export.csv"),
    ("Feature flags", "/admin/flags"),
]


# Backwards-compat shims so the rest of admin.py (and any test that
# imports the names directly) keeps working after the V5.2 refactor.
_auth_required = auth_required
_check_auth = check_admin_auth


def _now_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _previous_month(month: str) -> str:
    year, m = int(month[:4]), int(month[5:7])
    if m == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{m - 1:02d}"


def _active_clients() -> list:
    return [
        c for c in tenant.list_all()
        if not (c.get("id") or "").startswith("_")
        and (c.get("inbound_number") or "")
    ]


def _demo_clients() -> list:
    """Tenants that are real YAMLs but not wired to a live inbound number
    — e.g. `septic_pro` (website showcase). Included in admin views with
    a 'demo' badge so the operator can see activity there too."""
    return [
        c for c in tenant.list_all()
        if not (c.get("id") or "").startswith("_")
        and not (c.get("inbound_number") or "")
    ]


def _margin_row(margin: dict, *, demo: bool = False) -> list:
    """Return one row for the admin overview table (list of cells)."""
    pct = margin.get("margin_pct", 0)
    if margin["revenue_usd"] <= 0:
        health = pill("demo", "ghost") if demo else pill("—", "ghost")
    elif pct < 0:
        health = pill("loss", "bad")
    elif pct < 50:
        health = pill(f"{pct}%", "warn")
    else:
        health = pill(f"{pct}%", "good")

    label = html.escape(margin["client_id"])
    if demo:
        label += " " + pill("demo", "ghost")

    return [
        label,
        (f'{margin["total_calls"]}', "num"),
        (f'{margin["calls_filtered"]}', "num muted"),
        (f'{margin["total_minutes"]:.1f}', "num"),
        (f'{margin["llm_input_tokens"]:,}', "num muted"),
        (f'{margin["sms_segments"]}', "num"),
        (f'${margin["platform_cost_usd"]:.2f}', "num"),
        (f'${margin["revenue_usd"]:.2f}', "num"),
        (f'${margin["margin_usd"]:.2f}', "num"),
        (health, ""),
    ]


# ── Routes ─────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def overview(user=Depends(_check_auth)):
    month = _now_month()

    # Aggregate stat cards (top of page)
    totals = {"calls": 0, "minutes": 0.0, "emergencies": 0,
              "cost": 0.0, "revenue": 0.0}
    active = _active_clients()
    for c in active:
        m = usage.margin_for(c, month=month)
        totals["calls"] += m["total_calls"]
        totals["minutes"] += m["total_minutes"]
        totals["emergencies"] += m["emergencies"]
        totals["cost"] += m["platform_cost_usd"]
        totals["revenue"] += m["revenue_usd"]
    margin_pct = 0
    if totals["revenue"] > 0:
        margin_pct = int((totals["revenue"] - totals["cost"]) / totals["revenue"] * 100)

    top_stats = stats([
        stat_card("Active clients", len(active)),
        stat_card("Calls this month", f'{totals["calls"]:,}'),
        stat_card("Emergencies routed", totals["emergencies"]),
        stat_card("Minutes", f'{totals["minutes"]:.0f}'),
        stat_card("Revenue", f'${totals["revenue"]:.0f}',
                  delta=f'{margin_pct}% margin',
                  direction=("up" if margin_pct >= 50 else
                             "flat" if margin_pct > 0 else "down")),
    ])

    # Per-client rows
    rows = []
    for c in active:
        rows.append(_margin_row(usage.margin_for(c, month=month)))
    for c in _demo_clients():
        rows.append(_margin_row(usage.margin_for(c, month=month), demo=True))

    table = data_table(
        headers=[
            "Client",
            ("Calls", "num"), ("Filtered", "num"),
            ("Minutes", "num"), ("In tokens", "num"),
            ("SMS", "num"), ("Cost", "num"),
            ("Revenue", "num"), ("Margin $", "num"),
            "Health",
        ],
        rows=rows,
        empty_text="No active clients configured yet.",
    )

    body = top_stats + card(table, title="Per-client breakdown",
                            subtitle=f"Month: {month}")
    return HTMLResponse(page(
        title="Margin overview",
        body=body,
        nav=NAV, active="/admin",
        subtitle="Live cost + revenue by tenant",
        brand="Receptionist · Ops",
        footer_note=f"month {month}",
    ))


@router.get("/calls", response_class=HTMLResponse)
def recent_calls(limit: int = 50, client_id: str = "", user=Depends(_check_auth)):
    from src import call_summary
    rows_raw = usage.recent_calls(client_id=client_id or None, limit=limit)
    rows = []
    for r in rows_raw:
        start_iso = datetime.fromtimestamp(r["start_ts"], tz=timezone.utc).strftime(
            "%b %d %H:%M UTC")
        outcome = r.get("outcome") or "—"
        outcome_pill_variant = "good" if outcome == "normal" else (
            "warn" if outcome in ("duration_capped", "no_answer") else
            "bad" if outcome in ("spam_number", "spam_phrase") else "ghost")
        emoji = "🚨" if r.get("emergency") else ""
        call_sid = html.escape(r["call_sid"])
        detail_link = (
            f'<a href="/admin/call/{call_sid}">detail</a>'
            if call_sid else ""
        )
        # V3.4 — show AI summary when available (col may not exist in old DBs)
        summary = r.get("summary") if "summary" in r.keys() else None
        summary_cell = (
            f'<span class="muted" style="font-style:italic">{html.escape(summary)}</span>'
            if summary
            else '<span class="muted">—</span>'
        )
        rows.append([
            (html.escape(start_iso), "muted"),
            html.escape(r["client_id"]),
            (html.escape(r.get("from_number") or ""), "muted"),
            (f'{r.get("duration_s") or 0}s', "num"),
            pill(html.escape(outcome), outcome_pill_variant),
            summary_cell,
            emoji,
            detail_link,
        ])

    header_filter = f' · filter: <code>{html.escape(client_id)}</code>' if client_id else ""
    table = data_table(
        headers=["When", "Client", "From", ("Duration", "num"), "Outcome",
                 "AI summary", "Flag", ""],
        rows=rows,
        empty_text="No calls logged yet.",
    )
    body = card(table, title="Recent calls", subtitle=f"Last {limit}{header_filter}")
    return HTMLResponse(page(
        title="Recent calls", body=body,
        nav=NAV, active="/admin/calls",
        brand="Receptionist · Ops",
    ))


@router.get("/call/{call_sid}", response_class=HTMLResponse)
def call_detail(call_sid: str, user=Depends(_check_auth)):
    """V4 — per-call transcript + metadata view."""
    from src import transcripts
    meta = transcripts.get_call_meta(call_sid)
    if not meta:
        raise HTTPException(404, "call not found")
    turns = transcripts.get_transcript(call_sid)

    start_iso = datetime.fromtimestamp(meta["start_ts"], tz=timezone.utc).strftime(
        "%b %d, %Y %H:%M:%S UTC")
    end_iso = (datetime.fromtimestamp(meta["end_ts"], tz=timezone.utc).strftime(
        "%b %d, %Y %H:%M:%S UTC") if meta.get("end_ts") else "—")
    duration = f'{meta.get("duration_s") or 0}s'

    summary = meta.get("summary") if "summary" in (meta.keys() if hasattr(meta, "keys") else []) else None
    meta_rows = [
        ["Call SID", (f'<code>{html.escape(call_sid)}</code>', "mono")],
        ["Client", html.escape(meta["client_id"])],
        ["From", (html.escape(meta.get("from_number") or ""), "mono")],
        ["Start", html.escape(start_iso)],
        ["End", html.escape(end_iso)],
        ["Duration", (duration, "num")],
        ["Outcome", pill(meta.get("outcome") or "—", "ghost")],
        ["Emergency", "🚨 yes" if meta.get("emergency") else "no"],
    ]
    if summary:
        meta_rows.append(["AI summary",
                          f'<span style="font-style:italic">{html.escape(summary)}</span>'])
    meta_table = data_table(headers=["Field", "Value"], rows=meta_rows)

    # V4.5 — recording playback if available
    rec_html = ""
    try:
        from src import recordings as _rec
        rec = _rec.get_recording(call_sid)
        if rec and rec.get("recording_url"):
            duration = rec.get("duration_s") or 0
            rec_html = card(
                f'<audio controls preload="none" '
                f'src="/admin/recording/{html.escape(call_sid)}.mp3" '
                f'style="width:100%"></audio>'
                f'<p class="muted" style="margin-top:var(--s-2);">'
                f'Duration: {duration}s · '
                f'Recording SID: <code>{html.escape(rec.get("recording_sid") or "")}</code>'
                f'</p>',
                title="Audio recording",
            )
    except Exception:
        rec_html = ""

    if turns:
        conv_html = '<div style="display:flex;flex-direction:column;gap:var(--s-3);">'
        for t in turns:
            role = t["role"]
            badge = pill("caller" if role == "user" else "receptionist",
                         "ghost" if role == "user" else "info")
            ts = datetime.fromtimestamp(t["ts"], tz=timezone.utc).strftime("%H:%M:%S")
            intent_html = ""
            if t.get("intent"):
                intent_html = " " + pill(t["intent"], "info")
            conv_html += (
                f'<div style="padding:var(--s-3);'
                f'background:var(--n-50);border-radius:var(--radius-sm);'
                f'border:1px solid var(--border);">'
                f'<div class="row" style="margin-bottom:4px;">'
                f'{badge}{intent_html}'
                f'<span class="muted ml-auto mono" style="font-size:11px;">{ts}</span>'
                f'</div>'
                f'<div>{html.escape(t["text"])}</div>'
                f'</div>'
            )
        conv_html += '</div>'
    else:
        conv_html = (
            '<div class="empty">No transcript captured for this call. '
            'Transcripts are recorded for every turn starting after V4.</div>'
        )

    body = (
        card(meta_table, title="Call metadata") +
        rec_html +
        card(conv_html, title="Conversation transcript",
             subtitle=f"{len(turns)} turn(s)")
    )
    return HTMLResponse(page(
        title=f"Call · {call_sid[:12]}…",
        body=body,
        nav=NAV, active="/admin/calls",
        brand="Receptionist · Ops",
    ))


@router.get("/export.csv")
def export_csv(month: Optional[str] = None, user=Depends(_check_auth)):
    """CSV export of per-client monthly metrics — for overage billing."""
    active_clients = _active_clients()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "client_id", "month", "total_calls", "calls_handled", "calls_filtered",
        "emergencies", "total_minutes", "llm_input_tokens", "llm_output_tokens",
        "tts_chars", "sms_segments", "platform_cost_usd", "revenue_usd",
        "margin_usd", "margin_pct",
    ])
    for c in active_clients:
        m = usage.margin_for(c, month=month)
        writer.writerow([
            m["client_id"], m["month"], m["total_calls"], m["calls_handled"],
            m["calls_filtered"], m["emergencies"], m["total_minutes"],
            m["llm_input_tokens"], m["llm_output_tokens"], m["tts_chars"],
            m["sms_segments"], m["platform_cost_usd"], m["revenue_usd"],
            m["margin_usd"], m["margin_pct"],
        ])
    buf.seek(0)
    filename = f"usage_{month or _now_month()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/flags", response_class=HTMLResponse)
def flags_view(user=Depends(_check_auth)):
    def flag_row(name: str, desc: str) -> list:
        val = os.environ.get(name)
        if val is None:
            value_html = pill("(unset)", "ghost")
        else:
            is_on = val.lower() in ("true", "1", "yes")
            value_html = pill(val, "good" if is_on else "bad")
        return [(f'<code>{name}</code>', "mono"), value_html, desc]

    rows = [
        flag_row("MARGIN_PROTECTION_ENABLED",
                 "Global kill switch. When false, ALL enforcement is bypassed."),
        flag_row("ENFORCE_CALL_DURATION_CAP",
                 "Hard cap call duration at 240s (360s for emergencies)."),
        flag_row("ENFORCE_SPAM_FILTER",
                 "Reject known spam numbers and phrase-detected calls."),
        flag_row("ENFORCE_SMS_CAP",
                 "Cap outbound SMS at plan.sms_max_per_call (default 3)."),
        flag_row("ENFORCE_USAGE_ALERTS",
                 "Send daily digest email/webhook when thresholds crossed."),
        flag_row("ENFORCE_OWNER_EMERGENCY_SMS",
                 "Push SMS to owner before emergency transfer (P3)."),
        flag_row("ENFORCE_OWNER_DIGEST",
                 "Fire per-client 22:00-local daily summary (P4)."),
        flag_row("TWILIO_VERIFY_SIGNATURES",
                 "403 any webhook whose X-Twilio-Signature doesn't match (P6)."),
        flag_row("ENFORCE_EVAL_REGRESSION",
                 "Nightly eval run + alert on >5pp drop (P7)."),
        flag_row("PROMPT_CACHE_ENABLED",
                 "Send system prompt as cacheable blocks to Anthropic (P8)."),
        flag_row("ENFORCE_FEEDBACK_SMS",
                 "Post-call YES/NO follow-up SMS (P11)."),
    ]
    table = data_table(
        headers=["Flag", "State", "Description"],
        rows=rows,
    )
    body = card(
        '<p class="muted">Read-only view. Change flags by editing <code>.env</code> and restarting.</p>' +
        table,
        title="Feature flags",
    ) + card(
        '<p class="muted">Usage tracking is always on (data collection only). '
        'The kill switch disables enforcement, not logging.</p>',
        title="Invariant: data collection is always on",
    )
    return HTMLResponse(page(
        title="Feature flags", body=body,
        nav=NAV, active="/admin/flags",
        brand="Receptionist · Ops",
    ))


@router.get("/live", response_class=HTMLResponse)
def live_calls(user=Depends(_check_auth)):
    """V3.14 — list of in-flight calls (from call_timer) with transcript
    counts. Auto-refreshes every 3 seconds via meta refresh."""
    import time as _time
    from src import call_timer
    from src import transcripts as _tr
    snap = call_timer.snapshot()
    rows = []
    for sid, entry in snap.items():
        elapsed = int(_time.time() - entry["start_ts"])
        emergency = entry.get("emergency", False)
        turns = _tr.get_transcript(sid)
        last_text = ""
        if turns:
            last = turns[-1]
            last_text = (last.get("text") or "")[:80]
            if len(last.get("text") or "") > 80:
                last_text += "…"
        detail = (
            f'<a href="/admin/call/{html.escape(sid)}?live=1">watch</a>'
        )
        rows.append([
            (f'<code class="mono">{html.escape(sid[:12])}…</code>', "mono"),
            html.escape(entry.get("client_id") or "—"),
            (f'{elapsed}s', "num"),
            "🚨" if emergency else "",
            (f'{len(turns)}', "num"),
            (html.escape(last_text) or "(no speech yet)", "muted"),
            detail,
        ])

    subtitle = (f"{len(rows)} in-flight call(s) — page auto-refreshes every 3 seconds."
                if rows else "No calls in flight right now.")
    table = data_table(
        headers=["Call SID", "Client", ("Elapsed", "num"), "Flag",
                 ("Turns", "num"), "Latest caller line", ""],
        rows=rows,
        empty_text="Nothing live.",
    )
    body = card(table, title="In-flight calls", subtitle=subtitle)
    # Embed meta-refresh so the page reloads itself
    extra = '<meta http-equiv="refresh" content="3">'
    rendered = page(
        title="Live calls", body=body,
        nav=NAV, active="/admin/live",
        brand="Receptionist · Ops",
        footer_note="auto-refresh: 3s",
    )
    # Inject the meta into <head>
    rendered = rendered.replace("<head>\n", f"<head>\n{extra}\n", 1)
    return HTMLResponse(rendered)


@router.get("/agency/{agency_id}", response_class=HTMLResponse)
def agency_view(agency_id: str, user=Depends(_check_auth)):
    """V3.9 — aggregate overview for one agency's clients only."""
    from src import agency as _agency
    a = _agency.get_agency(agency_id)
    if a is None:
        raise HTTPException(404, "agency not found")

    owned_ids = set(_agency.clients_for_agency(agency_id))
    month = _now_month()
    all_active = _active_clients()
    owned_active = [c for c in all_active if c["id"] in owned_ids]

    totals = {"calls": 0, "minutes": 0.0, "emergencies": 0,
              "cost": 0.0, "revenue": 0.0}
    rows = []
    for c in owned_active:
        m = usage.margin_for(c, month=month)
        totals["calls"] += m["total_calls"]
        totals["minutes"] += m["total_minutes"]
        totals["emergencies"] += m["emergencies"]
        totals["cost"] += m["platform_cost_usd"]
        totals["revenue"] += m["revenue_usd"]
        rows.append(_margin_row(m))

    margin = totals["revenue"] - totals["cost"]
    margin_pct = int((margin / totals["revenue"] * 100)) if totals["revenue"] else 0
    top_stats = stats([
        stat_card("Owned clients", len(owned_active)),
        stat_card("Calls this month", f'{totals["calls"]:,}'),
        stat_card("Emergencies", totals["emergencies"]),
        stat_card("Revenue", f'${totals["revenue"]:.0f}',
                  delta=f"{margin_pct}% margin",
                  direction=("up" if margin_pct >= 50 else
                             "flat" if margin_pct > 0 else "down")),
    ])

    table = data_table(
        headers=[
            "Client",
            ("Calls", "num"), ("Filtered", "num"),
            ("Minutes", "num"), ("In tokens", "num"),
            ("SMS", "num"), ("Cost", "num"),
            ("Revenue", "num"), ("Margin $", "num"),
            "Health",
        ],
        rows=rows,
        empty_text="No active clients owned by this agency yet.",
    )

    body = top_stats + card(
        table,
        title=f"{a.get('name') or agency_id} — client aggregate",
        subtitle=f"Month: {month}"
                 f" · Contact: {html.escape(a.get('contact_email') or '—')}",
    )
    return HTMLResponse(page(
        title=f"{a.get('name') or agency_id}",
        body=body,
        nav=NAV, active="/admin",
        subtitle=f"Agency view · {len(owned_active)} active client(s)",
        brand="Receptionist · Ops",
        footer_note=f"agency={agency_id}",
    ))


@router.get("/bookings", response_class=HTMLResponse)
def bookings_view(client_id: str = "", limit: int = 50,
                  user=Depends(_check_auth)):
    """V3.6 — admin bookings list."""
    from src import bookings as _bk
    rows_raw = _bk.list_bookings(client_id=client_id or None, limit=limit)
    rows = []
    for r in rows_raw:
        created = datetime.fromtimestamp(r["created_ts"], tz=timezone.utc).strftime(
            "%b %d %H:%M UTC")
        call_link = (
            f'<a href="/admin/call/{html.escape(r["call_sid"])}">call</a>'
            if r.get("call_sid") else ""
        )
        status_variant = {"pending": "warn", "confirmed": "good",
                          "completed": "good", "cancelled": "bad"}.get(
            r.get("status") or "pending", "ghost")
        rows.append([
            (html.escape(created), "muted"),
            html.escape(r["client_id"]),
            html.escape(r.get("caller_name") or "—"),
            (html.escape(r.get("caller_phone") or ""), "muted"),
            html.escape(r.get("address") or "—"),
            html.escape(r.get("requested_when") or "—"),
            html.escape(r.get("service") or "—"),
            pill(r.get("status") or "pending", status_variant),
            call_link,
        ])

    header_filter = f' · filter: <code>{html.escape(client_id)}</code>' if client_id else ""
    table = data_table(
        headers=["Created", "Client", "Name", "Phone", "Address",
                 "When", "Service", "Status", ""],
        rows=rows,
        empty_text="No bookings yet. Bookings appear when a Scheduling call completes "
                   "with a committed appointment.",
    )
    body = card(table, title="Bookings",
                subtitle=f"Last {limit}{header_filter}")
    return HTMLResponse(page(
        title="Bookings", body=body,
        nav=NAV, active="/admin/bookings",
        brand="Receptionist · Ops",
    ))


@router.get("/alerts/trigger")
def trigger_digest(user=Depends(_check_auth)):
    """Force a digest to fire right now. Useful after tuning config/alerts.json."""
    result = alerts.send_digest_now()
    return JSONResponse(result)


@router.get("/analytics", response_class=HTMLResponse)
def analytics_view(user=Depends(_check_auth)):
    """P10 — intent distribution + hourly heatmap + MoM trend + flagged list."""
    month = _now_month()
    prev_month = _previous_month(month)
    active_clients = _active_clients()

    # Intent distribution (current month, all clients)
    intent_counts = _intent_counts(month)
    total_intents = sum(intent_counts.values()) or 1
    intent_rows = []
    for intent, n in sorted(intent_counts.items(), key=lambda kv: -kv[1]):
        pct = (n / total_intents) * 100
        intent_rows.append([
            html.escape(intent or "Unknown"),
            (f'{n}', "num"),
            (f'{pct:.1f}%', "num"),
            heatbar(n, max(intent_counts.values() or [1]), width=120),
        ])

    # Calls per hour (UTC)
    hours = _calls_per_hour(month)
    max_h = max(hours.values() or [0]) or 1
    heat_rows = []
    for h in range(24):
        n = hours.get(h, 0)
        heat_rows.append([
            (f'{h:02d}:00', "muted mono"),
            (f'{n}', "num"),
            heatbar(n, max_h, width=220),
        ])

    # MoM per client (with sparkline of last 7 days by call count)
    mom_rows = []
    for c in active_clients:
        cur = usage.margin_for(c, month=month)
        prev = usage.margin_for(c, month=prev_month)
        dcalls = cur["total_calls"] - prev["total_calls"]
        dmargin = cur["margin_usd"] - prev["margin_usd"]
        direction_calls = "up" if dcalls > 0 else ("down" if dcalls < 0 else "flat")
        direction_margin = "up" if dmargin > 0 else ("down" if dmargin < 0 else "flat")
        arrow_c = {"up": "▲", "down": "▼", "flat": "·"}[direction_calls]
        arrow_m = {"up": "▲", "down": "▼", "flat": "·"}[direction_margin]
        mom_rows.append([
            html.escape(c["id"]),
            (f'{cur["total_calls"]} <span class="muted">({arrow_c} {abs(dcalls)})</span>', "num"),
            (f'{cur["total_minutes"]:.0f}', "num"),
            (f'${cur["margin_usd"]:.0f} <span class="muted">({arrow_m} ${abs(dmargin):.0f})</span>', "num"),
            (f'{cur["margin_pct"]}%', "num"),
            sparkline(_calls_last_7_days(c["id"])),
        ])

    # Flagged
    flagged = _flagged_clients(active_clients, month)
    flag_rows = [
        [html.escape(f["client_id"]),
         " · ".join(pill(r, "warn") for r in f["reasons"])]
        for f in flagged
    ]

    body = (
        card(
            data_table(
                headers=["Intent", ("Count", "num"), ("Share", "num"), "Distribution"],
                rows=intent_rows,
                empty_text="No LLM turns logged yet.",
            ),
            title="Intent distribution",
            subtitle=f"Current month: {month}",
        ) +
        card(
            data_table(
                headers=["Hour (UTC)", ("Calls", "num"), "Heatmap"],
                rows=heat_rows,
            ),
            title="Calls per hour of day",
            subtitle="Identifies your actual peak hours",
        ) +
        card(
            data_table(
                headers=["Client", ("Calls (Δ)", "num"), ("Min", "num"),
                         ("Margin $ (Δ)", "num"), ("Margin %", "num"),
                         "7-day trend"],
                rows=mom_rows,
                empty_text="No active clients.",
            ),
            title="Month-over-month per client",
            subtitle=f"Compared against {prev_month}",
        ) +
        card(
            data_table(
                headers=["Client", "Reasons"],
                rows=flag_rows,
                empty_text="No flagged clients.",
            ),
            title="Flagged clients",
            subtitle="Margin < 50%, spam > 20%, or silence-timeout > 10%",
        )
    )
    return HTMLResponse(page(
        title="Analytics",
        body=body,
        nav=NAV, active="/admin/analytics",
        brand="Receptionist · Ops",
    ))


@router.get("/evals", response_class=HTMLResponse)
def evals_view(user=Depends(_check_auth)):
    """P7 — last recorded eval run + per-run trend."""
    try:
        from evals import runner as _runner
    except ImportError:
        body = card(
            '<p class="muted">evals/ package not importable.</p>',
            title="Evals",
        )
        return HTMLResponse(page(title="Evals", body=body, nav=NAV,
                                 active="/admin/evals", brand="Receptionist · Ops"))

    summary = _runner.latest_summary()
    history = _runner.load_history()[-10:]

    if summary is None:
        body = card(
            '<div class="empty">'
            '<p>No eval runs recorded yet.</p>'
            '<p>Run one manually:</p>'
            '<pre>python -m evals.runner --save</pre>'
            '</div>',
            title="Evals",
        )
        return HTMLResponse(page(title="Evals", body=body, nav=NAV,
                                 active="/admin/evals", brand="Receptionist · Ops"))

    pass_rate_pct = round(summary["pass_rate"] * 100, 1)
    pass_direction = (
        "up" if pass_rate_pct >= 90
        else "flat" if pass_rate_pct >= 80
        else "down"
    )
    top = stats([
        stat_card("Passed", f'{summary["passed"]}/{summary["total"]}'),
        stat_card("Pass rate", f'{pass_rate_pct}%',
                  delta=("excellent" if pass_rate_pct >= 90
                         else "watch" if pass_rate_pct >= 80
                         else "regression"),
                  direction=pass_direction),
        stat_card("Avg latency", f'{summary.get("avg_latency_ms", 0)}ms'),
        stat_card("Cases", summary["total"]),
    ])

    trend_rows = []
    for h in history:
        ts = datetime.fromtimestamp(h["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        rate = h.get("pass_rate", 0) * 100
        trend_rows.append([
            (html.escape(ts), "muted mono"),
            (f'{h.get("passed", 0)}/{h.get("total", 0)}', "num"),
            (f'{rate:.1f}%', "num"),
            (f'{h.get("avg_latency_ms", 0)}ms', "num muted"),
        ])

    sparkline_values = [h.get("pass_rate", 0) for h in history]

    body = top + card(
        data_table(
            headers=["When (UTC)", ("Passed/Total", "num"),
                     ("Pass rate", "num"), ("Avg latency", "num")],
            rows=trend_rows,
            empty_text="No history yet.",
        ) + f'<p class="muted">Trend: {sparkline(sparkline_values, width=220, height=30)}</p>',
        title="Recent runs",
    ) + card(
        '<pre>python -m evals.runner --save\n'
        'python -m evals.regression_detector  # compares to previous run</pre>',
        title="Refresh",
    )
    return HTMLResponse(page(
        title="Evals", body=body,
        nav=NAV, active="/admin/evals",
        brand="Receptionist · Ops",
    ))


# ── Internal helpers ───────────────────────────────────────────────────

def _intent_counts(month: str) -> dict:
    from src.usage import _connect, _init_schema, _db_lock
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        rows = conn.execute("""
            SELECT intent, COUNT(*) AS n FROM turns
             WHERE month = ? AND intent IS NOT NULL AND intent <> ''
          GROUP BY intent
        """, (month,)).fetchall()
        conn.close()
    return {r["intent"]: int(r["n"] or 0) for r in rows}


def _calls_per_hour(month: str) -> dict:
    from src.usage import _connect, _init_schema, _db_lock
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        rows = conn.execute("""
            SELECT CAST(strftime('%H', start_ts, 'unixepoch') AS INTEGER) AS h,
                   COUNT(*) AS n
              FROM calls
             WHERE month = ?
          GROUP BY h
        """, (month,)).fetchall()
        conn.close()
    return {int(r["h"] or 0): int(r["n"] or 0) for r in rows}


def _calls_last_7_days(client_id: str) -> list:
    """Return per-day call counts for the last 7 UTC days (ascending)."""
    import time
    from src.usage import _connect, _init_schema, _db_lock
    now_ts = int(time.time())
    buckets = [0] * 7
    start_ts = now_ts - (7 * 86400)
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        rows = conn.execute("""
            SELECT start_ts FROM calls
             WHERE client_id=? AND start_ts >= ?
        """, (client_id, start_ts)).fetchall()
        conn.close()
    for r in rows:
        age_days = (now_ts - int(r["start_ts"])) // 86400
        idx = 6 - min(6, max(0, age_days))
        buckets[idx] += 1
    return buckets


def _flagged_clients(active_clients: list, month: str) -> list:
    out = []
    for c in active_clients:
        m = usage.margin_for(c, month=month)
        reasons = []
        if m["revenue_usd"] > 0 and m["margin_pct"] < 50:
            reasons.append(f"margin {m['margin_pct']}%")
        total = m["total_calls"]
        filtered = m["calls_filtered"]
        if total > 0 and (filtered / total) > 0.20:
            reasons.append(f"spam {int(filtered / total * 100)}%")
        sil_rate = _silence_rate(c["id"], month)
        if total > 0 and sil_rate > 0.10:
            reasons.append(f"silence {int(sil_rate * 100)}%")
        if reasons:
            out.append({"client_id": c["id"], "reasons": reasons})
    return out


def _silence_rate(client_id: str, month: str) -> float:
    from src.usage import _connect, _init_schema, _db_lock
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        row = conn.execute("""
            SELECT SUM(CASE WHEN outcome='silence_timeout' THEN 1 ELSE 0 END) AS s,
                   COUNT(*) AS t
              FROM calls WHERE client_id=? AND month=?
        """, (client_id, month)).fetchone()
        conn.close()
    total = int(row["t"] or 0)
    if total == 0:
        return 0.0
    return int(row["s"] or 0) / total
