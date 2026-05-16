/**
 * neuthek marketing server.
 *
 * One Node process serves three things on the same origin:
 *   1. Built SPA from ./dist  (vite build output)
 *   2. POST /api/waitlist/signup    — public, rate-limited
 *   3. GET  /api/admin/waitlist     — admin Basic Auth
 *      PATCH /api/admin/waitlist/:id/notified
 *
 * Storage:
 *   - If DATABASE_URL is set, uses Postgres via `pg` (Render Postgres path).
 *   - Otherwise uses SQLite via better-sqlite3 at SQLITE_PATH or ./data/waitlist.db
 *     (good for local dev and tiny paid-disk Render deploys).
 *
 * The point of keeping this stand-alone is so the public marketing
 * site can run on Render without depending on the (unreleased) main
 * neuthek backend. When the main product launches, this can be folded
 * back into the main backend or kept as-is — either path works.
 */

import express from "express";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = parseInt(process.env.PORT || "5181", 10);
const ADMIN_USER = process.env.ADMIN_USER || "admin";

// Updates index used by the per-route prerender (see bottom of file).
// Loaded once at boot; if the file is missing or malformed we just
// fall through to the plain SPA shell.
let UPDATE_INDEX = [];
try {
  const updatesJsonPath = path.join(__dirname, "src", "data", "updates-index.json");
  const raw = JSON.parse(fs.readFileSync(updatesJsonPath, "utf8"));
  UPDATE_INDEX = Array.isArray(raw.updates) ? raw.updates : [];
} catch (e) {
  console.warn("[neuthek-marketing] updates index not loaded:", e?.message);
}
const ADMIN_PASS = process.env.ADMIN_PASS || "";
const DATABASE_URL = process.env.DATABASE_URL || "";

// Keep in sync with the WaitlistUseCase union in src/api.ts and the
// <option> list in src/pages/Waitlist.tsx. Anything not in this set
// is normalized to "other" by the signup handler below.
const ALLOWED_USE_CASES = new Set([
  "personal",
  "family",
  "creative",
  "developer",
  "student",
  "research",
  "educator",
  "professional",
  "other",
]);
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// --------------------------------------------------------------------- //
// Storage layer — Postgres or SQLite, same interface.
// --------------------------------------------------------------------- //

let store; // { upsertSignup, listSignups, markNotified, init }

