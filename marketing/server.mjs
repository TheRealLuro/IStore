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
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = parseInt(process.env.PORT || "5181", 10);
const ADMIN_USER = process.env.ADMIN_USER || "admin";

// Public origin used to build the verification link in outgoing emails.
// In prod, set PUBLIC_ORIGIN=https://neuthek.com so links resolve there.
// In dev with `npm run dev` we point at the Vite proxy (5180) so clicking
// the link from console lands on the SPA.
const PUBLIC_ORIGIN =
  process.env.PUBLIC_ORIGIN ||
  (process.env.NODE_ENV === "production"
    ? "https://neuthek.com"
    : `http://localhost:5180`);

// HMAC secret for verify tokens. Persisted to data/secret_verify.key so a
// process restart doesn't invalidate every pending verification email.
const SECRET_DIR = path.join(__dirname, "data");
const SECRET_PATH = path.join(SECRET_DIR, "secret_verify.key");
function loadOrCreateVerifySecret() {
  if (process.env.WAITLIST_VERIFY_SECRET) return process.env.WAITLIST_VERIFY_SECRET;
  try {
    return fs.readFileSync(SECRET_PATH, "utf8").trim();
  } catch {
    fs.mkdirSync(SECRET_DIR, { recursive: true });
    const fresh = crypto.randomBytes(32).toString("hex");
    fs.writeFileSync(SECRET_PATH, fresh, { mode: 0o600 });
    return fresh;
  }
}
const VERIFY_SECRET = loadOrCreateVerifySecret();
const VERIFY_TTL_SECONDS = 60 * 60 * 24 * 7; // 7 days

