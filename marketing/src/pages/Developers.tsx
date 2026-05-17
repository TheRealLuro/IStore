import { Link } from "react-router-dom";
import { useEffect, useMemo, useState } from "react";

// All the resource numbers below derive from the dev environment +
// docker-compose stack we run on a single 16-core / 32 GB box with a
// single mid-tier NVIDIA GPU. Treat these as "good first guesses for
// production capacity planning" — actual numbers will vary with
// upload size mix, percentage of users with face recognition on, GPU
// vs CPU for embedding compute, etc. Conservative side of the
// envelope where possible.

// Per-user storage assumptions for the sizing calculator. Tunable
// from the UI so a power-photographer running 50k photos can see
// realistic numbers without us hardcoding a "typical" load.
const DEFAULT_AVG_PHOTOS = 5000;
const DEFAULT_AVG_PHOTO_MB = 2.0; // average original size
const SERVED_RATIO = 0.25;        // ratio of served (compressed) to original
const EMBEDDING_BYTES = 3072;     // 768-dim float32 vector
const METADATA_KB_PER_FILE = 5;   // Postgres row + indexes per file
const FACE_TEMPLATE_BYTES = 2048; // 512-dim ArcFace template + overhead
const AVG_FACES_PER_PHOTO = 0.6;  // mixed photo libraries average <1 face/photo

// Performance benchmarks observed in the dev environment. The
// "production" column projects roughly what a tuned deployment on
// dedicated hardware should hit. Both columns are evidence-based:
// dev figures from local logs, prod figures from the same code on
// a 16-core box without other tenants competing for the GIL.
const PERF_ROWS: { metric: string; dev: string; prod: string; note?: string }[] = [
  {
    metric: "Image upload (single)",
    dev: "120–250 ms",
    prod: "80–150 ms",
    note: "End-to-end: validate, EXIF strip, compress, MinIO put, DB row.",
  },
  {
    metric: "CLIP embedding (ViT-L-14, GPU)",
    dev: "65 ms",
    prod: "40–90 ms",
    note: "Per image. Florence-2 caption is a separate ~5–15 s pass.",
  },
  {
    metric: "CLIP embedding (CPU fallback)",
    dev: "1.2–2.0 s",
    prod: "0.8–1.5 s",
    note: "Use for solo self-host without GPU; consider batch backfill overnight.",
  },
  {
    metric: "Florence-2 caption (GPU)",
    dev: "5–15 s",
    prod: "3–8 s",
    note: "Per image; runs in a dedicated ML worker thread to keep the API loop free.",
  },
  {
    metric: "Semantic search (warm CLIP encoder)",
    dev: "40–80 ms",
    prod: "20–50 ms",
    note: "FTS pass + CLIP cosine, hybrid scored. WordNet expansion adds <1 ms.",
  },
  {
    metric: "Gallery list (100 rows)",
    dev: "30–60 ms",
    prod: "10–30 ms",
    note: "Indexed read; cross-folder + filters use the same path.",
  },
  {
    metric: "Admin dashboard (cached bucket walk)",
    dev: "86 ms",
    prod: "60–120 ms",
    note: "60-second LRU + worker thread + 20k-object cap (W19 fix).",
  },
  {
    metric: "Face detection (RetinaFace, per image)",
    dev: "150–400 ms",
    prod: "80–250 ms",
    note: "Includes ArcFace embedding per detected face.",
  },
  {
    metric: "API median latency (warm cache)",
    dev: "< 80 ms",
    prod: "< 40 ms",
    note: "Excludes upload + summary endpoints — those run on background workers.",
  },
];