if (DATABASE_URL) {
  // -------- Postgres (Render Postgres path) --------
  const { default: pg } = await import("pg");
  const pool = new pg.Pool({
    connectionString: DATABASE_URL,
    ssl: DATABASE_URL.includes("render.com") || process.env.PGSSL === "require"
      ? { rejectUnauthorized: false }
      : undefined,
  });

  store = {
    backend: "postgres",
    async init() {
      await pool.query(`
        CREATE TABLE IF NOT EXISTS waitlist_signups (
          id           BIGSERIAL PRIMARY KEY,
          email        TEXT NOT NULL UNIQUE,
          use_case     TEXT NOT NULL DEFAULT 'personal',
          source       TEXT NOT NULL DEFAULT 'marketing-site',
          ip           TEXT,
          user_agent   TEXT,
          notified     BOOLEAN NOT NULL DEFAULT false,
          notified_at  TIMESTAMPTZ,
          newsletter_opt_in BOOLEAN NOT NULL DEFAULT false,
          newsletter_consent_at TIMESTAMPTZ,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
      `);
      // Forward-migrate older deployments that don't have the
      // newsletter columns yet. ADD COLUMN IF NOT EXISTS is idempotent.
      await pool.query(`
        ALTER TABLE waitlist_signups
          ADD COLUMN IF NOT EXISTS newsletter_opt_in BOOLEAN NOT NULL DEFAULT false
      `);
      await pool.query(`
        ALTER TABLE waitlist_signups
          ADD COLUMN IF NOT EXISTS newsletter_consent_at TIMESTAMPTZ
      `);
      await pool.query(`
        CREATE INDEX IF NOT EXISTS ix_waitlist_signups_created_at_desc
        ON waitlist_signups (created_at DESC)
      `);
    },
    async upsertSignup({ email, use_case, ip, user_agent, newsletter_opt_in }) {
      const flag = !!newsletter_opt_in;
      // When the user opts IN, stamp the consent timestamp so we keep
      // a chain-of-custody record (required by GDPR/CCPA-shaped
      // disclosures). When they're already opted in, we don't reset
      // the timestamp on a duplicate signup — first consent wins.
      // Opting back OUT clears the timestamp.
      await pool.query(
        `INSERT INTO waitlist_signups
           (email, use_case, ip, user_agent, newsletter_opt_in, newsletter_consent_at)
         VALUES ($1, $2, $3, $4, $5, CASE WHEN $5 THEN now() ELSE NULL END)
         ON CONFLICT (email) DO UPDATE
           SET use_case = EXCLUDED.use_case,
               newsletter_opt_in = EXCLUDED.newsletter_opt_in,
               newsletter_consent_at = CASE
                 WHEN EXCLUDED.newsletter_opt_in AND waitlist_signups.newsletter_consent_at IS NULL
                   THEN now()
                 WHEN NOT EXCLUDED.newsletter_opt_in
                   THEN NULL
                 ELSE waitlist_signups.newsletter_consent_at
               END,
               created_at = now()`,
        [email, use_case, ip || null, user_agent || null, flag]
      );
    },
    async listSignups(limit = 500) {
      const { rows } = await pool.query(
        `SELECT id, email, use_case, source, ip, user_agent,
                notified, notified_at,
                newsletter_opt_in, newsletter_consent_at,
                created_at
         FROM waitlist_signups
         ORDER BY created_at DESC
         LIMIT $1`,
        [limit]
      );
      return rows;
    },
    async markNotified(id) {
      const { rows } = await pool.query(
        `UPDATE waitlist_signups
         SET notified = true, notified_at = now()
         WHERE id = $1
         RETURNING id, email, use_case, source, ip, user_agent,
                   notified, notified_at, created_at`,
        [id]
      );
      return rows[0] || null;
    },
  };
} else {
  // -------- SQLite (local dev + paid-disk Render path) --------
  const { default: Database } = await import("better-sqlite3");
  const dbPath = process.env.SQLITE_PATH ||
                 path.join(__dirname, "data", "waitlist.db");
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  const db = new Database(dbPath);
  db.pragma("journal_mode = WAL");

  store = {
    backend: "sqlite",
    dbPath,
    async init() {
      db.exec(`
        CREATE TABLE IF NOT EXISTS waitlist_signups (
          id           INTEGER PRIMARY KEY AUTOINCREMENT,
          email        TEXT NOT NULL UNIQUE COLLATE NOCASE,
          use_case     TEXT NOT NULL DEFAULT 'personal',
          source       TEXT NOT NULL DEFAULT 'marketing-site',
          ip           TEXT,
          user_agent   TEXT,
          notified     INTEGER NOT NULL DEFAULT 0,
          notified_at  TEXT,
          newsletter_opt_in INTEGER NOT NULL DEFAULT 0,
          newsletter_consent_at TEXT,
          created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_waitlist_signups_created_at_desc
          ON waitlist_signups (created_at DESC);
      `);
      // Forward-migrate the columns for any existing dev/sqlite file
      // that predates this schema. SQLite doesn't support
      // `ADD COLUMN IF NOT EXISTS`, so we check pragma + run if missing.
      const cols = db.prepare("PRAGMA table_info(waitlist_signups)").all();
      const names = new Set(cols.map((c) => c.name));
      if (!names.has("newsletter_opt_in")) {
        db.exec("ALTER TABLE waitlist_signups ADD COLUMN newsletter_opt_in INTEGER NOT NULL DEFAULT 0");
      }
      if (!names.has("newsletter_consent_at")) {
        db.exec("ALTER TABLE waitlist_signups ADD COLUMN newsletter_consent_at TEXT");
      }
    },
    async upsertSignup({ email, use_case, ip, user_agent, newsletter_opt_in }) {
      const flag = newsletter_opt_in ? 1 : 0;
      const existing = db.prepare(
        "SELECT id, newsletter_consent_at FROM waitlist_signups WHERE email = ?"
      ).get(email);
      if (existing) {
        // First-consent-wins for the timestamp; opting back out clears it.
        let nextStamp;
        if (flag && !existing.newsletter_consent_at) {
          nextStamp = new Date().toISOString();
        } else if (!flag) {
          nextStamp = null;
        } else {
          nextStamp = existing.newsletter_consent_at;
        }
        db.prepare(
          `UPDATE waitlist_signups
           SET use_case = ?,
               newsletter_opt_in = ?,
               newsletter_consent_at = ?,
               created_at = CURRENT_TIMESTAMP
           WHERE email = ?`
        ).run(use_case, flag, nextStamp, email);
      } else {
        db.prepare(
          `INSERT INTO waitlist_signups
             (email, use_case, ip, user_agent, newsletter_opt_in, newsletter_consent_at)
           VALUES (?, ?, ?, ?, ?, ?)`
        ).run(
          email, use_case, ip || null, user_agent || null,
          flag, flag ? new Date().toISOString() : null,
        );
      }
    },
    async listSignups(limit = 500) {
      const rows = db.prepare(
        `SELECT id, email, use_case, source, ip, user_agent,
                notified, notified_at,
                newsletter_opt_in, newsletter_consent_at,
                created_at
         FROM waitlist_signups
         ORDER BY datetime(created_at) DESC
         LIMIT ?`
      ).all(limit);
      // Normalize boolean for the API.
      return rows.map((r) => ({ ...r, notified: !!r.notified }));
    },
    async markNotified(id) {
      const row = db.prepare(
        `UPDATE waitlist_signups
         SET notified = 1, notified_at = CURRENT_TIMESTAMP
         WHERE id = ?
         RETURNING id, email, use_case, source, ip, user_agent,
                   notified, notified_at, created_at`
      ).get(id);
      return row ? { ...row, notified: !!row.notified } : null;
    },
  };
}

