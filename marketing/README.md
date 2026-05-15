# neuthek marketing

The public marketing + onboarding site for neuthek. Self-contained:
Express + Vite + React + TypeScript, with a Postgres-backed waitlist
that runs on a single Render Web Service. No dependency on the main
neuthek backend — designed to ship to the public internet before any
other piece of the product is released.

## What lives here

```
marketing/
  server.mjs           <- Express server (API + static SPA)
  index.html           <- SPA shell
  package.json         <- frontend + server deps
  vite.config.ts       <- dev: proxies /api -> 5181
  render.yaml          <- Render blueprint (Web Service + Postgres)
  public/
    favicon.svg        <- Constellation Glyph (the brand mark)
    og-cover.svg
    robots.txt
    sitemap.xml
    site.webmanifest
  src/
    main.tsx
    App.tsx
    styles.css
    api.ts             <- frontend client for /api/waitlist + /api/admin
    components/
      Banner.tsx       <- persistent "nothing released yet" banner
      Nav.tsx
      Footer.tsx
      WordMark.tsx     <- the inline-SVG brand mark
      TechCarousel.tsx
    pages/
      Home.tsx
      Features.tsx
      Hosting.tsx
      Developers.tsx
      Roadmap.tsx
      Compare.tsx
      Pricing.tsx
      Waitlist.tsx     <- POSTs to /api/waitlist/signup
      Admin.tsx        <- admin viewer at /#/admin (Basic Auth)
      Privacy.tsx
      Terms.tsx
      NotFound.tsx
```

## Local dev (two-process)

```bash
cd marketing
npm install

# terminal 1 — Express server on 5181
npm run dev:server

# terminal 2 — Vite SPA on 5180, proxies /api -> 5181
npm run dev
```

Open http://127.0.0.1:5180. The /api proxy is wired in
[vite.config.ts](vite.config.ts) so frontend `fetch("/api/...")` calls
work without CORS.

### Local dev (single-process)

Build the SPA into `dist/` then run only Express:

```bash
npm run build
ADMIN_PASS=hunter2 npm start
# http://127.0.0.1:5181
```

## Production: Render

1. Point Render at the repo, select the [render.yaml](render.yaml) blueprint
   with `rootDir: marketing`.
2. Render provisions a free Postgres database and a free Web Service.
3. In the Render dashboard's **Environment** tab for the web service,
   set `ADMIN_PASS` to a strong password (the blueprint has
   `sync: false` so it's never committed).
4. Visit `https://<your-render-domain>/` to see the marketing site,
   and `https://<your-render-domain>/#/admin` for the waitlist viewer.

The Postgres connection string is auto-injected as `DATABASE_URL`;
the server detects it and uses Postgres for storage. Without
`DATABASE_URL` (e.g. local dev), the server falls back to SQLite at
`./data/waitlist.db`.

## API

### Public

| Method | Path                  | Behavior |
|--------|-----------------------|----------|
| GET    | /api/health           | `{ok:true, backend:"postgres"|"sqlite"}` |
| POST   | /api/waitlist/signup  | `{email, use_case}` → `{ok:true, already_signed_up:false}`. Rate-limited 10/min/IP, idempotent on email, anti-enumeration response. |

### Admin (HTTP Basic Auth via ADMIN_USER / ADMIN_PASS)

| Method | Path                                | Behavior |
|--------|-------------------------------------|----------|
| GET    | /api/admin/waitlist                 | List signups, newest first, max 500. |
| PATCH  | /api/admin/waitlist/:id/notified    | Flip `notified=true` + stamp `notified_at`. |

## Editing rules

- Every claim about the product must be either truthful **today** (the
  rare case — e.g. "this site is online") or marked as **planned /
  coming / in development**. The product is not publicly released.
- Don't write "today," "free today," "available now," or invite people
  to `git clone` until the open-source build is actually published.
- Competitor mentions are nominative only. No vendor logos that aren't
  ours; brand names quoted as text.
- The persistent `Banner` reminding visitors that nothing is released
  yet must stay until the public launch.