function b64urlEncode(buf) {
  return Buffer.from(buf).toString("base64")
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function b64urlDecode(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  return Buffer.from(s, "base64").toString("utf8");
}

// Generic HMAC-signed `purpose.id.exp.mac` token.
// `purpose` namespaces the token type so an unsubscribe token can't be
// replayed as a verify token (and vice versa). Verifier rejects on
// purpose mismatch, expired exp, or bad MAC.
function signToken(purpose, id, ttlSeconds) {
  const exp = Math.floor(Date.now() / 1000) + ttlSeconds;
  const payload = `${purpose}.${id}.${exp}`;
  const mac = crypto.createHmac("sha256", VERIFY_SECRET).update(payload).digest("hex").slice(0, 32);
  return b64urlEncode(`${payload}.${mac}`);
}
function verifyToken(purpose, token) {
  let raw;
  try { raw = b64urlDecode(token); }
  catch { return null; }
  const parts = raw.split(".");
  if (parts.length !== 4) return null;
  const [pur, idStr, expStr, mac] = parts;
  if (pur !== purpose) return null;
  const id = parseInt(idStr, 10);
  const exp = parseInt(expStr, 10);
  if (!Number.isFinite(id) || !Number.isFinite(exp)) return null;
  if (Math.floor(Date.now() / 1000) > exp) return null;
  const expectMac = crypto.createHmac("sha256", VERIFY_SECRET)
    .update(`${pur}.${id}.${exp}`).digest("hex").slice(0, 32);
  const a = Buffer.from(mac);
  const b = Buffer.from(expectMac);
  if (a.length !== b.length) return null;
  if (!crypto.timingSafeEqual(a, b)) return null;
  return { id, exp };
}

// Verify token kept as a thin wrapper for clarity at call sites.
function signVerifyToken(id) { return signToken("v", id, VERIFY_TTL_SECONDS); }
function verifyVerifyToken(token) { return verifyToken("v", token); }

const UNSUB_TTL_SECONDS = 60 * 60 * 24 * 365; // 1 year — old emails should still unsub
function signUnsubToken(id) { return signToken("u", id, UNSUB_TTL_SECONDS); }
function verifyUnsubToken(token) { return verifyToken("u", token); }

// ---------- Newsletter template ----------------------------------------
// Inline-styled HTML — email clients drop external stylesheets, so every
// rule lives on the element. Stays under ~600px for Gmail / Apple Mail
// rendering. System fonts only — no Geist webfont, since email clients
// fall back unpredictably for custom faces.
function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function newsletterHighlights(entry) {
  // Lead with the new features (more exciting); if there are fewer than 3,
  // pad with fixes. Cap at 3 bullets so the email stays scannable.
  const news = entry.body?.newFeatures || [];
  const fixed = entry.body?.fixed || [];
  return [...news, ...fixed].slice(0, 3);
}

function buildNewsletterHtml(entry, { readMoreUrl, unsubUrl, browserUrl }) {
  const highlights = newsletterHighlights(entry);
  const bullets = highlights.map((s) =>
    `<li style="margin:0 0 10px;line-height:1.55;color:#0a0a0a;font-size:15px">${escapeHtml(s)}</li>`
  ).join("");
  return `<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(entry.title)}</title></head>
<body style="margin:0;padding:0;background:#f7f7f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0a0a0a">
  <div style="max-width:560px;margin:0 auto;padding:32px 20px 24px">
    <div style="text-align:center;margin-bottom:28px">
      <a href="https://neuthek.com/" style="font-size:18px;font-weight:600;color:#0a0a0a;text-decoration:none;letter-spacing:-0.01em">neuthek</a>
    </div>
    <div style="background:#ffffff;border:1px solid #ececec;border-radius:14px;padding:28px 26px">
      <div style="font-size:11px;font-weight:500;letter-spacing:0.12em;text-transform:uppercase;color:#8a8a8a;margin-bottom:14px">
        ${escapeHtml(entry.week)} &middot; Weekly update
      </div>
      <h1 style="margin:0 0 14px;font-size:22px;line-height:1.25;font-weight:600;letter-spacing:-0.015em;color:#0a0a0a">
        ${escapeHtml(entry.title)}
      </h1>
      <p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:#525252">
        ${escapeHtml(entry.summary)}
      </p>
      ${highlights.length > 0 ? `
      <div style="font-size:11px;font-weight:500;letter-spacing:0.1em;text-transform:uppercase;color:#8a8a8a;margin:0 0 10px">
        What shipped this week
      </div>
      <ul style="margin:0 0 24px;padding-left:18px">${bullets}</ul>
      ` : ""}
      <div style="text-align:left;margin:8px 0 4px">
        <a href="${escapeHtml(readMoreUrl)}" style="display:inline-block;background:#0a0a0a;color:#ffffff;padding:12px 22px;border-radius:999px;font-size:14px;font-weight:500;text-decoration:none">
          Read the full update &rarr;
        </a>
      </div>
    </div>
    <div style="text-align:center;margin-top:22px;font-size:12px;color:#8a8a8a;line-height:1.65">
      You're getting this because you opted into neuthek's weekly newsletter
      when you joined the waitlist.<br>
      <a href="${escapeHtml(unsubUrl)}" style="color:#525252;text-decoration:underline">Unsubscribe</a>
      &nbsp;&middot;&nbsp;
      <a href="${escapeHtml(browserUrl)}" style="color:#525252;text-decoration:underline">View in browser</a>
    </div>
  </div>
</body></html>`;
}

function buildNewsletterText(entry, { readMoreUrl, unsubUrl }) {
  const highlights = newsletterHighlights(entry);
  const bullets = highlights.map((s) => `  - ${s}`).join("\n");
  return [
    `neuthek weekly update`,
    `${entry.week}`,
    ``,
    entry.title,
    ``,
    entry.summary,
    ``,
    highlights.length > 0 ? `What shipped this week:` : "",
    bullets,
    ``,
    `Read the full update: ${readMoreUrl}`,
    ``,
    `---`,
    `You're getting this because you opted into neuthek's weekly newsletter`,
    `when you joined the waitlist.`,
    `Unsubscribe: ${unsubUrl}`,
  ].filter(Boolean).join("\n");
}

async function sendNewsletterEmail({ to, subject, html, text, unsubUrl }) {
  const key = process.env.RESEND_API_KEY;
  const from = process.env.RESEND_FROM || "neuthek <noreply@neuthek.com>";
  if (!key) {
    console.log(`[newsletter] would send to ${to} (no RESEND_API_KEY) — unsub: ${unsubUrl}`);
    return { sent: false, reason: "no-resend-key" };
  }
  try {
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      // RFC 8058: List-Unsubscribe + List-Unsubscribe-Post tell Gmail /
      // Apple Mail to render a native "Unsubscribe" affordance and
      // enable one-click compliance, which Gmail now penalises bulk
      // senders for missing. Resend's `headers` field passes them
      // through verbatim.
      body: JSON.stringify({
        from,
        to,
        subject,
        html,
        text,
        headers: {
          "List-Unsubscribe": `<${unsubUrl}>, <mailto:unsubscribe@neuthek.com?subject=unsubscribe>`,
          "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
      }),
    });
    if (!res.ok) {
      const body = await res.text();
      console.error(`[newsletter] Resend rejected for ${to}: ${res.status} ${body}`);
      return { sent: false, reason: `resend-${res.status}` };
    }
    return { sent: true };
  } catch (err) {
    console.error(`[newsletter] Resend network error for ${to}`, err);
    return { sent: false, reason: "resend-network" };
  }
}

