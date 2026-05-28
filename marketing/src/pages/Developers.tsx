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
        <div className="container split">
          <div>
            <span className="eyebrow">API surface</span>
            <h2>Small, predictable, documented.</h2>
            <p style={{ marginTop: 16 }}>
              FastAPI ships interactive OpenAPI docs at
              <code> /docs</code>. Every route below is gated by JWT
              and scoped to the authenticated user by Postgres FORCE
              Row-Level Security at the DB layer — even a bug in the
              app handler can't return another user's row.
            </p>
            <p style={{ marginTop: 12, color: "var(--ink-3)" }}>
              The endpoint shapes are what the engine exposes today.
              The published API may add fields but won't remove them
              without a release-note migration.
            </p>
          </div>
          <div className="code-card">
            <div className="code-card__chrome">
              <span className="code-card__dots"><span/><span/><span/></span>
              <span className="code-card__title">routes — engine API surface</span>
              <span className="code-card__lang">http</span>
            </div>
            <pre className="code"><span className="tok-c"># Auth</span>{`
`}<span className="tok-k">POST</span>   /auth/jwt/login              <span className="tok-c"># JWT login (TOTP-gated if enabled)</span>{`
`}<span className="tok-k">POST</span>   /auth/jwt/login-totp         <span className="tok-c"># 2-factor follow-up</span>{`
`}<span className="tok-k">POST</span>   /auth/register               <span className="tok-c"># account create</span>{`
`}<span className="tok-k">GET</span>    /auth/google/login           <span className="tok-c"># Google SSO start</span>{`
`}<span className="tok-k">GET</span>    /users/me                    <span className="tok-c"># current user + linked identities</span>{`

`}<span className="tok-c"># Account & security</span>{`
`}<span className="tok-k">POST</span>   /account/totp/enroll         <span className="tok-c"># 2FA setup</span>{`
`}<span className="tok-k">POST</span>   /account/totp/codes          <span className="tok-c"># regen recovery codes</span>{`
`}<span className="tok-k">POST</span>   /account/google/link         <span className="tok-c"># attach Google to existing account</span>{`
`}<span className="tok-k">GET</span>    /account/trash               <span className="tok-c"># soft-deleted rows</span>{`
`}<span className="tok-k">POST</span>   /account/export              <span className="tok-c"># portable ZIP (rate-limited)</span>{`
`}<span className="tok-k">POST</span>   /account/delete              <span className="tok-c"># hard delete (every byte)</span>{`

`}<span className="tok-c"># Images</span>{`
`}<span className="tok-k">POST</span>   /images/                     <span className="tok-c"># upload</span>{`
`}<span className="tok-k">GET</span>    /images/                     <span className="tok-c"># list (scene, content_type,</span>{`
                                  `}<span className="tok-c">#   indoor_outdoor, has_faces,</span>{`
                                  `}<span className="tok-c">#   has_gps, person_id, folder_id,</span>{`
                                  `}<span className="tok-c">#   starred, trashed, tag, all)</span>{`
`}<span className="tok-k">GET</span>    /images/facets               <span className="tok-c"># filter chip options + counts</span>{`
`}<span className="tok-k">GET</span>    /images/{`{id}`}/original        <span className="tok-c"># original bytes</span>{`
`}<span className="tok-k">GET</span>    /images/{`{id}`}/served          <span className="tok-c"># compressed (?max_dim=N for thumbs)</span>{`
`}<span className="tok-k">POST</span>   /images/{`{id}`}/star            <span className="tok-c"># toggle favorite</span>{`
`}<span className="tok-k">POST</span>   /images/{`{id}`}/resummarize     <span className="tok-c"># force re-caption</span>{`
`}<span className="tok-k">PATCH</span>  /images/{`{id}`}/name            <span className="tok-c"># rename</span>{`
`}<span className="tok-k">DELETE</span> /images/{`{id}`}                 <span className="tok-c"># soft delete (?purge=true → hard)</span>{`
`}<span className="tok-k">POST</span>   /images/best-of              <span className="tok-c"># rank N selected</span>{`

`}<span className="tok-c"># Search</span>{`
`}<span className="tok-k">GET</span>    /search/?q=<span className="tok-s">&lt;text&gt;</span>            <span className="tok-c"># hybrid CLIP + FTS + WordNet</span>{`

`}<span className="tok-c"># Cloud sync</span>{`
`}<span className="tok-k">POST</span>   /cloud/{`{src}`}/connect          <span className="tok-c"># OAuth / credentials start</span>{`
`}<span className="tok-k">POST</span>   /cloud/{`{src}`}/sync             <span className="tok-c"># manual sweep</span>{`

`}<span className="tok-c"># Billing · Health</span>{`
`}<span className="tok-k">POST</span>   /billing/checkout            <span className="tok-c"># Stripe Embedded Checkout</span>{`
`}<span className="tok-k">GET</span>    /health                      <span className="tok-c"># liveness + DB ping</span>
            </pre>
          </div>
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
