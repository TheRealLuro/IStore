# Edge security with Cloudflare

neuthek runs two public surfaces:

1. **Marketing site** — the Vite SPA + its small Express API (`marketing/`,
   one Render Web Service).
2. **The app** — FastAPI behind Caddy (self-host docker-compose, and the
   managed hosted deployment).

Putting **Cloudflare** in front of both adds a managed edge: TLS, HTTP/3,
a WAF + managed rule sets, DDoS absorption, bot management, and a CDN
cache for static assets — without changing application code.

This doc has two parts: **what's already prepared in the repo** (done) and
**the operator checklist** (you, in the Cloudflare dashboard + DNS — we
can't do that part).

---

## Already prepared in the repo

- **Caddy (self-host app)** trusts Cloudflare's published proxy ranges and
  reads the real visitor IP from `CF-Connecting-IP` (`Caddyfile` global
  `servers { trusted_proxies static … ; client_ip_headers Cf-Connecting-Ip … }`).
  So `{client_ip}`, per-IP rate limits, lockouts, and audit logs reflect
  the actual user, not a Cloudflare edge node. (Harmless when Cloudflare
  isn't in front — those source IPs simply never match.)
  **Refresh the CIDR list from <https://www.cloudflare.com/ips/> periodically.**
- **Marketing Express** keys rate-limits and records signup IPs on
  `CF-Connecting-IP` when present, falling back to `X-Forwarded-For` /
  `req.ip` (`marketing/server.mjs` `clientIp()` + the rate-limit
  `keyGenerator`). Prevents one CF edge node from sharing a throttle
  bucket with everyone behind it.
- Both already send tight security headers (Caddy `header { … }`; Express
  header middleware). Cloudflare layers on top, it doesn't replace them.

---

## Operator checklist (Cloudflare dashboard + DNS)

### 1. Add the zone
1. Add your domain to Cloudflare; update the registrar's nameservers to
   the two Cloudflare assigns.
2. Create **proxied** (orange-cloud) DNS records:
   - `@` / `www` → the **app** origin (or your `hosted` host).
   - the marketing host (e.g. `neuthek.com` apex or a `www`/root) → the
     **Render** marketing service. (Render: add the custom domain there
     too, then point the proxied record at Render's target.)

### 2. TLS
- SSL/TLS mode: **Full (strict)** — Cloudflare ↔ origin is encrypted and
  the origin cert is validated. (Caddy's Let's Encrypt cert satisfies
  this; Render terminates TLS for the marketing service.)
- Enable **Always Use HTTPS**, **HSTS** (matches what Caddy/Express
  already send), **TLS 1.3**, **Automatic HTTPS Rewrites**.

### 3. Lock the origin to Cloudflare (important)
This is what makes `CF-Connecting-IP` trustworthy and stops attackers
bypassing the WAF by hitting the origin directly:
- **Self-host app**: firewall the origin so only Cloudflare's IP ranges
  (<https://www.cloudflare.com/ips/>) can reach Caddy's 80/443 — or
  enable **Authenticated Origin Pulls** and have Caddy require the CF
  client cert.
- **Marketing (Render)**: enable Cloudflare **Authenticated Origin
  Pulls**, or restrict the Render service to Cloudflare ranges if your
  plan allows. (Render always sits behind its own proxy, so also keep
  `trust proxy` correct — see note below.)

### 4. WAF + bot + rate limiting
- Turn on the **Cloudflare Managed Ruleset** + **OWASP Core Ruleset**.
- **Bot Fight Mode** (or Super Bot Fight Mode) on.
- A **Rate Limiting rule** in front of the auth + signup endpoints
  (`/auth/*`, `/api/waitlist/signup`, `/api/admin/*`) as defense-in-depth
  on top of the app's own limits.
- Consider a **Managed Challenge** on `/admin` and `/api/admin/*`.

### 5. Caching (marketing only)
- Cache static assets (`/assets/*`, images, fonts) aggressively; **bypass
  cache** for `/api/*` and any HTML you want always-fresh. A "Cache
  Everything" page rule scoped to `/assets/*` is the simplest win.
- Do **not** cache the app origin's authenticated routes.

### 6. After cut-over — verify
- `curl -sI https://<domain>` shows `server: cloudflare` + `cf-ray`.
- Sign-in lockout + waitlist rate limits still trigger per *real* IP (not
  globally) — confirm two different networks aren't sharing a bucket.
- Audit log / signup rows show real visitor IPs, not `104.x`/`172.x`
  Cloudflare ranges.

### Note on `trust proxy` (marketing)
`marketing/server.mjs` sets `app.set("trust proxy", 1)` for the single
Render hop. With Cloudflare **and** Render in front there are two hops,
but rate-limiting and IP logging key on `CF-Connecting-IP` directly
(see above), so they stay correct regardless. If you ever rely on
`req.ip` itself behind both, bump `trust proxy` to `2`.
