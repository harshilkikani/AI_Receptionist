# Invoicing

_Last updated: 2026-04-21 (commit db3207e)_

Monthly invoices are generated automatically by `src/invoices.py` and
sent via the same transport the daily digest uses. Until
`config/alerts.json::monthly_invoice.enabled` is flipped to `true`,
nothing auto-sends — the CLI always works for manual sends.

## Invoice formula

Every invoice starts with the plan base from `client.plan.monthly_price`
and adds billable overage rows only where the plan defines both an
included allowance and a rate. Informational rows (calls handled,
minutes used, SMS sent, emergencies routed) appear for context but don't
contribute to the total.

```
total = monthly_price
      + max(0, calls_handled - included_calls) × overage_rate_per_call
      + max(0, minutes_used  - included_minutes) × overage_rate_per_minute   [optional]
      + max(0, sms_segments  - included_sms_segments) × overage_rate_per_sms [optional]
      + emergencies × emergency_surcharge                                    [optional]
```

- `calls_handled` excludes spam/silence-filtered calls. Only calls the AI
  actually took count toward overage.
- `minutes_used` comes from `usage.monthly_summary.total_minutes` and
  excludes calls force-terminated before connect.
- The "optional" rows appear only if the plan YAML defines both the
  allowance AND the rate. By default none of them are set.

## Sending an invoice manually

```bash
# preview HTML body (piped to a file, opened in a browser)
python -m src.invoices preview ace_hvac 2026-04 > /tmp/inv.html

# CSV for spreadsheet paste
python -m src.invoices csv ace_hvac 2026-04

# send one invoice via the configured transport (SMTP / webhook)
python -m src.invoices send ace_hvac 2026-04

# force send for every active client (ignores day/hour gate)
python -m src.invoices send-all --month 2026-04
```

Exit code 0 = success, 1 = transport reported failure, 2 = client ID
unknown.

## Automating the monthly dispatch

1. Set `config/alerts.json::monthly_invoice.enabled` to `true`.
2. Pick a day and hour (UTC). Default: day 1 at 15 UTC (10 AM ET).
3. Decide transport:
   - `same_as_digest` (default) — inherits `transport` from the top-level
     `alerts.json`. Easiest.
   - `smtp` — overrides. Requires `smtp.host/port/user/tls` configured
     and `ALERT_SMTP_PASSWORD` in `.env`.
   - `webhook` — overrides. Requires `webhook.url`.
4. Ensure every active client has `owner_email` set in their YAML
   (SMTP only; webhook transport works without it).

The daily digest loop in `src/alerts.py` checks the day+hour match each
time it fires, so no second scheduler is needed.

## What the client sees

The HTML body renders inline in the email client. Columns: **Line**,
**Quantity**, **Amount**. Info rows display the quantity but a `—` for
amount so the client can see what usage was attributed to them without
being confused by the billable subset.

A CSV attachment (`invoice_{client_id}_{month}.csv`) ships with every
email for accounting-system import.

The client can also view this same invoice any time at
`/client/{client_id}/invoice/{YYYY-MM}?t=<token>` (see
`docs/CLIENT_PORTAL.md`).

## Dispute flow

1. Client replies to the invoice email (or calls).
2. Operator runs `python -m src.invoices preview <client> <month>` and
   compares against the admin CSV export (`/admin/export.csv?month=...`).
3. If an adjustment is needed:
   - Fix the root cause (e.g., mis-tagged spam call → re-tag in
     `data/usage.db` directly, or adjust the plan field in the YAML).
   - Re-send the corrected invoice with
     `python -m src.invoices send <client> <month>`.
4. Keep a record of the original + corrected totals in your accounting
   system. The application does not version invoices; the re-sent one is
   canonical.

## Re-sending an invoice

```bash
# Same command as the initial send. Idempotent from this module's
# perspective — duplicates are the delivery mechanism's problem (SMTP
# will send twice, webhook will POST twice). Operator discretion.
python -m src.invoices send ace_hvac 2026-04
```

## What's intentionally NOT here

- No PDF rendering. The spec preferred HTML + print; browsers handle
  print-to-PDF cleanly on any OS.
- No tax line. If you need sales tax, add a `tax_rate` to `plan` and
  extend `generate_invoice` to add a tax row. Flag it in `CHANGES.md`
  when you do — don't let it be a silent number change.
- No recurring-credit / prorated-refund semantics. Issue manual credits
  by adjusting the next month's invoice total and noting the reason in
  your accounting record.

## Related files

- `src/invoices.py` — generation + rendering + dispatch
- `src/alerts.py::_maybe_send_monthly_invoices` — scheduled hook
- `config/alerts.json::monthly_invoice` — schedule + transport
- `clients/<id>.yaml::owner_email` — SMTP recipient
- `tests/test_invoices.py` — formula coverage
