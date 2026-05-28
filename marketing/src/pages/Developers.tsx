import { Link } from "react-router-dom";
import { useEffect } from "react";

// Each dependency / model is name + a one-line "what" (always shown) +
// a fuller "why" (revealed on demand). Keeping the page scannable by
// default — you read 19 names + one-liners, and expand only what you
// care about — instead of a wall of two-paragraph cards.
type Item = { name: string; what: string; why: string };

const STACK: Item[] = [
  {
    name: "FastAPI + Python 3.12",
    what: "The HTTP layer and every business endpoint — async SQLAlchemy + asyncpg, Alembic migrations.",
    why: "Async by default (no Flask/WSGI greenlet dance), automatic OpenAPI docs at /docs, and Pydantic v2 validation that doubles as the published schema. Python keeps the API on the same runtime as the PyTorch inference workers — no IPC layer between API and models.",
  },
  {
    name: "PostgreSQL 16 + pgvector",
    what: "Source of truth for every row, plus the cosine-similarity index over CLIP + face embeddings.",
    why: "Vectors and ACID in one transaction — no separate Pinecone or Qdrant to keep in sync with the row that owns the embedding. Deleting a row deletes its vector atomically. And FORCE Row-Level Security per user means a bug in an app handler still can't return another user's row.",
  },
  {
    name: "Redis 7",
    what: "Per-user ML job queues (summarize, face-scan, vision backfill) and rate-limit counters.",
    why: "The per-user fair scheduler needs cheap atomic lists (LPUSH/BRPOP) and counters (INCR + EXPIRE). Postgres can do both but pays a transaction cost we don't need for ephemeral state — single-purpose, single-process, trivially backed up by skipping the backup.",
  },
  {
    name: "MinIO (S3 API)",
    what: "Object storage for originals, compressed variants, and face crops — SSE-S3/KMS encrypted at rest.",
    why: "The S3 wire protocol lets us swap MinIO for real AWS / R2 / Backblaze without changing a line of application code. MinIO itself runs anywhere — a NUC, a Raspberry Pi, or a Kubernetes cluster — same binary.",
  },
  {
    name: "Caddy (TLS edge)",
    what: "Reverse proxy + automatic Let's Encrypt for TLS, HSTS, HTTP/3, and security headers.",
    why: "nginx + certbot is two tools to configure and a cron job to forget. Caddy is one config file, certs auto-renew, and the defaults are tighter than most hand-rolled nginx blocks — operators don't need to know HTTPS to run the stack.",
  },
  {
    name: "fastapi-users + JWT + TOTP",
    what: "Account creation, password reset, email verification, JWT, TOTP 2FA, and Argon2id hashing.",
    why: "Rolling your own auth is the fastest way to ship a security bug. fastapi-users is production-tested, plugs into FastAPI's dependency system natively, and lets us layer our own consent-bundle hook on the user-create path without forking.",
  },
  {
    name: "Fernet for at-rest secrets",
    what: "Authenticated-encryption envelope for OAuth tokens, TOTP secrets — anything a DB dump shouldn't reveal.",
    why: "Fernet is the boring-correct choice: authenticated encryption, no nonce-reuse footguns, key rotation built in. The key comes from CLOUD_ENCRYPTION_KEY in the operator environment; a boot-time validator refuses to start in prod without it.",
  },
  {
    name: "React 18 + Vite + TanStack Query",
    what: "The SPA — Vite for dev/build, TanStack Query for server state, Zustand for the rest.",
    why: "TanStack Query removes most of the reasons people reach for Redux, Vite's HMR is the fastest dev loop we've used, and React 18's concurrent features (filter-chip transitions, lightbox Suspense) are load-bearing for perceived responsiveness.",
  },
  {
    name: "Prism (~40 grammars)",
    what: "Syntax highlighting for the in-browser code-file preview surface.",
    why: "highlight.js auto-detect misfires on small files. Prism's per-language grammars are explicit, ship as small modules, and we eager-load the 40 that cover almost every source file people store in a personal cloud.",
  },
  {
    name: "Docker Compose",
    what: "One docker compose up -d brings up API, ML worker, Postgres, Redis, MinIO, and Caddy with TLS.",
    why: "Self-host is the headline deployment story. Compose is the lowest-friction way to run a multi-container app on a single host — no Kubernetes, no Helm, no cluster to manage. Optional overlays add TLS, encrypted backups, and GPU passthrough without touching the base file.",
  },
  {
    name: "pytest + pytest-asyncio",
    what: "End-to-end suite over uploads, codec dispatch, RLS, consent gates, auth, deletion, and migrations.",
    why: "We lean on tests to prove security properties: one uploads + deletes + asserts 0 rows + 0 objects; another reads another tenant's data with RLS active and asserts the empty result; and there's a test for every hygiene contract (no real PII in fixtures, gitleaks over full history).",
  },
  {
    name: "Stripe (Embedded Checkout)",
    what: "Free / Pro / Business tiers via hosted checkout + signed webhooks + Customer Portal.",
    why: "Embedded Checkout means we never see a card number or take on PCI scope. Empty Stripe env vars short-circuit /billing/* to 503 in dev, so the operator never has to wire it up to run self-host.",
  },
];