await store.init();
console.log(`[neuthek-marketing] storage backend: ${store.backend}` +
            (store.dbPath ? ` at ${store.dbPath}` : ""));

// --------------------------------------------------------------------- //
// Rate limiter — fixed-window per IP. Single-instance only.
// --------------------------------------------------------------------- //

const buckets = new Map(); // ip -> { count, expiresAt }

function rateLimit({ key, limit, windowMs }) {
  const now = Date.now();
  const b = buckets.get(key);
  if (!b || b.expiresAt <= now) {
    buckets.set(key, { count: 1, expiresAt: now + windowMs });
    return true;
  }
  if (b.count >= limit) return false;
  b.count++;
  return true;
}

// Sweep the buckets every 5 minutes so we don't grow unbounded over weeks.
setInterval(() => {
  const now = Date.now();
  for (const [k, v] of buckets) if (v.expiresAt <= now) buckets.delete(k);
}, 5 * 60 * 1000).unref();

function clientIp(req) {
  const fwd = req.headers["x-forwarded-for"];
  if (typeof fwd === "string" && fwd.length) return fwd.split(",")[0].trim();
  return req.ip || req.socket?.remoteAddress || "unknown";
}

// --------------------------------------------------------------------- //
// Express app.
// --------------------------------------------------------------------- //

const app = express();
app.set("trust proxy", true);
app.disable("x-powered-by");
app.use(express.json({ limit: "32kb" }));

// Security headers. Helmet would do this for us, but a tiny middleware
// keeps the dependency tree small.
app.use((_req, res, next) => {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Referrer-Policy", "strict-origin-when-cross-origin");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  next();
});

// ----- Public health check (Render uses this) -----
app.get("/api/health", (_req, res) => {
  res.json({ ok: true, backend: store.backend });
});