// Resend HTTP API — no extra dependencies, just fetch(). Set RESEND_API_KEY
// + RESEND_FROM in env to wire real email. Without a key we log the URL
// to the console so local dev still works.
async function sendVerifyEmail({ to, verifyUrl }) {
  const key = process.env.RESEND_API_KEY;
  const from = process.env.RESEND_FROM || "neuthek <noreply@neuthek.com>";
  if (!key) {
    console.log(`[waitlist] verify link for ${to}: ${verifyUrl}`);
    return { sent: false, reason: "no-resend-key" };
  }
  try {
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from,
        to,
        subject: "Confirm your neuthek waitlist signup",
        html: `<!doctype html><html><body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.55;color:#0a0a0a;max-width:560px;margin:0 auto;padding:24px;">
          <h2 style="margin:0 0 12px;font-weight:600">Confirm your email</h2>
          <p style="margin:0 0 16px;color:#525252">Thanks for joining the neuthek waitlist. Click the button below to confirm this email address — that way we know you actually own it and can reach you when the hosted version opens.</p>
          <p style="margin:0 0 20px"><a href="${verifyUrl}" style="display:inline-block;background:#0a0a0a;color:#fff;padding:11px 20px;border-radius:8px;text-decoration:none;font-weight:500">Confirm my email</a></p>
          <p style="margin:0 0 8px;color:#525252;font-size:13px">Or paste this link in your browser:</p>
          <p style="margin:0 0 24px;color:#525252;font-size:13px;word-break:break-all"><a href="${verifyUrl}" style="color:#0a0a0a">${verifyUrl}</a></p>
          <p style="margin:0;color:#8a8a8a;font-size:12px">The link expires in 7 days. If you didn't sign up, you can ignore this email.</p>
        </body></html>`,
        text: `Confirm your neuthek waitlist signup:\n\n${verifyUrl}\n\nThe link expires in 7 days. If you didn't sign up, you can ignore this email.`,
      }),
    });
    if (!res.ok) {
      const body = await res.text();
      console.error(`[waitlist] Resend rejected: ${res.status} ${body}`);
      return { sent: false, reason: `resend-${res.status}` };
    }
    return { sent: true };
  } catch (err) {
    console.error("[waitlist] Resend network error", err);
    return { sent: false, reason: "resend-network" };
  }
}

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
          verified     BOOLEAN NOT NULL DEFAULT false,
          verified_at  TIMESTAMPTZ,
          verify_sent_at TIMESTAMPTZ,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
      `);
      // Forward-migrate older deployments. ADD COLUMN IF NOT EXISTS is idempotent.
      await pool.query(`ALTER TABLE waitlist_signups ADD COLUMN IF NOT EXISTS newsletter_opt_in BOOLEAN NOT NULL DEFAULT false`);
      await pool.query(`ALTER TABLE waitlist_signups ADD COLUMN IF NOT EXISTS newsletter_consent_at TIMESTAMPTZ`);
      await pool.query(`ALTER TABLE waitlist_signups ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT false`);
      await pool.query(`ALTER TABLE waitlist_signups ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ`);
      await pool.query(`ALTER TABLE waitlist_signups ADD COLUMN IF NOT EXISTS verify_sent_at TIMESTAMPTZ`);
      await pool.query(`ALTER TABLE waitlist_signups ADD COLUMN IF NOT EXISTS unsubscribed_at TIMESTAMPTZ`);
      await pool.query(`
        CREATE INDEX IF NOT EXISTS ix_waitlist_signups_created_at_desc
        ON waitlist_signups (created_at DESC)
      `);
      // Per-recipient audit + dedupe for newsletter sends. UNIQUE
      // (signup_id, slug) means re-running a send for the same week
      // skips already-delivered rows without bookkeeping in the API.
      await pool.query(`
        CREATE TABLE IF NOT EXISTS newsletter_sends (
          id          BIGSERIAL PRIMARY KEY,
          signup_id   BIGINT NOT NULL REFERENCES waitlist_signups(id) ON DELETE CASCADE,
          slug        TEXT NOT NULL,
          sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          status      TEXT NOT NULL DEFAULT 'sent',
          error       TEXT,
          UNIQUE (signup_id, slug)
        )
      `);
      await pool.query(`
        CREATE INDEX IF NOT EXISTS ix_newsletter_sends_slug ON newsletter_sends (slug)
      `);
    },
    async upsertSignup({ email, use_case, ip, user_agent, newsletter_opt_in }) {
      const flag = !!newsletter_opt_in;
      // INSERT-then-SELECT pattern so we can return the row id for the
      // verify-token signer. ON CONFLICT updates fields but keeps the
      // verified flag — re-signups don't un-verify a confirmed email.
      const { rows } = await pool.query(
        `INSERT INTO waitlist_signups
           (email, use_case, ip, user_agent, newsletter_opt_in, newsletter_consent_at, verify_sent_at)
         VALUES ($1, $2, $3, $4, $5, CASE WHEN $5 THEN now() ELSE NULL END, now())
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
               verify_sent_at = now(),
               created_at = now()
         RETURNING id, verified`,
        [email, use_case, ip || null, user_agent || null, flag]
      );
      return rows[0];
    },
    async listSignups(limit = 500) {
      const { rows } = await pool.query(
        `SELECT id, email, use_case, source, ip, user_agent,
                notified, notified_at,
                newsletter_opt_in, newsletter_consent_at, unsubscribed_at,
                verified, verified_at, verify_sent_at,
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
                   notified, notified_at,
                   newsletter_opt_in, newsletter_consent_at,
                   verified, verified_at, verify_sent_at,
                   created_at`,
        [id]
      );
      return rows[0] || null;
    },
    async markVerified(id) {
      const { rows } = await pool.query(
        `UPDATE waitlist_signups
         SET verified = true, verified_at = COALESCE(verified_at, now())
         WHERE id = $1
         RETURNING id, email, verified, verified_at`,
        [id]
      );
      return rows[0] || null;
    },
    async findById(id) {
      const { rows } = await pool.query(
        `SELECT id, email, verified, verify_sent_at FROM waitlist_signups WHERE id = $1`,
        [id]
      );
      return rows[0] || null;
    },
    async findByEmail(email) {
      const { rows } = await pool.query(
        `SELECT id, email, verified, verify_sent_at FROM waitlist_signups WHERE email = $1`,
        [email]
      );
      return rows[0] || null;
    },
    async stampVerifySent(id) {
      await pool.query(`UPDATE waitlist_signups SET verify_sent_at = now() WHERE id = $1`, [id]);
    },
    async markUnsubscribed(id) {
      const { rows } = await pool.query(
        `UPDATE waitlist_signups
         SET unsubscribed_at = COALESCE(unsubscribed_at, now()),
             newsletter_opt_in = false
         WHERE id = $1
         RETURNING id, email, unsubscribed_at`,
        [id]
      );
      return rows[0] || null;
    },
    async listNewsletterRecipients(slug) {
      // Eligible recipients: verified, opted-in, not unsubscribed, and
      // not already sent this slug (the LEFT JOIN + NULL filter is the
      // dedupe). Returns minimal payload — just what the sender needs.
      const { rows } = await pool.query(
        `SELECT w.id, w.email
         FROM waitlist_signups w
         LEFT JOIN newsletter_sends s ON s.signup_id = w.id AND s.slug = $1
         WHERE w.verified = true
           AND w.newsletter_opt_in = true
           AND w.unsubscribed_at IS NULL
           AND s.id IS NULL
         ORDER BY w.created_at ASC`,
        [slug]
      );
      return rows;
    },
    async recordNewsletterSend({ signupId, slug, status, error }) {
      // UNIQUE (signup_id, slug) means a race that double-attempts a
      // send will fail-quiet on the second insert — the constraint
      // does the dedupe. ON CONFLICT DO NOTHING keeps the call idempotent.
      await pool.query(
        `INSERT INTO newsletter_sends (signup_id, slug, status, error)
         VALUES ($1, $2, $3, $4)
         ON CONFLICT (signup_id, slug) DO NOTHING`,
        [signupId, slug, status, error || null]
      );
    },
    async newsletterSendStats(slug) {
      const { rows } = await pool.query(
        `SELECT
           COUNT(*) FILTER (WHERE status = 'sent')   AS sent,
           COUNT(*) FILTER (WHERE status = 'failed') AS failed
         FROM newsletter_sends WHERE slug = $1`,
        [slug]
      );
      return { sent: Number(rows[0].sent), failed: Number(rows[0].failed) };
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
          verified     INTEGER NOT NULL DEFAULT 0,
          verified_at  TEXT,
          verify_sent_at TEXT,
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
      if (!names.has("verified")) {
        db.exec("ALTER TABLE waitlist_signups ADD COLUMN verified INTEGER NOT NULL DEFAULT 0");
      }
      if (!names.has("verified_at")) {
        db.exec("ALTER TABLE waitlist_signups ADD COLUMN verified_at TEXT");
      }
      if (!names.has("verify_sent_at")) {
        db.exec("ALTER TABLE waitlist_signups ADD COLUMN verify_sent_at TEXT");
      }
      if (!names.has("unsubscribed_at")) {
        db.exec("ALTER TABLE waitlist_signups ADD COLUMN unsubscribed_at TEXT");
      }
      db.exec(`
        CREATE TABLE IF NOT EXISTS newsletter_sends (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          signup_id   INTEGER NOT NULL REFERENCES waitlist_signups(id) ON DELETE CASCADE,
          slug        TEXT NOT NULL,
          sent_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          status      TEXT NOT NULL DEFAULT 'sent',
          error       TEXT,
          UNIQUE (signup_id, slug)
        );
        CREATE INDEX IF NOT EXISTS ix_newsletter_sends_slug ON newsletter_sends (slug);
      `);
    },
    async upsertSignup({ email, use_case, ip, user_agent, newsletter_opt_in }) {
      const flag = newsletter_opt_in ? 1 : 0;
      const now = new Date().toISOString();
      const existing = db.prepare(
        "SELECT id, newsletter_consent_at, verified FROM waitlist_signups WHERE email = ?"
      ).get(email);
      if (existing) {
        // First-consent-wins for the timestamp; opting back out clears it.
        let nextStamp;
        if (flag && !existing.newsletter_consent_at) {
          nextStamp = now;
        } else if (!flag) {
          nextStamp = null;
        } else {
          nextStamp = existing.newsletter_consent_at;
        }
        // Note: we DON'T touch the verified flag — re-signing up with the
        // same email doesn't un-verify a confirmed address.
        db.prepare(
          `UPDATE waitlist_signups
           SET use_case = ?,
               newsletter_opt_in = ?,
               newsletter_consent_at = ?,
               verify_sent_at = ?,
               created_at = CURRENT_TIMESTAMP
           WHERE email = ?`
        ).run(use_case, flag, nextStamp, now, email);
        return { id: existing.id, verified: !!existing.verified };
      }
      const info = db.prepare(
        `INSERT INTO waitlist_signups
           (email, use_case, ip, user_agent, newsletter_opt_in, newsletter_consent_at, verify_sent_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`
      ).run(
        email, use_case, ip || null, user_agent || null,
        flag, flag ? now : null, now,
      );
      return { id: Number(info.lastInsertRowid), verified: false };
    },
    async listSignups(limit = 500) {
      const rows = db.prepare(
        `SELECT id, email, use_case, source, ip, user_agent,
                notified, notified_at,
                newsletter_opt_in, newsletter_consent_at, unsubscribed_at,
                verified, verified_at, verify_sent_at,
                created_at
         FROM waitlist_signups
         ORDER BY datetime(created_at) DESC
         LIMIT ?`
      ).all(limit);
      return rows.map((r) => ({
        ...r,
        notified: !!r.notified,
        newsletter_opt_in: !!r.newsletter_opt_in,
        verified: !!r.verified,
      }));
    },
    async markNotified(id) {
      const row = db.prepare(
        `UPDATE waitlist_signups
         SET notified = 1, notified_at = CURRENT_TIMESTAMP
         WHERE id = ?
         RETURNING id, email, use_case, source, ip, user_agent,
                   notified, notified_at,
                   newsletter_opt_in, newsletter_consent_at,
                   verified, verified_at, verify_sent_at,
                   created_at`
      ).get(id);
      return row ? {
        ...row,
        notified: !!row.notified,
        newsletter_opt_in: !!row.newsletter_opt_in,
        verified: !!row.verified,
      } : null;
    },
    async markVerified(id) {
      const row = db.prepare(
        `UPDATE waitlist_signups
         SET verified = 1, verified_at = COALESCE(verified_at, CURRENT_TIMESTAMP)
         WHERE id = ?
         RETURNING id, email, verified, verified_at`
      ).get(id);
      return row ? { ...row, verified: !!row.verified } : null;
    },
    async findById(id) {
      const row = db.prepare(
        `SELECT id, email, verified, verify_sent_at FROM waitlist_signups WHERE id = ?`
      ).get(id);
      return row ? { ...row, verified: !!row.verified } : null;
    },
    async findByEmail(email) {
      const row = db.prepare(
        `SELECT id, email, verified, verify_sent_at FROM waitlist_signups WHERE email = ?`
      ).get(email);
      return row ? { ...row, verified: !!row.verified } : null;
    },
    async stampVerifySent(id) {
      db.prepare(`UPDATE waitlist_signups SET verify_sent_at = ? WHERE id = ?`)
        .run(new Date().toISOString(), id);
    },
    async markUnsubscribed(id) {
      const row = db.prepare(
        `UPDATE waitlist_signups
         SET unsubscribed_at = COALESCE(unsubscribed_at, CURRENT_TIMESTAMP),
             newsletter_opt_in = 0
         WHERE id = ?
         RETURNING id, email, unsubscribed_at`
      ).get(id);
      return row || null;
    },
    async listNewsletterRecipients(slug) {
      const rows = db.prepare(
        `SELECT w.id, w.email
         FROM waitlist_signups w
         LEFT JOIN newsletter_sends s ON s.signup_id = w.id AND s.slug = ?
         WHERE w.verified = 1
           AND w.newsletter_opt_in = 1
           AND w.unsubscribed_at IS NULL
           AND s.id IS NULL
         ORDER BY datetime(w.created_at) ASC`
      ).all(slug);
      return rows;
    },
    async recordNewsletterSend({ signupId, slug, status, error }) {
      db.prepare(
        `INSERT OR IGNORE INTO newsletter_sends (signup_id, slug, status, error)
         VALUES (?, ?, ?, ?)`
      ).run(signupId, slug, status, error || null);
    },
    async newsletterSendStats(slug) {
      const row = db.prepare(
        `SELECT
           SUM(CASE WHEN status = 'sent'   THEN 1 ELSE 0 END) AS sent,
           SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
         FROM newsletter_sends WHERE slug = ?`
      ).get(slug);
      return { sent: Number(row?.sent || 0), failed: Number(row?.failed || 0) };
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
    const row = await store.upsertSignup({
      email,
      use_case,
      ip: ip !== "unknown" ? ip : null,
      user_agent: user_agent || null,
      newsletter_opt_in,
    });
    // Already-verified emails don't need another round-trip — just
    // confirm the (possibly updated) use-case + newsletter pref.
    if (row?.verified) {
      return res.json({
        ok: true,
        already_signed_up: false,
        already_verified: true,
      });
    }
    const token = signVerifyToken(row.id);
    const verifyUrl = `${PUBLIC_ORIGIN}/waitlist/verify?token=${encodeURIComponent(token)}`;
    const send = await sendVerifyEmail({ to: email, verifyUrl });
    // In non-production environments, also expose the URL in the JSON
    // response so the test client can click through without a real
    // mailer. Production responses NEVER include the URL — that would
    // be an enumeration / takeover vector.
    const debug = process.env.NODE_ENV !== "production" && !send.sent
      ? { verify_url: verifyUrl }
      : {};
    return res.json({
      ok: true,
      already_signed_up: false,
      already_verified: false,
      verification_email_sent: !!send.sent,
      ...debug,
    });
  } catch (err) {
    console.error("[waitlist] signup error", err);
    return res.status(500).json({ ok: false, detail: "signup failed" });
  }
});

// ----- Verify endpoint (clicked from the email link) -----
// The SPA at /waitlist/verify will call this and show a result page.
app.get("/api/waitlist/verify", async (req, res) => {
  const token = String(req.query.token || "");
  if (!token) return res.status(400).json({ ok: false, detail: "missing token" });
  const claims = verifyVerifyToken(token);
  if (!claims) {
    return res.status(400).json({ ok: false, detail: "invalid or expired token" });
  }
  try {
    const row = await store.markVerified(claims.id);
    if (!row) return res.status(404).json({ ok: false, detail: "signup not found" });
    return res.json({ ok: true, email: row.email, verified_at: row.verified_at });
  } catch (err) {
    console.error("[waitlist] verify error", err);
    return res.status(500).json({ ok: false, detail: "verify failed" });
  }
});

// ----- Resend verification link -----
// Public endpoint, rate-limited per IP and email. We respond
// success-shaped whether or not the email exists so an attacker
// can't enumerate signups.
app.post("/api/waitlist/resend", async (req, res) => {
  const ip = clientIp(req);
  if (!rateLimit({ key: `resend:${ip}`, limit: 5, windowMs: 60_000 })) {
    return res.status(429).json({ ok: false, detail: "too many resend attempts" });
  }
  const email = String(req.body?.email || "").trim().toLowerCase();
  if (!EMAIL_RE.test(email) || email.length > 254) {
    return res.status(422).json({ ok: false, detail: "invalid email" });
  }
  if (!rateLimit({ key: `resend-email:${email}`, limit: 3, windowMs: 5 * 60_000 })) {
    return res.status(429).json({ ok: false, detail: "wait a few minutes before requesting another link" });
  }
  try {
    const existing = await store.findByEmail(email);
    if (!existing || existing.verified) {
      return res.json({ ok: true });
    }
    const token = signVerifyToken(existing.id);
    const verifyUrl = `${PUBLIC_ORIGIN}/waitlist/verify?token=${encodeURIComponent(token)}`;
    await sendVerifyEmail({ to: email, verifyUrl });
    await store.stampVerifySent(existing.id);
    return res.json({ ok: true });
  } catch (err) {
    console.error("[waitlist] resend error", err);
    return res.status(500).json({ ok: false, detail: "resend failed" });
  }
});

// ----- Unsubscribe (one-click, RFC 8058) -----
// Accepts BOTH GET (user clicks email link → friendly /unsubscribe SPA
// page) and POST (Gmail / Apple Mail one-click compliance). GET
// triggers the same unsubscribe action so the SPA page can confirm
// without a second round-trip; POST returns 204 No Content per the spec.
async function handleUnsubscribe(req, res) {
  const token = String(req.query?.token || req.body?.token || "");
  if (!token) return res.status(400).json({ ok: false, detail: "missing token" });
  const claims = verifyUnsubToken(token);
  if (!claims) return res.status(400).json({ ok: false, detail: "invalid or expired token" });
  try {
    const row = await store.markUnsubscribed(claims.id);
    if (!row) return res.status(404).json({ ok: false, detail: "signup not found" });
    if (req.method === "POST") return res.status(204).end();
    return res.json({ ok: true, email: row.email, unsubscribed_at: row.unsubscribed_at });
  } catch (err) {
    console.error("[unsubscribe] error", err);
    return res.status(500).json({ ok: false, detail: "unsubscribe failed" });
  }
}
app.get("/api/waitlist/unsubscribe", handleUnsubscribe);
// Accept POST with either JSON body or `application/x-www-form-urlencoded`
// (Gmail sends `application/x-www-form-urlencoded` with body
// `List-Unsubscribe=One-Click` plus the token from the URL).
app.post(
  "/api/waitlist/unsubscribe",
  express.urlencoded({ extended: false }),
  handleUnsubscribe
);

// ----- Admin: send newsletter for a specific weekly update -----
app.post("/api/admin/newsletter/send", adminAuth, async (req, res) => {
  const slug = String(req.body?.slug || "").trim();
  if (!slug) return res.status(400).json({ ok: false, detail: "missing slug" });

  // Look up the update entry. The index is loaded at server boot from
  // src/data/updates-index.json; an unknown slug is a 404 (operator
  // typo or stale index).
  const entry = UPDATE_INDEX.find((u) => u.slug === slug);
  if (!entry) return res.status(404).json({ ok: false, detail: "unknown update slug" });

  let recipients;
  try { recipients = await store.listNewsletterRecipients(slug); }
  catch (err) {
    console.error("[newsletter] recipient lookup failed", err);
    return res.status(500).json({ ok: false, detail: "recipient lookup failed" });
  }

  if (recipients.length === 0) {
    return res.json({ ok: true, total: 0, sent: 0, failed: 0, recipients: [] });
  }

  const readMoreUrl = `${PUBLIC_ORIGIN}/updates/${slug}`;
  const subject = `${entry.title.length > 70
    ? entry.title.slice(0, 67) + "..."
    : entry.title}`;

  // Sequential send with a small await between calls. Resend's free tier
  // limits to 10 req/sec; we stay well under by sleeping 150ms between
  // sends. For larger lists this could be migrated to /emails/batch
  // (Resend's bulk endpoint, up to 100 per request).
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let sentCount = 0;
  let failedCount = 0;
  const failedEmails = [];

  for (const r of recipients) {
    const unsubToken = signUnsubToken(r.id);
    const unsubUrl = `${PUBLIC_ORIGIN}/api/waitlist/unsubscribe?token=${encodeURIComponent(unsubToken)}`;
    const browserUrl = readMoreUrl;
    const html = buildNewsletterHtml(entry, { readMoreUrl, unsubUrl, browserUrl });
    const text = buildNewsletterText(entry, { readMoreUrl, unsubUrl });
    const result = await sendNewsletterEmail({
      to: r.email,
      subject,
      html,
      text,
      unsubUrl,
    });
    const status = result.sent ? "sent" : "failed";
    if (result.sent) sentCount++;
    else {
      failedCount++;
      failedEmails.push({ email: r.email, reason: result.reason });
    }
    try {
      await store.recordNewsletterSend({
        signupId: r.id,
        slug,
        status,
        error: result.sent ? null : result.reason || "unknown",
      });
    } catch (err) {
      console.error(`[newsletter] log insert failed for ${r.email}`, err);
    }
    await sleep(150);
  }

  return res.json({
    ok: true,
    slug,
    total: recipients.length,
    sent: sentCount,
    failed: failedCount,
    failures: failedEmails,
    resend_configured: !!process.env.RESEND_API_KEY,
  });
});

// ----- Admin: preview newsletter HTML for a slug -----
// Returns the same HTML body the broadcast would send, with a sample
// unsubscribe URL. Useful for "does this look right" QA before
// pulling the trigger on a real send.
app.get("/api/admin/newsletter/preview", adminAuth, async (req, res) => {
  const slug = String(req.query?.slug || "").trim();
  if (!slug) return res.status(400).json({ ok: false, detail: "missing slug" });
  const entry = UPDATE_INDEX.find((u) => u.slug === slug);
  if (!entry) return res.status(404).json({ ok: false, detail: "unknown update slug" });
  const sampleToken = signUnsubToken(0); // id=0 is fine; this is a preview
  const unsubUrl = `${PUBLIC_ORIGIN}/api/waitlist/unsubscribe?token=${encodeURIComponent(sampleToken)}`;
  const readMoreUrl = `${PUBLIC_ORIGIN}/updates/${slug}`;
  const html = buildNewsletterHtml(entry, { readMoreUrl, unsubUrl, browserUrl: readMoreUrl });
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.send(html);
});

// ----- Admin: list eligible recipients + previously-sent count for a slug -----
app.get("/api/admin/newsletter/recipients", adminAuth, async (req, res) => {
  const slug = String(req.query?.slug || "").trim();
  if (!slug) return res.status(400).json({ ok: false, detail: "missing slug" });
  const entry = UPDATE_INDEX.find((u) => u.slug === slug);
  if (!entry) return res.status(404).json({ ok: false, detail: "unknown update slug" });
  const recipients = await store.listNewsletterRecipients(slug);
  const stats = await store.newsletterSendStats(slug);
  res.json({
    slug,
    title: entry.title,
    eligible: recipients.length,
    already_sent: stats.sent,
    previous_failures: stats.failed,
  });
});

// ----- Admin: re-send verify for a specific row -----
// `adminAuth` is declared below as a function declaration, so it's
// hoisted into module scope and safe to reference here.
app.post("/api/admin/waitlist/:id/resend-verify", adminAuth, async (req, res) => {
  const id = parseInt(req.params.id, 10);
  if (!Number.isFinite(id) || id <= 0) return res.status(400).json({ ok: false, detail: "bad id" });
  const row = await store.findById(id);
  if (!row) return res.status(404).json({ ok: false, detail: "not found" });
  if (row.verified) return res.json({ ok: true, already_verified: true });
  const token = signVerifyToken(row.id);
  const verifyUrl = `${PUBLIC_ORIGIN}/waitlist/verify?token=${encodeURIComponent(token)}`;
  const send = await sendVerifyEmail({ to: row.email, verifyUrl });
  await store.stampVerifySent(row.id);
  // For admin, always include the URL so the operator can copy-paste it
  // if email delivery is mis-configured.
  return res.json({ ok: true, sent: !!send.sent, verify_url: verifyUrl });
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
    if (reqPath === "/waitlist" || reqPath === "/waitlist/") {
      return {
        title: "Join the neuthek waitlist",
        description:
          "Join the waitlist for neuthek, the AI-aware personal cloud. We email you twice — once when the hosted version opens and once at GA. Email verification required.",
        canonical: "https://neuthek.com/waitlist",
      };
    }
    if (reqPath === "/waitlist/verify" || reqPath === "/waitlist/verify/") {
      return {
        title: "Verify your email — neuthek",
        description: "Confirm your neuthek waitlist signup.",
        canonical: "https://neuthek.com/waitlist/verify",
      };
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
