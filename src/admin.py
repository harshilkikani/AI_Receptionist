"""Minimal admin dashboard — per-client usage, margin, recent calls, CSV export.

Mounted under /admin/* by main.py. Optional HTTP Basic auth via env vars
ADMIN_USER / ADMIN_PASS. If either is unset, the admin endpoints are
accessible without auth (suitable for local-only operation).

All routes read-only. No mutations — the admin does NOT expose config
editing. Operator edits YAML/JSON files on disk and restarts.

Kept lightweight:
  - No Jinja templates — plain HTML strings
  - No CSS framework — basic styling inline
  - No external deps — stdlib csv, html
"""

from __future__ import annotations

import csv
import html
import io
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src import tenant, usage, alerts

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBasic(auto_error=False)


def _auth_required() -> bool:
    return bool(os.environ.get("ADMIN_USER")) and bool(os.environ.get("ADMIN_PASS"))


def _check_auth(creds: Optional[HTTPBasicCredentials] = Depends(security)):
    if not _auth_required():
        return None
    if not creds:
        raise HTTPException(status_code=401, detail="Admin auth required",
                            headers={"WWW-Authenticate": "Basic"})
    if (creds.username != os.environ.get("ADMIN_USER")
            or creds.password != os.environ.get("ADMIN_PASS")):
        raise HTTPException(status_code=401, detail="Invalid admin credentials",
                            headers={"WWW-Authenticate": "Basic"})
    return creds.username


def _flag(name: str) -> str:
    """Render current env flag value."""
    return os.environ.get(name, "—") or "—"


# ── HTML helpers ───────────────────────────────────────────────────────

_CSS = """
body { font: 14px -apple-system, Segoe UI, sans-serif; background: #f6f7f9; color: #1a1a1a; margin: 0; padding: 20px; }
h1, h2 { margin-top: 0; }
h1 { font-size: 20px; }
h2 { font-size: 16px; margin-top: 24px; }
table { border-collapse: collapse; width: 100%; background: #fff; border-radius: 6px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.05); }
th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #eee; }
th { background: #fafafa; font-weight: 600; font-size: 12px; text-transform: uppercase; color: #666; }
tr:last-child td { border-bottom: none; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.flag-on { color: #059669; font-weight: 600; }
.flag-off { color: #dc2626; font-weight: 600; }
.warn { background: #fef3c7; }
.bad  { background: #fee2e2; }
.good { background: #ecfdf5; }
.pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.muted { color: #888; }
nav a { margin-right: 16px; color: #2563eb; text-decoration: none; }
nav a:hover { text-decoration: underline; }
"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head><body>
<nav>
  <a href="/admin">Overview</a>
  <a href="/admin/calls">Recent calls</a>
  <a href="/admin/export.csv">Export CSV</a>
  <a href="/admin/flags">Feature flags</a>
</nav>
<h1>{html.escape(title)}</h1>
{body}
</body></html>"""


def _margin_row(margin: dict) -> str:
    css_class = ""
    pct = margin.get("margin_pct", 0)
    if pct < 0:
        css_class = "bad"
    elif pct < 50:
        css_class = "warn"
    else:
        css_class = "good"
    return (
        f'<tr class="{css_class}">'
        f'<td>{html.escape(margin["client_id"])}</td>'
        f'<td class="num">{margin["total_calls"]}</td>'
        f'<td class="num">{margin["calls_filtered"]}</td>'
        f'<td class="num">{margin["total_minutes"]:.1f}</td>'
        f'<td class="num">{margin["llm_input_tokens"]:,}</td>'
        f'<td class="num">{margin["llm_output_tokens"]:,}</td>'
        f'<td class="num">{margin["sms_segments"]}</td>'
        f'<td class="num">${margin["platform_cost_usd"]:.2f}</td>'
        f'<td class="num">${margin["revenue_usd"]:.2f}</td>'
        f'<td class="num">${margin["margin_usd"]:.2f}</td>'
        f'<td class="num">{margin["margin_pct"]}%</td>'
        f"</tr>"
    )


# ── Routes ─────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def overview(user=Depends(_check_auth)):
    rows = []
    active_clients = [
        c for c in tenant.list_all()
        if not (c.get("id") or "").startswith("_")
        and (c.get("inbound_number") or "")  # skip reference configs
    ]
    for c in active_clients:
        m = usage.margin_for(c)
        rows.append(_margin_row(m))

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    body = f"""
<p class="muted">Current month: {month}</p>
<table>
  <tr>
    <th>Client</th><th class="num">Calls</th><th class="num">Filtered</th>
    <th class="num">Minutes</th><th class="num">In tokens</th><th class="num">Out tokens</th>
    <th class="num">SMS</th><th class="num">Cost</th><th class="num">Revenue</th>
    <th class="num">Margin $</th><th class="num">Margin %</th>
  </tr>
  {''.join(rows) if rows else '<tr><td colspan="11" class="muted">No active clients.</td></tr>'}
</table>
"""
    return HTMLResponse(_page("Receptionist admin — margin overview", body))