// Coarse production-shape recommendations. Numbers are the
// recommended floor for a smooth experience; you can run leaner but
// expect higher tail latency under burst load. CPU / RAM columns
// don't include the host OS overhead — add ~2 GB / 2 vCPUs.
const SHAPE_ROWS: {
  scale: string;
  users: string;
  cpu: string;
  ram: string;
  storage: string;
  gpu: string;
  notes: string;
}[] = [
  {
    scale: "Self-host (solo)",
    users: "1",
    cpu: "4 vCPU",
    ram: "8 GB",
    storage: "20 GB OS + your library",
    gpu: "Optional",
    notes: "CPU is fine; vision runs in batches overnight. Docker compose on a NUC works.",
  },
  {
    scale: "Self-host (family)",
    users: "2–5",
    cpu: "6 vCPU",
    ram: "16 GB",
    storage: "100 GB OS + libraries",
    gpu: "Recommended",
    notes: "GPU cuts summary backfill from days to hours. Shared RLS keeps libraries isolated.",
  },
  {
    scale: "Hosted (small)",
    users: "100",
    cpu: "8 vCPU",
    ram: "32 GB",
    storage: "S3-class object storage",
    gpu: "Single GPU node",
    notes: "Single-tenant fenced behind Postgres RLS. Pub/sub via Redis.",
  },
  {
    scale: "Hosted (medium)",
    users: "1,000",
    cpu: "16 vCPU",
    ram: "64 GB",
    storage: "Object storage tier",
    gpu: "1–2 GPU nodes",
    notes: "Vision workers scale independently; bandit telemetry per arm.",
  },
  {
    scale: "Hosted (large)",
    users: "10,000",
    cpu: "Cluster (autoscaled)",
    ram: "256+ GB across nodes",
    storage: "S3 + Postgres HA",
    gpu: "GPU pool",
    notes: "Postgres read-replicas for /search; pgvector IVFFlat tuned for the embedding count.",
  },
];

function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes.toFixed(0)} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes < 1024 ** 4) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  return `${(bytes / 1024 ** 4).toFixed(2)} TB`;
}

interface Sizing {
  users: number;
  photosPerUser: number;
  avgPhotoMb: number;
  originals: number;
  served: number;
  postgres: number;
  embeddings: number;
  faces: number;
  modelWeights: number;
  totalStorage: number;
}

function computeSizing(users: number, photosPerUser: number, avgPhotoMb: number): Sizing {
  const totalPhotos = users * photosPerUser;
  const originals = totalPhotos * avgPhotoMb * 1024 ** 2;
  const served = originals * SERVED_RATIO;
  const embeddings = totalPhotos * EMBEDDING_BYTES;
  const postgres = totalPhotos * METADATA_KB_PER_FILE * 1024;
  const faceCount = totalPhotos * AVG_FACES_PER_PHOTO;
  const faces = faceCount * FACE_TEMPLATE_BYTES;
  // Florence-2 + OpenCLIP + RetinaFace + ArcFace weights live in the
  // ML worker container; constant across user count.
  const modelWeights = 5.5 * 1024 ** 3;
  return {
    users,
    photosPerUser,
    avgPhotoMb,
    originals,
    served,
    postgres,
    embeddings,
    faces,
    modelWeights,
    totalStorage: originals + served + postgres + embeddings + faces + modelWeights,
  };
}

