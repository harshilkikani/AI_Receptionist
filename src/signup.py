"""V3.12 — Self-serve /signup flow for demo tenants.

Public form that lets a prospect type their company name + email and
get a 24h disposable demo tenant + a signed portal URL back. Rate-
limited (5/hour per IP by default; shared limiter state with admin).

Flow:
  GET  /signup     — render the form
  POST /signup     — validate, create demo tenant, show success page
                     with the portal URL

The tenant is created via onboarding._build_demo() + _write_yaml() so
it has the same 24h auto-purge semantics as `python -m src.onboarding
new-demo`. Startup lifespan already purges expired demos.

Env:
  ENFORCE_PUBLIC_SIGNUP   default 'true'. Flip to 'false' to disable
                          the form without removing the code path.
  SIGNUP_RATE_LIMIT_PER_HOUR  default '5'
"""
from __future__ import annotations

import html
import logging
import os
import re
import threading
import time
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from src import onboarding

log = logging.getLogger("signup")

router = APIRouter(tags=["signup"])

_rate_lock = threading.Lock()
_rate_buckets: dict = {}  # ip -> [timestamps_within_last_hour]


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _enforcement_active() -> bool:
    return os.environ.get("ENFORCE_PUBLIC_SIGNUP", "true").lower() == "true"


def _limit_per_hour() -> int:
    try:
        return max(1, int(os.environ.get("SIGNUP_RATE_LIMIT_PER_HOUR", "5")))
    except ValueError:
        return 5


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _check_rate_limit(ip: str) -> bool:
    """Return True if the IP is under its hourly quota.

    V5.1 — prunes empty buckets (IPs whose hits all aged out) so the
    dict stays bounded over time. Also caps total dict size at 5000
    entries; on overflow, oldest-by-most-recent-hit gets evicted."""
    now = time.time()
    cutoff = now - 3600
    limit = _limit_per_hour()
    with _rate_lock:
        bucket = [t for t in _rate_buckets.get(ip, []) if t >= cutoff]
        if len(bucket) >= limit:
            _rate_buckets[ip] = bucket
            return False
        bucket.append(now)
        _rate_buckets[ip] = bucket
        # Periodic prune: drop empty buckets every ~50 calls (keep cheap)
        if len(_rate_buckets) > 100 and (int(now) % 50) == 0:
            for stale_ip in [
                k for k, v in _rate_buckets.items() if not v
            ]:
                _rate_buckets.pop(stale_ip, None)
        # Hard cap: 5000 active IPs in the last hour is well past abuse
        if len(_rate_buckets) > 5000:
            oldest_ip = min(_rate_buckets.items(),
                            key=lambda kv: max(kv[1]) if kv[1] else 0)[0]
            _rate_buckets.pop(oldest_ip, None)
        return True


def _reset_rate_limits():
    """Test hook."""
    with _rate_lock:
        _rate_buckets.clear()


# ── HTML surfaces ─────────────────────────────────────────────────────

_STYLE = """
body { font: 15px ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
       background:#f8fafc; color:#0f172a; margin:0; padding: 48px 24px; }
.card { max-width: 560px; margin: 0 auto; background: #fff; padding: 32px;
        border-radius: 12px; box-shadow: 0 2px 8px rgba(15,23,42,.06);
        border: 1px solid #e2e8f0; }
h1 { font-size: 26px; font-weight: 800; letter-spacing: -0.02em; margin: 0 0 8px; }
.sub { color:#64748b; margin: 0 0 24px; }
label { display: block; margin-bottom: 14px; font-weight: 500; }
label span { display:block; font-size:13px; color:#475569; margin-bottom: 4px; }
input, select { width: 100%; padding: 10px 12px; border: 1px solid #cbd5e1;
                border-radius: 8px; font: inherit; box-sizing: border-box; }
input:focus, select:focus { outline: 2px solid #7c3aed; outline-offset: -1px;
                             border-color: transparent; }
button { background: #7c3aed; color: white; border: 0; padding: 11px 22px;
         border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 15px; }
button:hover { background: #6d28d9; }
.muted { color:#64748b; font-size: 13px; margin-top: 16px; }
.ok { padding: 12px 16px; border-radius: 8px; background:#ecfdf5;
      border: 1px solid #a7f3d0; color:#065f46; margin-bottom: 16px; }
.err { padding: 12px 16px; border-radius: 8px; background:#fef2f2;
       border: 1px solid #fecaca; color:#991b1b; margin-bottom: 16px; }
code { background:#f1f5f9; padding: 2px 6px; border-radius: 4px;
       font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
.url { word-break: break-all; }
a { color: #7c3aed; }
"""