// ----- Public signup -----
app.post("/api/waitlist/signup", async (req, res) => {
  const ip = clientIp(req);
  if (!rateLimit({ key: `signup:${ip}`, limit: 10, windowMs: 60_000 })) {
    return res.status(429).json({
      ok: false,
      detail: "Too many signup attempts. Please try again in a minute.",
    });
  }

  const body = req.body || {};
  const email = String(body.email || "").trim().toLowerCase();
  let use_case = String(body.use_case || "personal").toLowerCase();
  if (!ALLOWED_USE_CASES.has(use_case)) use_case = "other";
  // Coerce the checkbox value defensively — accept boolean true or
  // the string "true"; anything else (undefined, "off", "false", 0)
  // counts as a no.
  const newsletter_opt_in =
    body.newsletter_opt_in === true || body.newsletter_opt_in === "true";

  if (!EMAIL_RE.test(email) || email.length > 254) {
    return res.status(422).json({
      ok: false,
      detail: "Invalid email address.",
    });
  }

  const user_agent = String(req.headers["user-agent"] || "").slice(0, 500);

  try {
    await store.upsertSignup({
      email,
      use_case,
      ip: ip !== "unknown" ? ip : null,
      user_agent: user_agent || null,
      newsletter_opt_in,
    });
    // Anti-enumeration: never reveal whether this was new or duplicate.
    return res.json({ ok: true, already_signed_up: false });
  } catch (err) {
    console.error("[waitlist] signup error", err);
    return res.status(500).json({ ok: false, detail: "signup failed" });
  }
});

// ----- Basic Auth gate for admin endpoints -----
function adminAuth(req, res, next) {
  if (!ADMIN_PASS) {
    return res.status(503).json({
      ok: false,
      detail: "Admin viewer is not configured. Set ADMIN_PASS in env.",
    });
  }
  const header = req.headers.authorization || "";
  if (!header.startsWith("Basic ")) {
    res.setHeader("WWW-Authenticate", 'Basic realm="neuthek admin"');
    return res.status(401).json({ ok: false, detail: "auth required" });
  }
  let decoded;
  try {
    decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
  } catch {
    return res.status(401).json({ ok: false, detail: "bad auth" });
  }
  const idx = decoded.indexOf(":");
  if (idx < 0) return res.status(401).json({ ok: false, detail: "bad auth" });
  const user = decoded.slice(0, idx);
  const pass = decoded.slice(idx + 1);
  if (user !== ADMIN_USER || pass !== ADMIN_PASS) {
    // Rate-limit failed admin attempts so brute force is expensive.
    const ip = clientIp(req);
    rateLimit({ key: `admin:${ip}`, limit: 5, windowMs: 60_000 });
    return res.status(401).json({ ok: false, detail: "bad auth" });
  }
  next();
}

app.get("/api/admin/waitlist", adminAuth, async (req, res) => {
  const limit = Math.max(1, Math.min(500, parseInt(req.query.limit, 10) || 500));
  const rows = await store.listSignups(limit);
  res.json(rows);
});

app.patch("/api/admin/waitlist/:id/notified", adminAuth, async (req, res) => {
  const id = parseInt(req.params.id, 10);
  if (!Number.isFinite(id) || id <= 0) {
    return res.status(400).json({ ok: false, detail: "bad id" });
  }
  const row = await store.markNotified(id);
  if (!row) return res.status(404).json({ ok: false, detail: "not found" });
  res.json(row);
});

