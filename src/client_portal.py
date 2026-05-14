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
    card, data_table, page, pill, stat_card, stats, status_pill, icon,
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
    tq = f"?t={html.escape(t)}"
    return [
        ("Today",         f"/client/{client_id}{tq}"),
        ("Recent calls",  f"/client/{client_id}/calls{tq}"),
        ("Follow-ups",    f"/client/{client_id}/followups{tq}"),
        ("Settings",      f"/client/{client_id}/settings{tq}"),
    ]


# ── Routes ─────────────────────────────────────────────────────────────

@router.get("/{client_id}", response_class=HTMLResponse)
def summary(client_id: str, t: str = "", request: Request = None):
    client = _require(client_id, t)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    s = usage.monthly_summary(client_id)
    bookings = _count_bookings(client_id, month)
    last_call = _last_call_row(client_id)

    last_call_label = "No calls yet"
    last_call_badge = ""
    if last_call:
        last_call_label = datetime.fromtimestamp(
            last_call["start_ts"], tz=timezone.utc,
        ).strftime("%b %d, %H:%M")
        if last_call.get("emergency"):
            last_call_badge = " " + status_pill("emergency")

    top_stats = stats([
        stat_card("Calls answered", s["calls_handled"]),
        stat_card("Emergencies routed", s["emergencies"]),
        stat_card("Bookings captured", bookings),
    ])

    month_label = datetime.now(timezone.utc).strftime("%B %Y")
    summary_rows = [
        ["Last call", f'{html.escape(last_call_label)}{last_call_badge}'],
        ["This month so far", html.escape(month_label)],
    ]
    about_card = card(
        data_table(headers=["", ""], rows=summary_rows),
        title="At a glance",
    )

    tq = f"?t={html.escape(t)}"
    invoice_link = (
        f'<a href="/client/{html.escape(client_id)}/invoice/{html.escape(month)}{tq}" '
        f'class="btn" style="margin-top:var(--s-3)">View this month\'s invoice →</a>'
    )

    hero_body = (
        f'<p class="muted" style="margin:0;">Live activity for '
        f'{html.escape(client["name"])}. Bookmark this page — the link stays '
        f'the same. Numbers update as calls come in.</p>'
        f'{invoice_link}'
    )
    hero = card(hero_body)

    body = hero + top_stats + about_card
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


@router.get("/{client_id}/calls", response_class=HTMLResponse)
def call_log(client_id: str, t: str = "", limit: int = 50):
    client = _require(client_id, t)
    rows_raw = usage.recent_calls(client_id=client_id, limit=limit)
    rows = []
    for r in rows_raw:
        ts = datetime.fromtimestamp(r["start_ts"], tz=timezone.utc).strftime(
            "%b %d · %H:%M")
        # V9.0 — status_pill handles vocabulary mapping (no engineer
        # strings leak through). Emergency is shown via the status pill
        # when the underlying call was flagged.
        raw = (r.get("outcome") or "").strip().lower()
        if r.get("emergency"):
            status_html = status_pill("emergency")
        else:
            status_html = status_pill(raw or "answered")

        detail = (
            f'<a href="/client/{html.escape(client_id)}/call/{html.escape(r["call_sid"])}'
            f'?t={html.escape(t)}">View →</a>'
        ) if r["call_sid"] else ""

        from_n = r.get("from_number") or ""
        rows.append([
            (html.escape(ts), "muted"),
            html.escape(from_n) if from_n else "",
            (_fmt_duration(r.get("duration_s") or 0), "num muted"),
            status_html,
            detail,
        ])

    body = card(
        data_table(
            headers=["When", "From", ("Duration", "num"), "Status", ""],
            rows=rows,
            empty_text="No calls yet this month. Your line is ready when one comes in.",
        ),
        title="Recent calls",
        subtitle=f"Last {limit}",
    )
    return HTMLResponse(page(
        title=client["name"],
        subtitle="Recent calls",
        body=body,
        nav=_nav(client_id, t),
        active=f"/client/{client_id}/calls?t={html.escape(t)}",
        accent="client",
        brand=(client.get("brand_display_name") or client["name"]),
        brand_logo_url=client.get("brand_logo_url") or None,
        custom_accent_hex=client.get("brand_accent_color") or None,
    ))


@router.get("/{client_id}/call/{call_sid}", response_class=HTMLResponse)
def call_detail(client_id: str, call_sid: str, t: str = ""):
    """Per-call detail (transcript). Customer-facing — no internal
    identifiers, no engineer-y outcome strings."""
    client = _require(client_id, t)
    from src import transcripts
    meta = transcripts.get_call_meta(call_sid)
    if not meta or meta.get("client_id") != client_id:
        # Don't leak other tenants' call SIDs
        raise HTTPException(404, "call not found")
    turns = transcripts.get_transcript(call_sid)

    start_iso = datetime.fromtimestamp(meta["start_ts"], tz=timezone.utc).strftime(
        "%b %d, %Y · %H:%M:%S")
    duration = _fmt_duration(meta.get("duration_s") or 0)
    raw_outcome = (meta.get("outcome") or "").strip().lower()
    status_html = (status_pill("emergency") if meta.get("emergency")
                   else status_pill(raw_outcome or "answered"))

    meta_rows = [
        ["When", html.escape(start_iso)],
        ["Duration", (duration, "num")],
        ["Status", status_html],
        ["From", (html.escape(meta.get("from_number") or ""), "muted")],
    ]
    # Show summary if available — soften "AI summary" → "Call summary".
    summary_text = (meta.get("summary") if hasattr(meta, "get") else None)
    if summary_text:
        meta_rows.append(["Summary",
                          f'<span style="font-style:italic">{html.escape(summary_text)}</span>'])

    if turns:
        conv_html = '<div style="display:flex;flex-direction:column;gap:var(--s-3);">'
        for turn in turns:
            role = turn["role"]
            badge = pill("Caller" if role == "user" else "Receptionist",
                         "ghost" if role == "user" else "info")
            ts_str = datetime.fromtimestamp(turn["ts"], tz=timezone.utc).strftime("%H:%M:%S")
            conv_html += (
                f'<div style="padding:var(--s-3);'
                f'background:var(--n-50);border-radius:var(--radius-sm);'
                f'border:1px solid var(--border);">'
                f'<div class="row" style="margin-bottom:4px;">'
                f'{badge}'
                f'<span class="muted ml-auto" style="font-size:11px;">{ts_str}</span>'
                f'</div>'
                f'<div>{html.escape(turn["text"])}</div>'
                f'</div>'
            )
        conv_html += '</div>'
    else:
        conv_html = ('<div class="empty">No transcript captured for this call.</div>')

    body = (
        card(data_table(headers=["", ""], rows=meta_rows),
             title="Call summary") +
        card(conv_html, title="Conversation")
    )
    return HTMLResponse(page(
        title=client["name"],
        subtitle="Call detail",
        body=body,
        nav=_nav(client_id, t),
        active=f"/client/{client_id}/calls?t={html.escape(t)}",
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
    services_html = (
        card(f'<p style="margin:0">{html.escape(services)}</p>',
             title="What the receptionist knows about your business")
        if services else ""
    )
    contact_note = card(
        '<p style="margin:0;color:var(--muted)">'
        'To change any of these, reply to your welcome email or call your '
        'account contact. Changes typically apply within a few minutes.'
        '</p>',
    )
    body = (
        card(data_table(headers=["", ""], rows=rows),
             title="Account settings")
        + services_html
        + contact_note
    )
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