def _form_page(error: Optional[str] = None) -> str:
    err_html = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Try the AI receptionist — free demo</title>
<style>{_STYLE}</style>
</head><body>
<div class="card">
  <h1>Try it free for 24 hours</h1>
  <p class="sub">Spin up a disposable receptionist trained on your
  business in under 60 seconds. No credit card, no install.</p>
  {err_html}
  <form method="POST" action="/signup">
    <label>
      <span>Your business name</span>
      <input name="company_name" required maxlength="80"
             placeholder="Acme Plumbing">
    </label>
    <label>
      <span>What do you do?</span>
      <input name="services" required maxlength="120"
             placeholder="Plumbing, drain cleaning, water heaters">
    </label>
    <label>
      <span>Owner email (where we send the portal link)</span>
      <input name="owner_email" type="email" required maxlength="120"
             placeholder="you@business.com">
    </label>
    <button type="submit">Start my demo →</button>
    <p class="muted">Demo expires in 24 hours. Your data is isolated
    from other tenants. We delete everything at the end of the window.</p>
  </form>
</div>
</body></html>"""


def _success_page(portal_url: str, client_id: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Your demo is ready</title>
<style>{_STYLE}</style>
</head><body>
<div class="card">
  <h1>Your demo is ready 👋</h1>
  <div class="ok">Tenant <code>{html.escape(client_id)}</code> created.
  Expires in 24 hours.</div>
  <p>Bookmark this URL — it's your private dashboard:</p>
  <p class="url"><a href="{html.escape(portal_url)}" target="_blank"
     rel="noopener"><code>{html.escape(portal_url)}</code></a></p>
  <p class="muted">Call log, minutes used, and invoice live here.
  No app, no login.</p>
  <p class="muted">Want to tune the voice to YOUR business — pricing,
  service area, hours, emergency keywords? Reply to the email we sent you
  with the details and we'll push them in.</p>
</div>
</body></html>"""


# ── routes ─────────────────────────────────────────────────────────────

@router.get("/signup", response_class=HTMLResponse)
def signup_form():
    if not _enforcement_active():
        raise HTTPException(404, "not found")
    return HTMLResponse(_form_page())


@router.post("/signup", response_class=HTMLResponse)
def signup_submit(request: Request,
                  company_name: str = Form(...),
                  services: str = Form(...),
                  owner_email: str = Form(...)):
    if not _enforcement_active():
        raise HTTPException(404, "not found")

    ip = _client_ip(request)
    if not _check_rate_limit(ip):
        return HTMLResponse(
            _form_page(error=(
                "Too many signups from this network in the last hour. "
                "Try again later.")),
            status_code=429,
        )

    # Validate
    cn = (company_name or "").strip()
    sv = (services or "").strip()
    em = (owner_email or "").strip()
    if not cn or len(cn) > 80:
        return HTMLResponse(
            _form_page(error="Company name looks off."), status_code=400)
    if not sv or len(sv) > 120:
        return HTMLResponse(
            _form_page(error="Services description looks off."),
            status_code=400)
    if not _EMAIL_RE.match(em):
        return HTMLResponse(
            _form_page(error="That email doesn't look valid."),
            status_code=400)

    # Build a disposable demo tenant
    try:
        config = onboarding._build_demo()
    except Exception as e:
        log.error("signup: _build_demo failed: %s", e)
        raise HTTPException(500, "demo creation failed")
    # Customize with the prospect's fields
    config["name"] = cn
    config["services"] = sv
    config["owner_email"] = em
    config["owner_name"] = cn.split()[0] if cn.split() else "the owner"

    try:
        onboarding._write_yaml(config)
    except Exception as e:
        log.error("signup: _write_yaml failed: %s", e)
        raise HTTPException(500, "demo creation failed")

    # Mint a portal URL. If CLIENT_PORTAL_SECRET is unset, skip silently
    # and show a fallback message.
    from src import client_portal
    if client_portal._secret():
        try:
            url = client_portal.portal_url(config["id"])
        except Exception as e:
            log.error("signup: portal_url failed: %s", e)
            url = "(Portal secret not configured — contact support.)"
    else:
        url = "(Portal secret not configured — contact support.)"

    log.info("signup: new demo tenant %s from %s (%s)",
             config["id"], ip, em)
    return HTMLResponse(_success_page(url, config["id"]))