const MODELS: Item[] = [
  {
    name: "OpenCLIP ViT-L-14",
    what: "Embeds every image into a 768-dim vector; query text embeds into the same space for cosine ranking.",
    why: "OpenAI's original CLIP is closed and never got a proper open release. OpenCLIP is the open re-implementation trained on LAION-2B — same architecture, openly licensed weights, stronger benchmarks. ViT-L-14 is the accuracy/cost sweet spot on a single consumer GPU; larger variants gain ~3-5% recall at 3-4× the cost.",
  },
  {
    name: "Florence-2-large",
    what: "Generates the detailed caption for every image, plus scene-gated inline OCR.",
    why: "vs. BLIP-2 or LLaVA: Florence-2 (Microsoft Research) is purpose-built for vision — caption, detect, segment, OCR in one model — runs on a mid-tier GPU, and produces far more concrete captions (“auth-flow review on a whiteboard” vs. “a person near a whiteboard”). Apache-2.0 and small enough for 8-bit/4-bit quant.",
  },
  {
    name: "Qwen2.5-Instruct",
    what: "Rewrites raw captions into readable summaries, splices scene labels, summarizes documents via map-reduce.",
    why: "Llama-3.2-Instruct was the runner-up; Qwen2.5 was consistently better on instruction-following at the same parameter count, ships in 0.5B–7B variants so operators pick by hardware, and is Apache-2.0. The 1.5B variant runs at acceptable latency on CPU; the 4-bit GGUF path is well-trodden.",
  },
  {
    name: "RetinaFace + ArcFace (buffalo_l)",
    what: "Opt-in only: detects faces, embeds each into 512-dim, clusters into Me + people you tag.",
    why: "insightface is the standard reference implementation for both. ArcFace's angular-margin loss outperforms FaceNet's triplet loss on standard benchmarks and stays stable across age + lighting. The buffalo_l bundle ships both models with consistent preprocessing, so we don't wire two pipelines.",
  },
  {
    name: "rawpy + LibRaw",
    what: "Decodes camera RAW (NEF, CR2, ARW, DNG, RAF, ORF, RW2, PEF) into the full sensor image.",
    why: "LibRaw is the open-source standard dcraw evolved into; rawpy is the Python binding. Pillow's RAW support only grabs the embedded preview — we re-encode the full-resolution decode at JPEG q=95 so the gallery shows what your camera actually captured.",
  },
  {
    name: "LinUCB contextual bandit",
    what: "Picks the best codec (WebP/MozJPEG/AVIF/JXL) and quality per image from a 32-dim feature vector.",
    why: "A fixed “JPEG q=85” is mediocre on everything; a neural policy would need per-user training data. LinUCB learns online, converges in dozens of impressions per arm, and stays explainable — you can read the per-arm coefficients and see exactly why it picked AVIF over WebP. Reward feeds back per-user; we never share arm state.",
  },
  {
    name: "WordNet (via NLTK)",
    what: "Expands each search query into related terms before the token-exact FTS pass over summaries.",
    why: "CLIP already handles most of the semantic stretch, but the FTS pass is token-exact and benefits from explicit synonym expansion. WordNet is offline (no API), tiny (~10 MB), and exhaustive for English — plus a small visual-domain overlay for terms it doesn't link in the photographer sense.",
  },
];

