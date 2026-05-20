# Deploying neuthek-marketing to Render

This is a single-blueprint deploy: one Render Web Service runs Express
(`server.mjs`) which serves both the API and the built SPA, backed by
a free Render Postgres database. No dependency on the main neuthek
backend — this whole surface is self-contained.

Total time: ~10 minutes once you have a Render account.

---

## Prerequisites

- A GitHub account.
- The repo (`TheRealLuro/Neuthek`, GitHub-renamed from the historical
  `TheRealLuro/IStore` — redirects still work) with the `main` branch
  up to date, including all `marketing/` commits.
- A Render account at https://render.com (free tier is enough).

To verify the code is pushed:

```bash
git push origin main
git log origin/main..HEAD --oneline
# Empty output = nothing to push = ready
```

---

## Step 1 — Create the blueprint on Render (3 minutes)

1. Sign in to https://dashboard.render.com.
2. Click **New +** → **Blueprint**.
3. Connect your GitHub account if you haven't yet, then pick the
   repository. Render scans the repo and finds `marketing/render.yaml`
   automatically.
4. Render shows what it will create:
   - A **PostgreSQL database** named `neuthek-marketing-db` (free tier).
   - A **Web Service** named `neuthek-marketing` (free tier, Node).
5. Click **Apply** at the bottom.

Render now provisions the database (~30s), then starts the first
build of the web service. The web service waits for the database to
be ready before it boots.

---

## Step 2 — Set the admin password (THIS IS THE SECURITY STEP)

The blueprint marks `ADMIN_PASS` as `sync: false` so it never lives
in git. You **must** set it in the dashboard or the admin viewer
returns `503 — Admin viewer is not configured`.

1. In the Render dashboard, click into the **neuthek-marketing**
   web service.
2. Click **Environment** in the left sidebar.
3. Find the **ADMIN_PASS** row (it'll be blank). Click **Edit**.
4. Set a strong password (≥16 characters, mixed case + numbers +
   symbols recommended). Save.
5. Render auto-redeploys the service with the new env var.

Optionally edit `ADMIN_USER` from the default `admin` to a name you
prefer.

---

## Step 3 — First-time deploy completes (~3-5 minutes)

Watch the **Logs** tab. You're looking for:

```
[neuthek-marketing] storage backend: postgres
[neuthek-marketing] listening on http://127.0.0.1:10000
```

Render then health-checks `/api/health` and routes external traffic
to the service.

Your URL will be:

```
https://neuthek-marketing-XXXX.onrender.com
```

(The `XXXX` is a random hash Render assigns. You can find it at the
top of the service's dashboard page.)

---

## Step 4 — Smoke-test

1. **Public site**: visit `https://<your-url>/` — the marketing
   site loads with the banner, hero, carousel.
2. **Waitlist signup**: visit `/#/waitlist`, fill in your email +
   pick a use case, click "Add me to the waitlist." You should see
   "You're on the list."
3. **Admin viewer**: visit `https://<your-url>/#/admin`. The login
   card appears. Enter `admin` (or your custom `ADMIN_USER`) and
   the password you set in Step 2. You should see your signup in
   the table.

Try a few:
- Sign up the same email twice → admin viewer still shows one row
  (idempotent, per-email unique).
- Submit a malformed email → form rejects with validation error.
- Wrong admin password → "Wrong username or password."

---

## Updating the site

Anytime you push to `main`, Render auto-rebuilds and redeploys:

```bash
git push origin main
```

You'll see the new deploy in the Render dashboard's **Events** tab.

---

## Things to know about the free tier

- **Cold starts**: Free web services spin down after 15 minutes of
  inactivity. The first hit after that takes ~30 seconds while the
  service wakes up. Subsequent requests are instant. This is fine
  for a pre-launch marketing site; upgrade to the paid tier later
  if you want zero cold starts.
- **Free Postgres expires after 90 days**. Render emails a warning;
  you can either upgrade the database to the paid tier ($7/mo) or
  let it expire and lose the waitlist data. Plan to upgrade before
  you start collecting real signups.
- **Storage limits**: free Postgres is 256 MB. Waitlist signups are
  tiny (~200 bytes each) — that's room for ~1M signups before
  hitting the cap.

---

## Custom domain (optional, free)

If you own a domain (e.g. `neuthek.com`):

1. In the Render dashboard for the web service, click **Settings**
   → scroll to **Custom Domains** → **Add Custom Domain**.
2. Enter your domain. Render gives you a CNAME or A record to add
   at your DNS provider.
3. Add the DNS record. Within a few minutes, Render provisions a
   Let's Encrypt TLS cert and serves your site at the custom
   domain.

Update the canonical URL in `marketing/index.html` and the
`sitemap.xml` to your real domain once it's live so SEO points the
right way.

---

## Changing the admin password later

1. Render dashboard → neuthek-marketing → Environment.
2. Edit **ADMIN_PASS**, save.
3. Auto-redeploys with the new password (~30s).
4. Any open admin tab needs to re-login.

---

## Rollback / pause

If something goes sideways:

- **Pause the service**: dashboard → **Manual Deploy** → **Suspend
  Service**. The URL returns a friendly "service unavailable" page;
  no data is lost.
- **Roll back to an earlier deploy**: **Events** tab → find a
  successful earlier deploy → **Rollback to this version**.

---

## What this deploy does NOT touch

- The main neuthek backend (FastAPI / Postgres / MinIO / Redis) is
  not deployed by this blueprint. Only the `marketing/` directory.
- The hosted neuthek product is still pre-release. This deploy is
  the marketing site + waitlist only.