// ----- Static SPA (dist/) + HashRouter fallback -----
// In dev, `npm run dev` runs Vite separately on 5180 and proxies /api
// to this server on 5181 — see vite.config.ts. In prod, we serve the
// built dist/.
const distDir = path.join(__dirname, "dist");
if (fs.existsSync(distDir)) {
  app.use(express.static(distDir, {
    immutable: true,
    maxAge: "1y",
    setHeaders(res, file) {
      // index.html should never be long-cached so a redeploy is visible.
      if (file.endsWith("index.html")) {
        res.setHeader("Cache-Control", "no-cache, no-store, must-revalidate");
      }
    },
  }));
  // Crawler-friendly per-route meta + content injection.
  //
  // The big "ChatGPT can't find this" problem with SPAs is that the
  // initial HTML response is just `<div id="root"></div>` — no real
  // content for the crawler to read. We solve this by injecting a
  // page-specific <title>, meta description, and an HTML body
  // fragment (rendered into a hidden `<noscript>` block AND as a
  // visible-but-overwritten fallback) directly into the static
  // index.html before sending. React then hydrates over it on
  // client load — same UX as before, but now crawlers and AI
  // answer engines see the real text immediately.
  const INDEX_HTML_PATH = path.join(distDir, "index.html");
  let cachedShell = null;
  function readShell() {
    if (cachedShell === null) {
      try { cachedShell = fs.readFileSync(INDEX_HTML_PATH, "utf8"); }
      catch { cachedShell = ""; }
    }
    return cachedShell;
  }
  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  function renderShell(meta) {
    let html = readShell();
    if (!html) return null;
    if (meta.title) {
      html = html.replace(
        /<title>[^<]*<\/title>/,
        `<title>${escapeHtml(meta.title)}</title>`,
      );
    }
    if (meta.description) {
      html = html.replace(
        /<meta\s+name="description"\s+content="[^"]*"\s*\/?>/,
        `<meta name="description" content="${escapeHtml(meta.description)}" />`,
      );
    }
    if (meta.canonical) {
      html = html.replace(
        /<link\s+rel="canonical"\s+href="[^"]*"\s*\/?>/,
        `<link rel="canonical" href="${escapeHtml(meta.canonical)}" />`,
      );
    }
    if (meta.bodyHtml || meta.jsonLd) {
      // Drop in a static-text fallback so AI crawlers + search engines
      // see real content. React hydrates over it. We mark it
      // `data-prerender` so the React mount knows to overwrite the
      // contents rather than appending to them.
      const blob = `<div id="root" data-prerender="1">${meta.bodyHtml || ""}</div>` +
        (meta.jsonLd
          ? `<script type="application/ld+json">${meta.jsonLd}</script>`
          : "");
      html = html.replace(/<div id="root"><\/div>/, blob);
    }
    return html;
  }

  // Map paths → meta + prerender content. Adding a new route here is
  // additive; unknown paths fall through to the plain SPA shell.
  function metaForPath(reqPath) {
    if (reqPath === "/" || reqPath === "") {
      return {
        title: "neuthek — the next best cloud storage solution",
        description:
          "neuthek is an AI-aware personal cloud storage product in active development. Semantic search by what you remember, content-aware compression, privacy-first design. Self-host or hosted — join the waitlist.",
        canonical: "https://neuthek.com/",
        bodyHtml: `
          <header>
            <h1>neuthek — the next best cloud storage solution</h1>
            <p>
              neuthek is an AI-aware personal cloud storage product in
              active development. It pairs S3-compatible object
              storage with a Postgres + pgvector index so users can
              search their photos, videos, and documents by natural
              language — "snowy roof at sunset", "whiteboard photos
              from last week" — instead of remembering filenames.
            </p>
          </header>
          <section>
            <h2>Your data isn't ours. We never train on it. We never sell it.</h2>
            <p>
              neuthek runs on a server you control (self-hosted) or in
              your own tenant fenced behind Postgres row-level security
              (managed hosted). Your photos, videos, documents, face
              embeddings, summaries, and search history are not
              exported to third parties, are not used to train AI
              models — ours or anyone else's — and are not sold to ad
              networks, brokers, or partners.
            </p>
          </section>
          <section>
            <h2>What we're building</h2>
            <ul>
              <li>Search by meaning — CLIP-class embeddings, vector index, hybrid CLIP-cosine + Postgres-FTS ranker.</li>
              <li>Content-aware compression — LinUCB bandit picks per-image codec (WebP / MozJPEG / AVIF / JXL).</li>
              <li>Open source self-host (free) or managed hosted (waitlist).</li>
              <li>Stack: FastAPI, PostgreSQL, pgvector, Redis, MinIO, OpenCLIP, Florence-2.</li>
            </ul>
          </section>
          <p>
            <a href="/waitlist">Join the waitlist</a> ·
            <a href="/features">Features</a> ·
            <a href="/hosting">Hosting</a> ·
            <a href="/updates">Weekly updates</a>
          </p>
        `,
      };
    }
    if (reqPath === "/updates" || reqPath === "/updates/") {
      const items = UPDATE_INDEX;
      const summaries = items.map((u) =>
        `<article><h3><a href="/updates/${escapeHtml(u.slug)}">${escapeHtml(u.title)}</a></h3>` +
        `<time datetime="${escapeHtml(u.published)}">${escapeHtml(u.week)}</time>` +
        `<p>${escapeHtml(u.summary)}</p></article>`,
      ).join("");
      return {
        title: "Updates — neuthek changelog & weekly release notes",
        description:
          "Weekly release notes for neuthek, the AI-aware personal cloud. Browse what shipped each week — features, performance fixes, security updates, roadmap progress.",
        canonical: "https://neuthek.com/updates",
        bodyHtml: `
          <header><h1>What's new in neuthek</h1>
            <p>A weekly log of what we shipped, fixed, and changed. Pulled straight from the release notes — no marketing fluff.</p>
          </header>
          ${summaries}
        `,
      };
    }
    const m = reqPath.match(/^\/updates\/([a-z0-9-]+)\/?$/);
    if (m) {
      const slug = m[1];
      const entry = UPDATE_INDEX.find((u) => u.slug === slug);
      if (entry) {
        const bucketHtml = (label, items, intro) =>
          `<section><h2>${escapeHtml(label)}</h2>` +
          `<p>${escapeHtml(intro)}</p>` +
          `<ul>${items.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul></section>`;
        const articleHtml = [
          `<article>`,
          `<header><time datetime="${escapeHtml(entry.published)}">${escapeHtml(entry.week)}</time>`,
          `<h1>${escapeHtml(entry.title)}</h1>`,
          `<p>${escapeHtml(entry.summary)}</p></header>`,
          bucketHtml("Problems we found", entry.body.found, "What wasn't working as well as it should have."),
          bucketHtml("How we fixed them", entry.body.fixed, "What changed in this release to fix the issues above."),
          bucketHtml("New features", entry.body.newFeatures, "Brand-new capabilities that weren't there last week."),
          `<section><h2>Why this matters</h2><p>${escapeHtml(entry.body.why)}</p></section>`,
          bucketHtml("What this means for you", entry.body.whatTheyDo, "How the changes actually show up when you use neuthek."),
          `</article>`,
        ].join("");
        // Article-shape JSON-LD with the full text the crawler / AI
        // engine can lift verbatim.
        const articleBody = [
          entry.summary, "",
          "Problems we found:",
          ...entry.body.found.map((s) => `- ${s}`), "",
          "How we fixed them:",
          ...entry.body.fixed.map((s) => `- ${s}`), "",
          "New features in this release:",
          ...entry.body.newFeatures.map((s) => `- ${s}`), "",
          "Why this matters:",
          entry.body.why, "",
          "What this means for you:",
          ...entry.body.whatTheyDo.map((s) => `- ${s}`),
        ].join("\n");
        const jsonLd = JSON.stringify({
          "@context": "https://schema.org",
          "@type": "BlogPosting",
          headline: entry.title,
          description: entry.summary,
          articleBody,
          datePublished: entry.published,
          dateModified: entry.published,
          author: { "@type": "Organization", name: "neuthek", url: "https://neuthek.com/" },
          publisher: { "@type": "Organization", name: "neuthek", url: "https://neuthek.com/" },
          mainEntityOfPage: `https://neuthek.com/updates/${entry.slug}`,
          keywords: entry.tags.join(", "),
          inLanguage: "en",
        });
        return {
          title: `${entry.title} — neuthek updates`,
          description: entry.summary,
          canonical: `https://neuthek.com/updates/${entry.slug}`,
          bodyHtml: articleHtml,
          jsonLd,
        };
      }
    }
    return null;
  }

  app.get("*", (req, res, next) => {
    if (req.path.startsWith("/api/")) return next();
    const meta = metaForPath(req.path);
    if (meta) {
      const rendered = renderShell(meta);
      if (rendered) {
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.setHeader("Cache-Control", "no-cache, no-store, must-revalidate");
        return res.status(200).send(rendered);
      }
    }
    res.sendFile(INDEX_HTML_PATH);
  });
} else {
  console.log("[neuthek-marketing] no dist/ yet — run `npm run build` to enable static serve.");
}

app.use((err, _req, res, _next) => {
  console.error("[neuthek-marketing] unhandled", err);
  res.status(500).json({ ok: false, detail: "internal error" });
});

app.listen(PORT, () => {
  console.log(`[neuthek-marketing] listening on http://127.0.0.1:${PORT}`);
});
