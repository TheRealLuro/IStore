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
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
      `);
      await pool.query(`
        CREATE INDEX IF NOT EXISTS ix_waitlist_signups_created_at_desc
        ON waitlist_signups (created_at DESC)
      `);
    },
    async upsertSignup({ email, use_case, ip, user_agent }) {
      await pool.query(
        `INSERT INTO waitlist_signups (email, use_case, ip, user_agent)
         VALUES ($1, $2, $3, $4)
         ON CONFLICT (email) DO UPDATE
           SET use_case = EXCLUDED.use_case,
               created_at = now()`,
        [email, use_case, ip || null, user_agent || null]
      );
    },
    async listSignups(limit = 500) {
      const { rows } = await pool.query(
        `SELECT id, email, use_case, source, ip, user_agent,
                notified, notified_at, created_at
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
          created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_waitlist_signups_created_at_desc
          ON waitlist_signups (created_at DESC);
      `);
    },
    async upsertSignup({ email, use_case, ip, user_agent }) {
      // SQLite upsert: INSERT OR REPLACE keeps the same id when email matches.
      const existing = db.prepare(
        "SELECT id FROM waitlist_signups WHERE email = ?"
      ).get(email);
      if (existing) {
        db.prepare(
          `UPDATE waitlist_signups
           SET use_case = ?, created_at = CURRENT_TIMESTAMP
           WHERE email = ?`
        ).run(use_case, email);
      } else {
        db.prepare(
          `INSERT INTO waitlist_signups (email, use_case, ip, user_agent)
           VALUES (?, ?, ?, ?)`
        ).run(email, use_case, ip || null, user_agent || null);
      }
    },
    async listSignups(limit = 500) {
      const rows = db.prepare(
        `SELECT id, email, use_case, source, ip, user_agent,
                notified, notified_at, created_at
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
  app.get("*", (req, res, next) => {
    if (req.path.startsWith("/api/")) return next();
    res.sendFile(path.join(distDir, "index.html"));
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
