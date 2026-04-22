"""Client-facing portal — /client/{client_id} routes.

Each client gets a signed URL they can bookmark; the token validates with
HMAC-SHA256 of "{client_id}|{issued_ts}" under CLIENT_PORTAL_SECRET.

Design:
  - Tokens never expire. Rotate by changing CLIENT_PORTAL_SECRET.
  - No database of tokens — stateless, verify by recomputing HMAC.
  - If CLIENT_PORTAL_SECRET is empty/unset, ALL tokens are rejected (safe
    default — prevents accidental unauthenticated portal access).
  - No cost/margin fields on the summary — that's operator-internal.
    Invoice view is the client's bill (their prices), different surface.

CLI:
    python -m src.client_portal issue <client_id>
      -> prints the portal URL with a fresh signed token.

Inline HTML + CSS only (no Jinja) — matches src/admin.py style.
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
    """Produce a fresh signed token for the given client ID.
    Raises ValueError if CLIENT_PORTAL_SECRET is unset."""
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
    """Validate the token; return the client config or raise."""
    client = tenant.load_client_by_id(client_id)
    if client is None or (client.get("id") or "").startswith("_"):
        # Don't leak whether the client exists — always 403 on bad auth
        raise HTTPException(status_code=403, detail="invalid token")
    if not verify_token(client_id, t):
        raise HTTPException(status_code=403, detail="invalid token")
    return client


def portal_url(client_id: str, base_url: Optional[str] = None) -> str:
    token = issue_token(client_id)
    base = base_url or os.environ.get("PUBLIC_BASE_URL", "http://localhost:8765")
    return f"{base.rstrip('/')}/client/{client_id}?t={token}"


# ── HTML helpers (kept in sync with admin.py visual style) ─────────────

_CSS = """
body { font: 14px -apple-system, Segoe UI, sans-serif; background:#f6f7f9;
       color:#1a1a1a; margin:0; padding:20px; }
h1,h2 { margin-top:0; }
h1 { font-size: 22px; }
h2 { font-size: 15px; margin-top: 22px; color:#333; }
.card { background:#fff; border-radius:8px; padding:18px 22px; margin-bottom:16px;
        box-shadow:0 1px 3px rgba(0,0,0,.06); }
.kv { display:grid; grid-template-columns: 180px 1fr; row-gap:6px; }
.kv b { color:#555; font-weight:500; }
table { border-collapse:collapse; width:100%; }
th, td { padding:8px 12px; text-align:left; border-bottom:1px solid #eee; font-size:13px; }
th { background:#fafafa; font-weight:600; text-transform:uppercase; font-size:11px;
     color:#666; letter-spacing:.3px; }
.num { text-align:right; font-variant-numeric: tabular-nums; }
.muted { color:#888; }
nav { margin-bottom:14px; }
nav a { margin-right:16px; color:#2563eb; text-decoration:none; font-weight:500; }
nav a:hover { text-decoration: underline; }
.badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px;
         font-weight:600; background:#eef2ff; color:#4338ca; }
.line { display:flex; justify-content:space-between; padding:8px 0;
        border-bottom:1px dashed #eee; }
.line:last-child { border-bottom:none; font-weight:600; font-size:15px; }
@media print { nav { display:none; } body { background:#fff; padding:0; }
               .card { box-shadow:none; border:1px solid #ddd; } }
"""


def _page(title: str, body: str, client_id: str, token: str) -> str:
    nav = (
        f'<nav>'
        f'<a href="/client/{html.escape(client_id)}?t={html.escape(token)}">Overview</a>'
        f'<a href="/client/{html.escape(client_id)}/calls?t={html.escape(token)}">Call log</a>'
        f'<a href="/client/{html.escape(client_id)}/invoice/{datetime.now(timezone.utc).strftime("%Y-%m")}'
        f'?t={html.escape(token)}">Current invoice</a>'
        f'</nav>'
    )
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head><body>
{nav}
<h1>{html.escape(title)}</h1>
{body}
</body></html>"""


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
    last_call_str = (
        datetime.fromtimestamp(last_call["start_ts"], tz=timezone.utc).strftime("%b %d, %H:%M UTC")
        if last_call else "—"
    )

    minutes_line = f"{s['total_minutes']:.1f} / {included_min:.0f}"
    if included_min > 0:
        pct = (s["total_minutes"] / included_min * 100)
        minutes_line += f" <span class='muted'>({pct:.0f}%)</span>"

    body = f"""
<div class="card">
  <h2>{html.escape(client['name'])} <span class="badge">{html.escape(month)}</span></h2>
  <div class="kv">
    <b>Calls handled</b><span>{s['calls_handled']}</span>
    <b>Emergencies routed</b><span>{s['emergencies']}</span>
    <b>Bookings captured</b><span>{bookings}</span>
    <b>Minutes used</b><span>{minutes_line}</span>
    <b>Last call</b><span>{html.escape(last_call_str)}</span>
    <b>Calls filtered (spam/silence)</b><span class="muted">{s['calls_filtered']}</span>
  </div>
</div>
<p class="muted">This dashboard is kept in sync with your service activity in
near real time. Bookmark this URL — it won't change until
{html.escape(client.get('owner_name') or 'the operator')} rotates your access
credentials.</p>
"""
    return HTMLResponse(_page(f"{client['name']} — dashboard", body, client_id, t))


@router.get("/{client_id}/calls", response_class=HTMLResponse)
def call_log(client_id: str, t: str = "", limit: int = 50):
    client = _require(client_id, t)
    rows = usage.recent_calls(client_id=client_id, limit=limit)
    trs = []
    for r in rows:
        ts = datetime.fromtimestamp(r["start_ts"], tz=timezone.utc).strftime("%b %d %H:%M")
        outcome = _friendly_outcome(r.get("outcome") or "")
        intent = _top_intent_for_call(r["call_sid"])
        trs.append(
            f'<tr>'
            f'<td class="muted">{html.escape(ts)}</td>'
            f'<td class="num">{r.get("duration_s") or 0}s</td>'
            f'<td>{html.escape(outcome)}</td>'
            f'<td>{html.escape(intent or "—")}</td>'
            f'<td>{"Emergency" if r.get("emergency") else ""}</td>'
            f'</tr>'
        )
    body = f"""
<div class="card">
<table>
  <tr><th>When</th><th class="num">Duration</th><th>Outcome</th>
      <th>Intent</th><th></th></tr>
  {''.join(trs) if trs else '<tr><td colspan="5" class="muted">No calls logged yet.</td></tr>'}
</table>
</div>
"""
    return HTMLResponse(_page(f"{client['name']} — call log", body, client_id, t))


@router.get("/{client_id}/invoice/{month}", response_class=HTMLResponse)
def invoice_view(client_id: str, month: str, t: str = ""):
    client = _require(client_id, t)
    # P2 adds src.invoices.render_invoice_html; if available, use it.
    try:
        from src import invoices as _inv  # type: ignore
        invoice = _inv.generate_invoice(client, month)
        body = _inv.render_invoice_html(invoice)
        return HTMLResponse(_page(
            f"Invoice {html.escape(month)} — {html.escape(client['name'])}",
            body, client_id, t,
        ))
    except ImportError:
        pass
    # Fallback summary used until invoices module lands
    body = _fallback_invoice_body(client, month)
    return HTMLResponse(_page(
        f"Invoice {html.escape(month)} — {html.escape(client['name'])}",
        body, client_id, t,
    ))


# ── Internal helpers ───────────────────────────────────────────────────

def _fallback_invoice_body(client: dict, month: str) -> str:
    s = usage.monthly_summary(client["id"], month=month)
    plan = client.get("plan") or {}
    monthly = float(plan.get("monthly_price") or 0)
    included = float(plan.get("included_calls") or 0)
    overage_rate = float(plan.get("overage_rate_per_call") or 0)
    overage_calls = max(0, s["calls_handled"] - int(included)) if included else 0
    overage_cost = overage_calls * overage_rate
    total = monthly + overage_cost
    return f"""
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;">
    <div>
      <h2 style="margin:0">Invoice — {html.escape(month)}</h2>
      <p class="muted">Billed to {html.escape(client.get('name') or '')}</p>
    </div>
    <div style="text-align:right">
      <div class="muted">Plan</div>
      <div><b>{html.escape((plan.get('tier') or '').title() or 'Standard')}</b></div>
    </div>
  </div>
  <div style="margin-top:18px">
    <div class="line"><span>Monthly service plan</span><span>${monthly:.2f}</span></div>
    <div class="line"><span>Included calls</span><span>{int(included)}</span></div>
    <div class="line"><span>Calls handled this month</span><span>{s['calls_handled']}</span></div>
    <div class="line"><span>Overage calls ({overage_calls} × ${overage_rate:.2f})</span>
                     <span>${overage_cost:.2f}</span></div>
    <div class="line"><span>Total</span><span>${total:.2f}</span></div>
  </div>
</div>
<p class="muted">Questions? Reply to the email that delivered this invoice,
or call {html.escape(client.get('owner_name') or 'the office')} directly.</p>
"""


def _count_bookings(client_id: str, month: str) -> int:
    """Distinct calls this month that had at least one Scheduling turn."""
    from src.usage import _connect, _init_schema, _db_lock  # type: ignore
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT call_sid) AS n FROM turns
             WHERE client_id=? AND month=? AND intent='Scheduling'
               AND call_sid <> ''
            """,
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
    from src.usage import _connect, _init_schema, _db_lock  # type: ignore
    with _db_lock:
        conn = _connect()
        _init_schema(conn)
        row = conn.execute(
            """
            SELECT intent, COUNT(*) AS n FROM turns
             WHERE call_sid=? AND intent IS NOT NULL AND intent <> ''
             GROUP BY intent ORDER BY n DESC LIMIT 1
            """,
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