function DevCard({ item }: { item: Item }) {
  return (
    <details className="dev-card">
      <summary className="dev-card__summary">
        <span className="dev-card__name">{item.name}</span>
        <span className="dev-card__what">{item.what}</span>
        <span className="dev-card__more">
          Why we chose it
          <svg width="11" height="11" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="4 6 7 9 10 6" /></svg>
        </span>
      </summary>
      <p className="dev-card__why">{item.why}</p>
    </details>
  );
}

// API routes rendered as a clean, structured table (method badge · path ·
// note) grouped by area — not a clipping monospace terminal dump.
type Method = "GET" | "POST" | "PATCH" | "DELETE";
type Route = { m: Method; path: string; note: string };
const ROUTE_GROUPS: { group: string; routes: Route[] }[] = [
  { group: "Auth", routes: [
    { m: "POST", path: "/auth/jwt/login", note: "JWT login (TOTP-gated if enabled)" },
    { m: "POST", path: "/auth/jwt/login-totp", note: "2-factor follow-up" },
    { m: "POST", path: "/auth/register", note: "account create" },
    { m: "GET", path: "/auth/google/login", note: "Google SSO start" },
    { m: "GET", path: "/users/me", note: "current user + linked identities" },
  ]},
  { group: "Account & security", routes: [
    { m: "POST", path: "/account/totp/enroll", note: "2FA setup" },
    { m: "POST", path: "/account/totp/codes", note: "regen recovery codes" },
    { m: "POST", path: "/account/google/link", note: "attach Google to existing account" },
    { m: "GET", path: "/account/trash", note: "soft-deleted rows" },
    { m: "POST", path: "/account/export", note: "portable ZIP (rate-limited)" },
    { m: "POST", path: "/account/delete", note: "hard delete (every byte)" },
  ]},
  { group: "Images", routes: [
    { m: "POST", path: "/images/", note: "upload" },
    { m: "GET", path: "/images/", note: "list — scene, type, faces, gps, person, folder, starred, tag" },
    { m: "GET", path: "/images/facets", note: "filter chip options + counts" },
    { m: "GET", path: "/images/{id}/original", note: "original bytes" },
    { m: "GET", path: "/images/{id}/served", note: "compressed (?max_dim=N for thumbs)" },
    { m: "POST", path: "/images/{id}/star", note: "toggle favorite" },
    { m: "POST", path: "/images/{id}/resummarize", note: "force re-caption" },
    { m: "PATCH", path: "/images/{id}/name", note: "rename" },
    { m: "DELETE", path: "/images/{id}", note: "soft delete (?purge=true → hard)" },
    { m: "POST", path: "/images/best-of", note: "rank N selected" },
  ]},
  { group: "Search", routes: [
    { m: "GET", path: "/search/?q=<text>", note: "hybrid CLIP + FTS + WordNet" },
  ]},
  { group: "Cloud sync", routes: [
    { m: "POST", path: "/cloud/{src}/connect", note: "OAuth / credentials start" },
    { m: "POST", path: "/cloud/{src}/sync", note: "manual sweep" },
  ]},
  { group: "Billing · Health", routes: [
    { m: "POST", path: "/billing/checkout", note: "Stripe Embedded Checkout" },
    { m: "GET", path: "/health", note: "liveness + DB ping" },
  ]},
];