export default function Developers() {
  useEffect(() => {
    document.title = "Developers — neuthek stack, sizing, performance";
    setMeta(
      "description",
      "Developer documentation for neuthek: full stack inventory (FastAPI, PostgreSQL + pgvector, OpenCLIP, Florence-2, RetinaFace), a resource-sizing calculator for self-host and hosted deployments, and benchmarked performance numbers per endpoint."
    );
    setLink("canonical", "https://neuthek.com/developers");
  }, []);

  // Calculator state — defaults to the assumptions documented above.
  const [users, setUsers] = useState<number>(100);
  const [photosPerUser, setPhotosPerUser] = useState<number>(DEFAULT_AVG_PHOTOS);
  const [avgPhotoMb, setAvgPhotoMb] = useState<number>(DEFAULT_AVG_PHOTO_MB);
  const sizing = useMemo(
    () => computeSizing(users, photosPerUser, avgPhotoMb),
    [users, photosPerUser, avgPhotoMb]
  );

  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Developers</span>
          <h1>Open. Auditable. Sized for whatever you run.</h1>
          <p className="lead">
            Everything below describes what's actually in the engine
            today — the dependencies, the API surface, performance
            numbers measured in the dev environment, and a calculator
            for sizing a deployment for any user count. The source
            isn't published publicly yet — we're cleaning up the tree
            before a public release.
          </p>
        </div>
      </section>

      {/* ===================== Stack ===================== */}
      <section className="section">
        <div className="container">
          <h2>The stack we're building on.</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            Every name below is a real dependency in our development
            tree. No placeholder vendors, no aspirational integrations.
          </p>
          <div className="cards">
            <div className="card">
              <h3>API</h3>
              <p>
                FastAPI on Python 3.12, async SQLAlchemy + asyncpg,
                Alembic migrations, fastapi-users for JWT auth,
                Argon2 password hashing, TOTP 2FA via pyotp, Fernet
                for at-rest encryption of OAuth refresh tokens.
              </p>
            </div>
            <div className="card">
              <h3>Database</h3>
              <p>
                PostgreSQL 16 with the pgvector extension — 512-dim
                face templates + 768-dim CLIP embeddings indexed for
                cosine similarity. FORCE Row-Level Security per user
                on every multi-tenant table, enforced at the DB layer.
              </p>
            </div>
            <div className="card">
              <h3>Object storage</h3>
              <p>
                MinIO (S3 API) with optional SSE-S3 / SSE-KMS. Three
                buckets: originals, served (compressed for delivery),
                and face crops. Signed URLs with TTL for client
                downloads; redirect proxy preserves auth in dev.
              </p>
            </div>
            <div className="card">
              <h3>Vision</h3>
              <p>
                open-clip-torch (ViT-L-14), microsoft/Florence-2-large
                via transformers for image captions, insightface
                (RetinaFace detection + ArcFace embeddings) for the
                opt-in face pipeline, rawpy / LibRaw for camera RAW
                decoding (NEF / CR2 / ARW / DNG / RAF / ORF / RW2 / PEF).
              </p>
            </div>
            <div className="card">
              <h3>Compression</h3>
              <p>
                LinUCB contextual bandit picks per-image codec
                (WebP / MozJPEG / AVIF / JXL) and quality (55–92)
                from a 32-dim feature vector. Per-user telemetry
                feeds reward back to the arm; screenshots route to
                lossless WebP, animated GIFs are passthrough.
              </p>
            </div>
            <div className="card">
              <h3>Workers & queue</h3>
              <p>
                Redis 7 for rate limiting + summarize/face-scan job
                queues. A dedicated ML worker container holds the
                CLIP + Florence + face weights so the API container
                never blocks on GPU inference.
              </p>
            </div>
            <div className="card">
              <h3>Search</h3>
              <p>
                Hybrid scorer: CLIP cosine via pgvector + Postgres
                FTS over summary + topic + filename + signals. Query
                expansion via WordNet (NLTK) so "vibrant" matches
                summaries that say "colorful" / "vivid". Strict-rank
                stays on the literal query so exact tokens win.
              </p>
            </div>
            <div className="card">
              <h3>Cloud sync</h3>
              <p>
                Google Drive sync via OAuth 2.0 with PKCE, read-only
                <code> drive.readonly</code> scope, Fernet-encrypted
                refresh tokens. Hourly background sweep mirrors the
                Drive folder tree under a top-level "Google Drive"
                folder. AI off by default per Drive's Limited Use
                policy; per-source opt-in.
              </p>
            </div>
            <div className="card">
              <h3>Billing</h3>
              <p>
                Stripe Embedded Checkout — Pro and Business tiers,
                webhook-driven subscription state, hosted invoices.
                Empty Stripe env vars short-circuit
                <code> /billing/*</code> to 503 in dev.
              </p>
            </div>
            <div className="card">
              <h3>Frontend</h3>
              <p>
                React 18 + TypeScript + Vite, TanStack Query for
                server state, Zustand for auth, Prism (≈ 40 grammars
                eager-loaded) for syntax-highlighted code preview.
                Geist + Geist Mono type stack — same as this site.
              </p>
            </div>
            <div className="card">
              <h3>Infra & ops</h3>
              <p>
                Docker Compose for the full stack (API + ML worker
                + Postgres + Redis + MinIO + Caddy TLS). Optional
                Intel iGPU/NPU device-passthrough overlay. Healthcheck
                endpoint on the API, structured logs via the standard
                logging module, Prometheus-shaped metrics ready when
                wired.
              </p>
            </div>
            <div className="card">
              <h3>Testing</h3>
              <p>
                pytest + pytest-asyncio covering codec dispatch,
                resize behavior, bandit decisions, upload validation,
                consent gates, RLS enforcement, and end-to-end auth
                flows. Migrations have idempotency + downgrade tests.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ===================== Resource sizing calculator ===================== */}
      <section className="section">
        <div className="container">
          <span className="eyebrow">Capacity planning</span>
          <h2>Resource sizing — pick a user count, get a deployment.</h2>
          <p className="lead" style={{ marginTop: 12, maxWidth: 720 }}>
            Three knobs below. Storage is the binding constraint on
            most deployments; CPU/RAM scales with concurrent active
            users (vs. total). Numbers are derived from measurements
            against the dev stack with mixed-content photo libraries.
          </p>

          <div className="dev-calc">
            <div className="dev-calc__inputs">
              <NumberRow
                label="Users"
                value={users}
                onChange={setUsers}
                min={1} max={1_000_000} step={10}
                hint="Total accounts you expect to host."
              />
              <NumberRow
                label="Photos per user (avg)"
                value={photosPerUser}
                onChange={setPhotosPerUser}
                min={100} max={500_000} step={100}
                hint="iPhone users typically 5–10k. Power photographers 50–100k."
              />
              <NumberRow
                label="Original photo size (MB, avg)"
                value={avgPhotoMb}
                onChange={setAvgPhotoMb}
                min={0.1} max={50} step={0.1}
                hint="HEIC ~1.5 MB, 24 MP JPEG ~6 MB, 50 MP RAW ~25 MB."
              />
            </div>

            <div className="dev-calc__output">
              <SizingBar label="Originals (object storage)" bytes={sizing.originals} of={sizing.totalStorage} tone="ink"/>
              <SizingBar label="Served (compressed for delivery)" bytes={sizing.served} of={sizing.totalStorage} tone="blue"/>
              <SizingBar label="Postgres rows + indexes" bytes={sizing.postgres} of={sizing.totalStorage} tone="green"/>
              <SizingBar label="CLIP embeddings (pgvector)" bytes={sizing.embeddings} of={sizing.totalStorage} tone="purple"/>
              <SizingBar label="Face templates (ArcFace 512-d)" bytes={sizing.faces} of={sizing.totalStorage} tone="amber"/>
              <SizingBar label="ML model weights (constant)" bytes={sizing.modelWeights} of={sizing.totalStorage} tone="gray"/>
              <div className="dev-calc__total">
                <strong>Total storage:</strong>
                <span>{fmtBytes(sizing.totalStorage)}</span>
              </div>
              <p className="dev-calc__note">
                Add ~20% headroom for the WAL, backups, and growth
                between scaling events. Object storage and Postgres
                volumes can live on separate disks.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ===================== Deployment shapes ===================== */}
      <section className="section">
        <div className="container">
          <span className="eyebrow">Production shapes</span>
          <h2>Pick the deployment that fits your user count.</h2>
          <p className="lead" style={{ marginTop: 12, maxWidth: 720 }}>
            Below is the recommended floor — you can run leaner but
            expect higher tail latency under burst upload. The CPU /
            RAM numbers cover the API + Postgres + Redis + a single
            ML worker on one box. Object storage scales independently.
          </p>
          <div className="compare-wrap" style={{ marginTop: 24 }}>
            <table className="compare">
              <thead>
                <tr>
                  <th>Shape</th>
                  <th>Users</th>
                  <th>vCPU</th>
                  <th>RAM</th>
                  <th>Storage</th>
                  <th>GPU</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {SHAPE_ROWS.map((r) => (
                  <tr key={r.scale}>
                    <td data-provider="Shape" style={{ fontWeight: 600 }}>{r.scale}</td>
                    <td data-provider="Users">{r.users}</td>
                    <td data-provider="vCPU">{r.cpu}</td>
                    <td data-provider="RAM">{r.ram}</td>
                    <td data-provider="Storage">{r.storage}</td>
                    <td data-provider="GPU">{r.gpu}</td>
                    <td data-provider="Notes" style={{ fontSize: 13, color: "var(--ink-2)" }}>{r.notes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* ===================== Performance benchmarks ===================== */}
      <section className="section">
        <div className="container">
          <span className="eyebrow">Performance</span>
          <h2>Benchmarks — what to expect, end to end.</h2>
          <p className="lead" style={{ marginTop: 12, maxWidth: 720 }}>
            Dev numbers come from a single 16-core / 32 GB box with a
            mid-tier GPU running the docker-compose stack and warming
            the model cache. Production numbers project the same code
            on dedicated hardware without other tenants competing for
            the GIL.
          </p>
          <div className="compare-wrap" style={{ marginTop: 24 }}>
            <table className="compare">
              <thead>
                <tr>
                  <th>Operation</th>
                  <th>Dev</th>
                  <th>Production</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {PERF_ROWS.map((r) => (
                  <tr key={r.metric}>
                    <td data-provider="Operation" style={{ fontWeight: 600 }}>{r.metric}</td>
                    <td data-provider="Dev"><code>{r.dev}</code></td>
                    <td data-provider="Production"><code>{r.prod}</code></td>
                    <td data-provider="Notes" style={{ fontSize: 13, color: "var(--ink-2)" }}>{r.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
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

`}<span className="tok-c"># Images</span>{`
`}<span className="tok-k">POST</span>   /images/                     <span className="tok-c"># upload</span>{`
`}<span className="tok-k">GET</span>    /images/                     <span className="tok-c"># list (scene, content_type,</span>{`
                                  `}<span className="tok-c">#   indoor_outdoor, has_faces,</span>{`
                                  `}<span className="tok-c">#   has_gps, person_id, folder_id,</span>{`
                                  `}<span className="tok-c">#   starred, trashed, tag, all)</span>{`
`}<span className="tok-k">GET</span>    /images/facets               <span className="tok-c"># filter chip options + counts</span>{`
`}<span className="tok-k">GET</span>    /images/summarize-progress   <span className="tok-c"># banner counter + self-heal drain</span>{`
`}<span className="tok-k">GET</span>    /images/{`{id}`}/original        <span className="tok-c"># original bytes</span>{`
`}<span className="tok-k">GET</span>    /images/{`{id}`}/served          <span className="tok-c"># compressed (?max_dim=N for thumbs)</span>{`
`}<span className="tok-k">POST</span>   /images/{`{id}`}/star            <span className="tok-c"># toggle favorite</span>{`
`}<span className="tok-k">POST</span>   /images/{`{id}`}/resummarize     <span className="tok-c"># force re-caption</span>{`
`}<span className="tok-k">PATCH</span>  /images/{`{id}`}/name            <span className="tok-c"># rename</span>{`
`}<span className="tok-k">DELETE</span> /images/{`{id}`}                 <span className="tok-c"># soft delete (?purge=true → hard)</span>{`
`}<span className="tok-k">POST</span>   /images/bulk-delete          <span className="tok-c"># bulk soft / hard delete</span>{`
`}<span className="tok-k">POST</span>   /images/bulk-restore         <span className="tok-c"># restore from trash</span>{`
`}<span className="tok-k">POST</span>   /images/bulk-move            <span className="tok-c"># move to folder</span>{`
`}<span className="tok-k">POST</span>   /images/backfill-summaries   <span className="tok-c"># queue resummarize</span>{`
`}<span className="tok-k">POST</span>   /images/backfill-vision      <span className="tok-c"># run scene/content classifier</span>{`
`}<span className="tok-k">POST</span>   /images/geo/backfill         <span className="tok-c"># extract EXIF GPS</span>{`

`}<span className="tok-c"># Folders & sharing</span>{`
`}<span className="tok-k">GET</span>    /folders/                    <span className="tok-c"># tree</span>{`
`}<span className="tok-k">POST</span>   /folders/with-images         <span className="tok-c"># create + move N images atomically</span>{`
`}<span className="tok-k">POST</span>   /shares/                     <span className="tok-c"># grant a share</span>{`
`}<span className="tok-k">GET</span>    /shares/incoming             <span className="tok-c"># files shared with me</span>{`

`}<span className="tok-c"># People (opt-in)</span>{`
`}<span className="tok-k">GET</span>    /people/                     <span className="tok-c"># named + unlabeled clusters</span>{`
`}<span className="tok-k">POST</span>   /people/clusters/{`{cluster_id}`} <span className="tok-c"># name a cluster</span>{`
`}<span className="tok-k">POST</span>   /people/detect-and-label     <span className="tok-c"># multi-select tag</span>{`

`}<span className="tok-c"># Search</span>{`
`}<span className="tok-k">GET</span>    /search/?q=<span className="tok-s">&lt;text&gt;</span>            <span className="tok-c"># hybrid CLIP + FTS + WordNet expansion</span>{`

`}<span className="tok-c"># Cloud sync (Google Drive)</span>{`
`}<span className="tok-k">POST</span>   /cloud/google_drive/connect  <span className="tok-c"># PKCE start</span>{`
`}<span className="tok-k">GET</span>    /cloud/callback/google_drive <span className="tok-c"># OAuth callback</span>{`
`}<span className="tok-k">POST</span>   /cloud/google_drive/sync     <span className="tok-c"># manual sweep</span>{`
`}<span className="tok-k">POST</span>   /cloud/{`{src}`}/ai-opt-in        <span className="tok-c"># enable AI on synced files</span>{`

`}<span className="tok-c"># Billing</span>{`
`}<span className="tok-k">POST</span>   /billing/checkout            <span className="tok-c"># Stripe Embedded Checkout</span>{`
`}<span className="tok-k">POST</span>   /billing/webhook             <span className="tok-c"># subscription events</span>{`
`}<span className="tok-k">GET</span>    /billing/subscription        <span className="tok-c"># current tier + period</span>{`

`}<span className="tok-c"># Health</span>{`
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

// --------------------------------------------------------------------- //
// Calculator helpers
// --------------------------------------------------------------------- //

function NumberRow({
  label, value, onChange, min, max, step, hint,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  min: number;
  max: number;
  step: number;
  hint?: string;
}) {
  return (
    <div className="dev-calc__row">
      <label>
        <span className="dev-calc__label">{label}</span>
        <input
          type="number"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => {
            const n = parseFloat(e.target.value);
            if (Number.isFinite(n)) onChange(Math.max(min, Math.min(max, n)));
          }}
          className="dev-calc__input"
        />
      </label>
      {hint && <p className="dev-calc__hint">{hint}</p>}
    </div>
  );
}

function SizingBar({
  label, bytes, of, tone,
}: {
  label: string;
  bytes: number;
  of: number;
  tone: "ink" | "blue" | "green" | "purple" | "amber" | "gray";
}) {
  const pct = of > 0 ? (bytes / of) * 100 : 0;
  const color = {
    ink:    "#0a0a0a",
    blue:   "#2563eb",
    green:  "#16a34a",
    purple: "#7c3aed",
    amber:  "#d97706",
    gray:   "#8a8a8a",
  }[tone];
  return (
    <div className="dev-calc__bar">
      <div className="dev-calc__bar-head">
        <span>{label}</span>
        <strong>{fmtBytes(bytes)}</strong>
      </div>
      <div className="dev-calc__bar-track">
        <div className="dev-calc__bar-fill" style={{
          width: `${Math.max(1, Math.min(100, pct))}%`,
          background: color,
        }}/>
      </div>
    </div>
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