@router.get("/calls", response_class=HTMLResponse)
def recent_calls(limit: int = 50, client_id: str = "", user=Depends(_check_auth)):
    rows = usage.recent_calls(client_id=client_id or None, limit=limit)
    trs = []
    for r in rows:
        start_iso = datetime.fromtimestamp(r["start_ts"], tz=timezone.utc).isoformat(timespec="seconds")
        outcome = html.escape(r.get("outcome") or "—")
        trs.append(
            f'<tr>'
            f'<td class="muted">{start_iso}</td>'
            f'<td>{html.escape(r["client_id"])}</td>'
            f'<td class="muted">{html.escape(r.get("from_number") or "")}</td>'
            f'<td class="num">{r.get("duration_s") or 0}s</td>'
            f'<td>{outcome}</td>'
            f'<td>{"🚨" if r.get("emergency") else ""}</td>'
            f'</tr>'
        )
    client_filter = f' for <b>{html.escape(client_id)}</b>' if client_id else ""
    body = f"""
<p class="muted">Last {limit} calls{client_filter}</p>
<table>
  <tr>
    <th>Start (UTC)</th><th>Client</th><th>From</th>
    <th class="num">Duration</th><th>Outcome</th><th>Emergency</th>
  </tr>
  {''.join(trs) if trs else '<tr><td colspan="6" class="muted">No calls logged yet.</td></tr>'}
</table>
"""
    return HTMLResponse(_page("Recent calls", body))


@router.get("/export.csv")
def export_csv(month: Optional[str] = None, user=Depends(_check_auth)):
    """CSV export of per-client monthly metrics — for overage billing."""
    active_clients = [
        c for c in tenant.list_all()
        if not (c.get("id") or "").startswith("_")
        and (c.get("inbound_number") or "")  # skip reference configs
    ]
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
    filename = f"usage_{month or datetime.now(timezone.utc).strftime('%Y-%m')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/flags", response_class=HTMLResponse)
def flags_view(user=Depends(_check_auth)):
    def row(name: str, desc: str):
        val = os.environ.get(name)
        if val is None:
            return f"<tr><td><code>{name}</code></td><td class='muted'>(unset — using default)</td><td>{html.escape(desc)}</td></tr>"
        cls = "flag-on" if val.lower() in ("true", "1", "yes") else "flag-off"
        return f"<tr><td><code>{name}</code></td><td class='{cls}'>{html.escape(val)}</td><td>{html.escape(desc)}</td></tr>"

    body = f"""
<p class="muted">Read-only view. Change flags by editing <code>.env</code> and restarting the server.</p>
<table>
  <tr><th>Flag</th><th>Current</th><th>Description</th></tr>
  {row("MARGIN_PROTECTION_ENABLED", "Global kill switch. When false, ALL enforcement is bypassed.")}
  {row("ENFORCE_CALL_DURATION_CAP", "Hard cap call duration at 240s (360s for emergencies).")}
  {row("ENFORCE_SPAM_FILTER", "Reject known spam numbers and phrase-detected calls.")}
  {row("ENFORCE_SMS_CAP", "Cap outbound SMS at plan.sms_max_per_call (default 3).")}
  {row("ENFORCE_USAGE_ALERTS", "Send daily digest email/webhook when thresholds crossed.")}
</table>
<h2>Non-enforcement</h2>
<p class="muted">Usage tracking is always on (data collection only). The kill switch does not disable tracking — only enforcement actions.</p>
"""
    return HTMLResponse(_page("Feature flags", body))


@router.get("/alerts/trigger")
def trigger_digest(user=Depends(_check_auth)):
    """Force a digest to fire right now. Useful after tuning config/alerts.json."""
    result = alerts.send_digest_now()
    return JSONResponse(result)


@router.get("/evals", response_class=HTMLResponse)
def evals_view(user=Depends(_check_auth)):
    """P7 — last recorded eval run + per-case results."""
    try:
        from evals import runner as _runner
    except ImportError:
        return HTMLResponse(_page(
            "Evals",
            "<p class='muted'>evals/ package not importable.</p>",
        ))

    summary = _runner.latest_summary()
    history = _runner.load_history()[-10:]  # last 10 runs for a trend

    if summary is None:
        body = """
<div class="muted">
  <p>No eval runs recorded yet.</p>
  <p>Run one manually:</p>
  <pre>python -m evals.runner --save</pre>
</div>"""
        return HTMLResponse(_page("Evals", body))

    # Re-run to get per-case results for the current view (summary from
    # history is slim). If this is too slow, cache it in a JSON file next
    # to eval_history.jsonl. For 20 cases against a real LLM, this is
    # ~15-30 seconds — the page lives behind admin auth + rate limits, so
    # acceptable. Gate this with a query param to avoid surprise.
    trend_rows = []
    for h in history:
        ts = datetime.fromtimestamp(h["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        trend_rows.append(
            f'<tr><td class="muted">{ts}</td>'
            f'<td class="num">{h.get("passed", 0)}/{h.get("total", 0)}</td>'
            f'<td class="num">{h.get("pass_rate", 0)*100:.1f}%</td>'
            f'<td class="num">{h.get("avg_latency_ms", 0)}ms</td></tr>'
        )

    ts = datetime.fromtimestamp(summary["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = f"""
<p class="muted">Last run: {html.escape(ts)}</p>
<table>
  <tr><th>Passed</th><th>Total</th><th>Pass rate</th><th>Avg latency</th></tr>
  <tr class="good">
    <td class="num">{summary['passed']}</td>
    <td class="num">{summary['total']}</td>
    <td class="num">{summary['pass_rate']*100:.1f}%</td>
    <td class="num">{summary.get('avg_latency_ms', 0)}ms</td>
  </tr>
</table>

<h2>Recent runs</h2>
<table>
  <tr><th>When (UTC)</th><th class="num">Passed/Total</th>
      <th class="num">Pass rate</th><th class="num">Avg latency</th></tr>
  {''.join(trend_rows) if trend_rows else ''}
</table>

<h2>How to refresh</h2>
<pre>python -m evals.runner --save
python -m evals.regression_detector  # compares to previous run</pre>
"""
    return HTMLResponse(_page("Evals", body))