function RoutesTable() {
  return (
    <div className="routes">
      {ROUTE_GROUPS.map((g) => (
        <div className="routes__group" key={g.group}>
          <div className="routes__label">{g.group}</div>
          {g.routes.map((r) => (
            <div className="route-row" key={r.m + r.path}>
              <span className={`route-m route-m--${r.m.toLowerCase()}`}>{r.m}</span>
              <code className="route-path">{r.path}</code>
              <span className="route-note">{r.note}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

export default function Developers() {
  useEffect(() => {
    document.title = "Developers — neuthek stack & model choices";
    setMeta(
      "description",
      "Developer documentation for neuthek: the dependencies we picked and why (FastAPI, PostgreSQL + pgvector, Redis, MinIO, Caddy), the AI/ML models we run and why (OpenCLIP ViT-L-14, Florence-2-large, Qwen2.5, RetinaFace + ArcFace, rawpy/LibRaw, LinUCB bandit), and the public API surface."
    );
    setLink("canonical", "https://neuthek.com/developers");
  }, []);

  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Developers</span>
          <h1>What we built it with, and why.</h1>
          <p className="lead">
            Every dependency and model below is real — in our development
            tree today. Each shows what it does at a glance; tap{" "}
            <em>Why we chose it</em> for the reasoning over the obvious
            alternative. The source isn't public yet — we're cleaning up the
            tree before release.
          </p>
        </div>
      </section>

      {/* ===================== Stack ===================== */}
      <section className="section">
        <div className="container">
          <span className="eyebrow">The stack</span>
          <h2>What it runs on.</h2>
          <div className="dev-grid">
            {STACK.map((s) => <DevCard key={s.name} item={s} />)}
          </div>
        </div>
      </section>

      {/* ===================== Models ===================== */}
      <section className="section">
        <div className="container">
          <span className="eyebrow">Models we chose</span>
          <h2>The AI side.</h2>
          <p className="lead" style={{ marginTop: 12, maxWidth: 680 }}>
            Every model is pre-trained with frozen weights —{" "}
            <strong>we never fine-tune anything on your library</strong>.
          </p>
          <div className="dev-grid" style={{ marginTop: 24 }}>
            {MODELS.map((m) => <DevCard key={m.name} item={m} />)}
          </div>
        </div>
      </section>

      {/* ===================== API surface ===================== */}
      <section className="section">
        <div className="container">
          <span className="eyebrow">API surface</span>
          <h2>Small, predictable, documented.</h2>
          <p className="lead" style={{ marginTop: 12, maxWidth: 720 }}>
            FastAPI ships interactive OpenAPI docs at <code>/docs</code>. Every
            route is gated by JWT and scoped to the authenticated user by
            Postgres FORCE Row-Level Security — even a bug in an app handler
            can't return another user's row.
          </p>

          <div className="routes-note">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            <div>
              <strong>This is the self-host engine surface.</strong> These are
              the routes your own instance exposes when you run neuthek on your
              hardware — a home server or NAS. The hosted neuthek service runs
              the same engine behind additional gateway guards (WAF, stricter
              rate limits, bot protection), so these endpoints aren't reachable
              directly there. Shapes may gain fields but won't lose them without
              a release-note migration.
            </div>
          </div>

          <RoutesTable />
        </div>
      </section>

      {/* ===================== Pre-launch contribute ===================== */}
      <section className="section section--ink">
        <div className="container">
          <h2>Want to contribute when it opens?</h2>
          <p style={{ marginTop: 12, maxWidth: 640 }}>
            When source is published, pull requests will be welcome.
            We aim to keep changes small, prefer adding tests
            alongside behavior changes, and call out privacy impact
            in every PR that touches user data, embeddings, or face
            workflows.
          </p>
          <p style={{ marginTop: 24 }}>
            <Link
              to="/waitlist"
              className="btn btn--ghost btn--lg"
              style={{ borderColor: "rgba(255,255,255,0.3)", color: "var(--surface)" }}
            >
              Get notified at release
            </Link>
          </p>
          <p style={{ marginTop: 12, fontSize: 13, color: "rgba(255,255,255,0.5)" }}>
            The repository URL will appear here once the source is published.
          </p>
        </div>
      </section>
    </>
  );
}

function setMeta(name: string, content: string) {
  let el = document.querySelector<HTMLMetaElement>(`meta[name="${name}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute("name", name);
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);
}

function setLink(rel: string, href: string) {
  let el = document.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`);
  if (!el) {
    el = document.createElement("link");
    el.setAttribute("rel", rel);
    document.head.appendChild(el);
  }
  el.setAttribute("href", href);
}
