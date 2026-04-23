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

CLI:
    python -m src.client_portal issue <client_id>

Visual language lives in src.design (accent="client" = teal).
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
from src.design import card, data_table, page, pill, stat_card, stats

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
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    tq = f"?t={html.escape(t)}"
    return [
        ("Overview",  f"/client/{client_id}{tq}"),
        ("Call log",  f"/client/{client_id}/calls{tq}"),
        ("Invoice",   f"/client/{client_id}/invoice/{month}{tq}"),
    ]


# ── Routes ─────────────────────────────────────────────────────────────

@router.get("/{client_id}", response_class=HTMLResponse)
def summary(client_id: str, t: str = "", request: Request = None):
    client = _require(client_id, t)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    s = usage.monthly_summary(client_id)
    plan = client.get("plan") or {}
    included_min = float(plan.get("included_minutes", 0) or 0)
    bookings = _count_bookings(client_id, month)
    last_call = _last_call_row(client_id)

    minutes_line = f"{s['total_minutes']:.1f} / {included_min:.0f}"
    minutes_delta = None
    minutes_direction = "flat"
    if included_min > 0:
        pct = s["total_minutes"] / included_min * 100
        minutes_delta = f"{pct:.0f}% of plan"
        if pct < 60:
            minutes_direction = "up"
        elif pct < 90:
            minutes_direction = "flat"
        else:
            minutes_direction = "down"

    last_call_label = "—"
    last_call_badge = ""
    if last_call:
        last_call_label = datetime.fromtimestamp(
            last_call["start_ts"], tz=timezone.utc,
        ).strftime("%b %d, %H:%M UTC")
        last_call_badge = f' {pill("Emergency", "bad")}' if last_call.get("emergency") else ""

    top_stats = stats([
        stat_card("Calls handled", s["calls_handled"]),
        stat_card("Emergencies routed", s["emergencies"]),
        stat_card("Bookings captured", bookings),
        stat_card("Minutes used", minutes_line,
                  delta=minutes_delta, direction=minutes_direction),
    ])

    summary_rows = [
        ["Last call", f'{html.escape(last_call_label)}{last_call_badge}'],
        ["Calls filtered (spam/silence)",
         f'<span class="muted">{s["calls_filtered"]}</span>'],
        ["Service month", html.escape(month)],
    ]
    about_card = card(
        data_table(headers=["Field", "Value"], rows=summary_rows),
        title="This month at a glance",
    )

    # Quick explainer for new visitors
    hero_body = (
        f'<p class="muted" style="margin:0;">This is {html.escape(client["name"])}\'s '
        f'live activity panel. Bookmark this page — the link stays the same unless '
        f'{html.escape(client.get("owner_name") or "the operator")} rotates it. '
        f'Nothing here updates on a schedule; the numbers are current.</p>'
    )
    hero = card(hero_body)

    body = hero + top_stats + about_card
    return HTMLResponse(page(
        title=client["name"],
        subtitle=f"Activity dashboard · {month}",
        body=body,
        nav=_nav(client_id, t), active=f"/client/{client_id}?t={html.escape(t)}",
        accent="client",
        brand=client["name"],
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
        outcome = _friendly_outcome(r.get("outcome") or "")
        intent = _top_intent_for_call(r["call_sid"])
        variant = "good" if outcome == "Handled" else (
            "bad" if "spam" in outcome.lower() or "fail" in outcome.lower() else
            "warn" if "wrap" in outcome.lower() else "ghost")
        flag = pill("Emergency", "bad") if r.get("emergency") else ""
        detail = (
            f'<a href="/client/{html.escape(client_id)}/call/{html.escape(r["call_sid"])}'
            f'?t={html.escape(t)}">detail</a>'
        ) if r["call_sid"] else ""
        rows.append([
            (html.escape(ts), "muted mono"),
            (f'{r.get("duration_s") or 0}s', "num"),
            pill(outcome, variant),
            (html.escape(intent or "—"), ""),
            flag,
            detail,
        ])

    body = card(
        data_table(
            headers=["When", ("Duration", "num"), "Outcome", "Intent", "", ""],
            rows=rows,
            empty_text="No calls logged yet — the AI hasn't taken any calls for you this month.",
        ),
        title="Recent calls",
        subtitle=f"Last {limit}",
    )
    return HTMLResponse(page(
        title=f"{client['name']} — calls",
        body=body,
        nav=_nav(client_id, t),
        active=f"/client/{client_id}/calls?t={html.escape(t)}",
        accent="client",
        brand=client["name"],
    ))


@router.get("/{client_id}/call/{call_sid}", response_class=HTMLResponse)
def call_detail(client_id: str, call_sid: str, t: str = ""):
    """V4 — client-facing per-call detail (transcript)."""
    client = _require(client_id, t)
    from src import transcripts
    meta = transcripts.get_call_meta(call_sid)
    if not meta or meta.get("client_id") != client_id:
        # Don't leak other tenants' call SIDs
        raise HTTPException(404, "call not found")
    turns = transcripts.get_transcript(call_sid)

    start_iso = datetime.fromtimestamp(meta["start_ts"], tz=timezone.utc).strftime(
        "%b %d, %Y · %H:%M:%S UTC")
    duration = f'{meta.get("duration_s") or 0}s'
    outcome = _friendly_outcome(meta.get("outcome") or "")

    meta_rows = [
        ["When", html.escape(start_iso)],
        ["Duration", (duration, "num")],
        ["Outcome", pill(outcome, "good" if outcome == "Handled" else "ghost")],
        ["Emergency", "🚨 yes" if meta.get("emergency") else "no"],
        ["From", (html.escape(meta.get("from_number") or ""), "mono muted")],
    ]

    if turns:
        conv_html = '<div style="display:flex;flex-direction:column;gap:var(--s-3);">'
        for turn in turns:
            role = turn["role"]
            badge = pill("caller" if role == "user" else "receptionist",
                         "ghost" if role == "user" else "info")
            ts_str = datetime.fromtimestamp(turn["ts"], tz=timezone.utc).strftime("%H:%M:%S")
            conv_html += (
                f'<div style="padding:var(--s-3);'
                f'background:var(--n-50);border-radius:var(--radius-sm);'
                f'border:1px solid var(--border);">'
                f'<div class="row" style="margin-bottom:4px;">'
                f'{badge}'
                f'<span class="muted ml-auto mono" style="font-size:11px;">{ts_str}</span>'
                f'</div>'
                f'<div>{html.escape(turn["text"])}</div>'
                f'</div>'
            )
        conv_html += '</div>'
    else:
        conv_html = ('<div class="empty">No transcript captured for this call.</div>')

    body = (
        card(data_table(headers=["Field", "Value"], rows=meta_rows),
             title="Call summary") +
        card(conv_html, title="Conversation",
             subtitle=f"{len(turns)} turn(s)")
    )
    return HTMLResponse(page(
        title=f"Call detail — {call_sid[:10]}…",
        body=body,
        nav=_nav(client_id, t),
        active=f"/client/{client_id}/calls?t={html.escape(t)}",
        accent="client",
        brand=client["name"],
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
        brand=client["name"],
        footer_note="Print-friendly · Ctrl+P or ⌘P",
    ))


# ── helpers ────────────────────────────────────────────────────────────

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
