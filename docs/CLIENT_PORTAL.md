# Client Portal

_Last updated: 2026-04-21 (commit 0e09629)_

Each tenant gets one stable, bookmarkable URL that shows THEIR activity —
no one else's. URLs are signed with HMAC-SHA256 so nothing needs to be
stored server-side.

## What the client sees

| Route | Purpose |
|---|---|
| `GET /client/{client_id}?t=<token>` | This month's summary card |
| `GET /client/{client_id}/calls?t=<token>` | Recent call log |
| `GET /client/{client_id}/invoice/{YYYY-MM}?t=<token>` | Printable invoice |

### Summary card fields (visible to client)
- Calls handled
- Emergencies routed
- Bookings captured (distinct calls that contained a `Scheduling` turn)
- Minutes used / plan limit
- Last call timestamp
- Calls filtered (spam/silence) — shown faintly

### Call log fields (visible to client)
- Timestamp, duration, outcome (friendly label), inferred top intent,
  "Emergency" flag.

### Invoice fields (visible to client)
- Monthly plan price, included-calls allowance, calls handled this month,
  overage calls × overage rate, total. Client is the billed party, so
  these numbers ARE the client's prices — distinct from our internal
  platform cost / margin.

### Never visible to client
- Platform cost per call / minute
- Margin dollars or percent
- Revenue from other clients
- Token payload beyond what's already in the URL

If you're adding a new field to the portal, grep it for the words
`cost`, `margin`, `revenue`, `platform_cost` — none of those should ever
appear in the rendered response bodies.

## Issuing a URL

Set the secret once in `.env` (32+ random characters):

```
CLIENT_PORTAL_SECRET=<random-32-chars>
PUBLIC_BASE_URL=https://your-tunnel-domain.example.com
```

Then mint a URL:

```bash
python -m src.client_portal issue ace_hvac
# → https://your-tunnel-domain.example.com/client/ace_hvac?t=<token>
```

Send that URL to the client via email. They bookmark it. That's it.

## Rotating access

Change `CLIENT_PORTAL_SECRET` in `.env` and restart the server. Every
previously-issued token is now invalid. Re-mint and re-send URLs to each
active client.

This is a clean break — there is no partial rotation. Use it when:
- A client leaves the platform
- The secret is suspected compromised
- You want to force all clients to re-confirm the URL

## Design choices (and why)

- **HMAC over JWT.** Simpler, smaller, no JWT library dependency. We
  don't need expiration semantics — rotation via secret change is enough
  for this stage.
- **No server-side token table.** Stateless. Adding a table would only be
  useful for per-token revocation, which isn't in scope.
- **403 on unknown client.** Prevents enumeration via status-code
  differences.
- **Reserved `_`-prefixed client IDs unreachable via portal.** These are
  template/fallback configs; exposing them would serve generic data to
  unauthenticated callers.
- **Inline HTML, no template engine.** Same style as `src/admin.py` —
  avoids a Jinja dependency. Print-friendly CSS for the invoice view.

## Operational notes

- The portal uses the same `AdminRateLimitMiddleware` prefix list for
  protection only if you include `/client` in `ADMIN_RATE_LIMIT_PATHS`.
  By default it is NOT rate-limited because legitimate client browsing
  can be noisy. Add it if you see abuse.
- Portal responses carry the same security headers
  (`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`) as
  the rest of the app.
- The portal reads from `data/usage.db` — the same database the admin
  dashboard uses. If that DB is cold or wiped, the portal shows empty
  state until new calls come in.

## Related files

- `src/client_portal.py` — router + CLI + token helpers
- `tests/test_client_portal.py` — roundtrip + route auth + no-leak checks
- `src/invoices.py` (P2) — richer invoice body, used when available
