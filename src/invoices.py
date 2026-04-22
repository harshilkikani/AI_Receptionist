"""Invoice generation, rendering, and monthly send.

Responsibilities:
  - generate_invoice(client, month) -> canonical invoice dict
  - render_invoice_html(invoice) -> printable HTML
  - render_invoice_csv(invoice) -> flat CSV (one row per line item + total)
  - send_monthly_invoices(month, now) -> dispatches invoices for all active
    clients via the same transport as the daily digest
  - CLI: python -m src.invoices preview|send|send-all

Billable line items:
  - Monthly service plan (plan.monthly_price)
  - Call overage  (max(0, calls - included_calls) × plan.overage_rate_per_call)
  - Minute overage (only if plan.overage_rate_per_minute is configured)
  - SMS overage   (only if plan.included_sms_segments + overage_rate_per_sms)
  - Emergency surcharge (only if plan.emergency_surcharge is configured)

Informational rows (no amount, shown for context):
  - Included calls / minutes
  - Calls handled / minutes used / SMS segments / emergencies

All amounts rounded to 2 decimals in the dict for stable rendering.
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

from src import tenant, usage

log = logging.getLogger("invoices")


# ── Generation ─────────────────────────────────────────────────────────

def _ri(x) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def _rf(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _round(x: float) -> float:
    return round(x + 1e-9, 2)


def _info_line(label: str, qty, unit: str) -> dict:
    return {"label": label, "qty": qty, "unit": unit,
            "unit_price": 0.0, "amount": 0.0, "info_only": True}


def _bill_line(label: str, qty, unit: str, unit_price: float, amount: float) -> dict:
    return {"label": label, "qty": qty, "unit": unit,
            "unit_price": _round(unit_price), "amount": _round(amount),
            "info_only": False}


def generate_invoice(client: dict, month: Optional[str] = None) -> dict:
    """Compute a canonical invoice dict for one client in one month.

    `month` format: 'YYYY-MM'. Defaults to current UTC month.
    """
    if not client or (client.get("id") or "").startswith("_"):
        raise ValueError("cannot invoice reserved or missing client")

    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    plan = client.get("plan") or {}
    summary = usage.monthly_summary(client["id"], month=month)

    # Plan parameters (with sensible zeros for unset fields)
    base = _rf(plan.get("monthly_price"))
    included_calls = _ri(plan.get("included_calls"))
    included_minutes = _ri(plan.get("included_minutes"))
    rate_per_call = _rf(plan.get("overage_rate_per_call"))
    rate_per_minute = _rf(plan.get("overage_rate_per_minute"))
    included_sms = _ri(plan.get("included_sms_segments"))
    rate_per_sms = _rf(plan.get("overage_rate_per_sms"))
    emergency_surcharge = _rf(plan.get("emergency_surcharge"))

    # Actuals from usage DB (handled calls only — filtered/spam don't bill)
    calls_handled = _ri(summary.get("calls_handled"))
    minutes_used = _rf(summary.get("total_minutes"))
    sms_segments = _ri(summary.get("sms_segments"))
    emergencies = _ri(summary.get("emergencies"))

    lines: list = []

    # Plan base
    lines.append(_bill_line(
        "Monthly service plan", 1, "month", base, base,
    ))

    # Informational rows
    if included_calls:
        lines.append(_info_line(
            "Included calls", included_calls, "calls",
        ))
    if included_minutes:
        lines.append(_info_line(
            "Included minutes", included_minutes, "minutes",
        ))
    lines.append(_info_line("Calls handled", calls_handled, "calls"))
    lines.append(_info_line("Minutes used", round(minutes_used, 1), "minutes"))
    if sms_segments or included_sms:
        lines.append(_info_line("SMS segments sent", sms_segments, "segments"))
    if emergencies:
        lines.append(_info_line("Emergencies routed", emergencies, "calls"))

    # Call overage — ONLY if plan has included_calls + overage rate
    if included_calls and rate_per_call:
        overage_calls = max(0, calls_handled - included_calls)
        if overage_calls:
            amt = overage_calls * rate_per_call
            lines.append(_bill_line(
                f"Call overage ({overage_calls} × ${rate_per_call:.2f})",
                overage_calls, "calls", rate_per_call, amt,
            ))

    # Minute overage — ONLY if plan has explicit per-minute overage rate
    if included_minutes and rate_per_minute:
        overage_min = max(0.0, minutes_used - included_minutes)
        if overage_min > 0.1:
            amt = overage_min * rate_per_minute
            lines.append(_bill_line(
                f"Minute overage ({overage_min:.1f} × ${rate_per_minute:.3f})",
                round(overage_min, 1), "minutes", rate_per_minute, amt,
            ))

    # SMS overage
    if included_sms or rate_per_sms:
        overage_sms = max(0, sms_segments - included_sms)
        if overage_sms and rate_per_sms:
            amt = overage_sms * rate_per_sms
            lines.append(_bill_line(
                f"SMS overage ({overage_sms} × ${rate_per_sms:.3f})",
                overage_sms, "segments", rate_per_sms, amt,
            ))

    # Emergency surcharge (per emergency routed)
    if emergency_surcharge and emergencies:
        amt = emergencies * emergency_surcharge
        lines.append(_bill_line(
            f"Emergency surcharge ({emergencies} × ${emergency_surcharge:.2f})",
            emergencies, "calls", emergency_surcharge, amt,
        ))

    subtotal = sum(ln["amount"] for ln in lines if not ln["info_only"])
    total = _round(subtotal)

    return {
        "client_id": client["id"],
        "client_name": client.get("name") or client["id"],
        "owner_name": client.get("owner_name") or "the owner",
        "owner_email": client.get("owner_email"),
        "month": month,
        "plan_tier": (plan.get("tier") or "").lower() or "standard",
        "line_items": lines,
        "subtotal": _round(subtotal),
        "total": total,
        "currency": "USD",
        "generated_ts": int(time.time()),
    }


# ── Rendering ──────────────────────────────────────────────────────────

_INVOICE_CSS = """
.invoice { max-width: 680px; margin: 0 auto; }
.invoice h2 { margin: 0; font-size: 22px; }
.invoice .head { display:flex; justify-content:space-between; align-items:flex-start;
                 margin-bottom: 14px; }
.invoice .tier { font-size:12px; color:#4338ca; background:#eef2ff;
                 padding:3px 10px; border-radius:999px; display:inline-block; }
.invoice table { width:100%; border-collapse:collapse; margin: 10px 0 0; }
.invoice th, .invoice td { padding:9px 10px; border-bottom:1px solid #eee;
                            font-size: 13px; }
.invoice th { text-align:left; background:#fafafa; font-weight:600;
              font-size: 11px; text-transform: uppercase; color:#666; }
.invoice td.num { text-align:right; font-variant-numeric: tabular-nums; }
.invoice .info td { color:#666; }
.invoice .total td { font-weight:700; font-size: 15px; background:#f3f4f6; }
.invoice .muted { color:#888; font-size:12px; }
"""


def render_invoice_html(invoice: dict) -> str:
    """Return an HTML fragment (not a full page). The client portal wraps
    it in the full page template; email send wraps it in a doctype."""
    rows = []
    for ln in invoice["line_items"]:
        css = "info" if ln["info_only"] else ""
        amount_cell = (
            '<td class="num muted">—</td>'
            if ln["info_only"]
            else f'<td class="num">${ln["amount"]:.2f}</td>'
        )
        rows.append(
            f'<tr class="{css}">'
            f'<td>{html.escape(ln["label"])}</td>'
            f'<td class="num muted">{html.escape(str(ln["qty"]))} {html.escape(ln["unit"])}</td>'
            f"{amount_cell}"
            f"</tr>"
        )
    rows.append(
        f'<tr class="total"><td>Total</td><td></td>'
        f'<td class="num">${invoice["total"]:.2f}</td></tr>'
    )

    generated = datetime.fromtimestamp(invoice["generated_ts"], tz=timezone.utc).strftime(
        "%b %d, %Y %H:%M UTC"
    )

    return f"""
<style>{_INVOICE_CSS}</style>
<div class="invoice">
  <div class="head">
    <div>
      <h2>Invoice — {html.escape(invoice["month"])}</h2>
      <div class="muted">Billed to {html.escape(invoice["client_name"])}</div>
    </div>
    <div style="text-align:right">
      <div class="tier">{html.escape(invoice["plan_tier"].title())}</div>
      <div class="muted" style="margin-top:4px">Generated {html.escape(generated)}</div>
    </div>
  </div>

  <table>
    <tr><th>Line</th><th class="num">Quantity</th><th class="num">Amount</th></tr>
    {''.join(rows)}
  </table>

  <p class="muted" style="margin-top:16px">
    Questions or disputes — reply within 14 days of invoice date to the
    email that delivered this bill, or call {html.escape(invoice["owner_name"])}
    directly to have the operator review the line items.
  </p>
</div>
"""


def render_invoice_csv(invoice: dict) -> str:
    """Flat CSV: header + one row per line item + total row."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["client_id", "month", "label", "qty", "unit", "unit_price", "amount"])
    for ln in invoice["line_items"]:
        w.writerow([
            invoice["client_id"], invoice["month"], ln["label"],
            ln["qty"], ln["unit"],
            f'{ln["unit_price"]:.4f}', f'{ln["amount"]:.2f}',
        ])
    w.writerow([
        invoice["client_id"], invoice["month"], "TOTAL", "", "", "",
        f'{invoice["total"]:.2f}',
    ])
    return buf.getvalue()


# ── Monthly dispatch ───────────────────────────────────────────────────

def _cfg() -> dict:
    from src import alerts as _alerts  # share the same config cache
    return _alerts._cfg() or {}


def _previous_month(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.month == 1:
        return f"{now.year - 1:04d}-12"
    return f"{now.year:04d}-{now.month - 1:02d}"


def _invoice_cfg() -> dict:
    cfg = _cfg()
    return cfg.get("monthly_invoice") or {}


def _is_invoice_day_now(now: datetime) -> bool:
    ic = _invoice_cfg()
    if not ic.get("enabled"):
        return False
    if now.day != int(ic.get("send_on_day", 1)):
        return False
    if now.hour != int(ic.get("send_hour_utc", 15)):
        return False
    return True


def _transport_name(cfg: dict, invoice_cfg: dict) -> str:
    t = (invoice_cfg.get("transport") or "same_as_digest").lower()
    if t == "same_as_digest":
        return (cfg.get("transport") or "webhook").lower()
    return t


def _send_email_invoice(invoice: dict, subject: str, html_body: str,
                        csv_body: str) -> bool:
    """Send via SMTP using the alerts.json smtp config. TO is owner_email
    (falls back to alerts.smtp.to if owner_email is missing)."""
    cfg = _cfg()
    smtp = cfg.get("smtp") or {}
    host = smtp.get("host") or ""
    port = int(smtp.get("port") or 587)
    user = smtp.get("user") or ""
    password = os.environ.get("ALERT_SMTP_PASSWORD", "")
    from_addr = smtp.get("from") or user
    to_list = [invoice["owner_email"]] if invoice.get("owner_email") else (smtp.get("to") or [])

    if not (host and user and password and to_list):
        log.info("invoice email suppressed: SMTP config incomplete for %s",
                 invoice["client_id"])
        return False

    import smtplib
    import ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_list)
    msg.attach(MIMEText(html_body, "html"))
    attach = MIMEApplication(csv_body.encode("utf-8"), _subtype="csv")
    attach.add_header(
        "Content-Disposition", "attachment",
        filename=f'invoice_{invoice["client_id"]}_{invoice["month"]}.csv',
    )
    msg.attach(attach)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=30) as s:
            if smtp.get("tls"):
                s.starttls(context=ctx)
            s.login(user, password)
            s.sendmail(from_addr, to_list, msg.as_string())
        log.info("invoice emailed to %d recipients for %s",
                 len(to_list), invoice["client_id"])
        return True
    except Exception as e:
        log.error("invoice email failed for %s: %s", invoice["client_id"], e)
        return False


def _send_webhook_invoice(invoice: dict, html_body: str, csv_body: str) -> bool:
    cfg = _cfg()
    url = (cfg.get("webhook") or {}).get("url") or ""
    if not url:
        log.info("invoice webhook suppressed: no URL configured for %s",
                 invoice["client_id"])
        return False
    headers = (cfg.get("webhook") or {}).get("headers") or {}
    headers = {**headers, "Content-Type": "application/json"}
    body = json.dumps({
        "type": "invoice",
        "client_id": invoice["client_id"],
        "client_name": invoice["client_name"],
        "owner_email": invoice.get("owner_email"),
        "month": invoice["month"],
        "total": invoice["total"],
        "line_items": invoice["line_items"],
        "html": html_body,
        "csv": csv_body,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = 200 <= r.status < 300
            log.info("invoice webhook for %s: status=%d",
                     invoice["client_id"], r.status)
            return ok
    except urllib.error.URLError as e:
        log.error("invoice webhook failed for %s: %s", invoice["client_id"], e)
        return False


def send_invoice(client: dict, month: str) -> dict:
    """Generate + send one invoice. Returns result dict with {sent, transport, reason}."""
    invoice = generate_invoice(client, month)
    html_body = render_invoice_html(invoice)
    csv_body = render_invoice_csv(invoice)
    subject = f"Invoice {invoice['month']} — {invoice['client_name']}"
    transport = _transport_name(_cfg(), _invoice_cfg())
    if transport == "smtp":
        ok = _send_email_invoice(invoice, subject, html_body, csv_body)
        return {"sent": ok, "transport": "smtp",
                "client_id": client["id"], "month": month,
                "total": invoice["total"]}
    ok = _send_webhook_invoice(invoice, html_body, csv_body)
    return {"sent": ok, "transport": "webhook",
            "client_id": client["id"], "month": month,
            "total": invoice["total"]}


def send_monthly_invoices(month: Optional[str] = None,
                          force: bool = False,
                          now: Optional[datetime] = None) -> dict:
    """If today is the configured send day, ship invoices for the previous
    month to every active client. `force=True` skips the date guard (used
    by CLI + admin endpoint)."""
    now = now or datetime.now(timezone.utc)
    invoice_cfg = _invoice_cfg()

    if not force and not _is_invoice_day_now(now):
        return {"sent": 0, "skipped": "not_invoice_day"}

    target_month = month or _previous_month(now)
    results = []
    for client in tenant.list_all():
        cid = client.get("id") or ""
        if cid.startswith("_"):
            continue
        if not (client.get("inbound_number") or ""):
            continue
        # Skip clients with no owner_email on SMTP transport — they can't
        # receive the email. Webhook transport works regardless.
        transport = _transport_name(_cfg(), invoice_cfg)
        if transport == "smtp" and not client.get("owner_email"):
            log.info("skipping monthly invoice for %s: owner_email missing", cid)
            results.append({"client_id": cid, "sent": False,
                            "reason": "owner_email_missing"})
            continue
        results.append(send_invoice(client, target_month))

    sent = sum(1 for r in results if r.get("sent"))
    return {"sent": sent, "attempted": len(results),
            "month": target_month, "results": results}


# ── CLI ────────────────────────────────────────────────────────────────

def _cli(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.invoices")
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("preview", help="print invoice HTML to stdout")
    pv.add_argument("client_id")
    pv.add_argument("month", help="YYYY-MM")

    csvc = sub.add_parser("csv", help="print invoice CSV to stdout")
    csvc.add_argument("client_id")
    csvc.add_argument("month", help="YYYY-MM")

    s = sub.add_parser("send", help="send invoice for one client + month")
    s.add_argument("client_id")
    s.add_argument("month", help="YYYY-MM")

    sa = sub.add_parser("send-all", help="send invoices for ALL active clients")
    sa.add_argument("--month", default=None,
                    help="target month (default: previous month)")

    args = p.parse_args(argv)

    if args.cmd in ("preview", "csv", "send"):
        client = tenant.load_client_by_id(args.client_id)
        if client is None or (client.get("id") or "").startswith("_"):
            print(f"Unknown or reserved client: {args.client_id}", file=sys.stderr)
            return 2

    if args.cmd == "preview":
        invoice = generate_invoice(client, args.month)
        print(render_invoice_html(invoice))
        return 0

    if args.cmd == "csv":
        invoice = generate_invoice(client, args.month)
        print(render_invoice_csv(invoice))
        return 0

    if args.cmd == "send":
        result = send_invoice(client, args.month)
        print(json.dumps(result))
        return 0 if result.get("sent") else 1

    if args.cmd == "send-all":
        result = send_monthly_invoices(month=args.month, force=True)
        print(json.dumps(result, indent=2))
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
