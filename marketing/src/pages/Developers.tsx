import { Link } from "react-router-dom";
import { useEffect } from "react";

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
            Two questions worth answering up front: which dependencies
            we picked and what each one earns its place doing, and
            which AI/ML models we run and why those over the
            alternatives. Both lists are real — every name below is in
            our development tree today. The source isn't published
            publicly yet; we're cleaning up the tree before a public
            release.
          </p>
        </div>
      </section>

      {/* ===================== Stack ===================== */}
      <section className="section">
        <div className="container">
          <span className="eyebrow">The stack</span>
          <h2>What it runs on, and why.</h2>
          <p className="lead" style={{ marginTop: 12, maxWidth: 720 }}>
            One short paragraph per dependency: what it does for us +
            why we picked it over the obvious alternative.
          </p>
          <div className="cards" style={{ marginTop: 24 }}>
            <div className="card">
              <h3>FastAPI + Python 3.12</h3>
              <p>
                <strong>What:</strong> the HTTP layer + every business
                endpoint, with async SQLAlchemy + asyncpg under the
                hood and Alembic for migrations.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why:</strong> async by default (none of the
                Flask/WSGI dance with greenlets), automatic OpenAPI
                docs at <code>/docs</code>, and Pydantic v2 gives us
                request/response validation that doubles as the
                published schema. Python because the ML side is
                already PyTorch — same runtime as the inference
                workers means no IPC layer between API and models.
              </p>
            </div>

            <div className="card">
              <h3>PostgreSQL 16 + pgvector</h3>
              <p>
                <strong>What:</strong> the source of truth for every
                user row, plus the cosine-similarity index over
                CLIP + face embeddings.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why:</strong> we wanted vectors and ACID in
                the same transaction — no separate Pinecone or Qdrant
                to keep in sync with the row that owns the embedding.
                pgvector does HNSW or IVFFlat indexing in-database;
                deleting a row deletes its vector atomically. Postgres
                also gives us <strong>FORCE Row-Level Security</strong>{" "}
                per user on every multi-tenant table — a bug in an
                app handler still can't return another user's row.
              </p>
            </div>

            <div className="card">
              <h3>Redis 7</h3>
              <p>
                <strong>What:</strong> per-user job queues for the ML
                pipeline (summarize, face-scan, vision backfill), plus
                rate-limit counters for every gated endpoint.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why:</strong> the per-user fair scheduler
                needs cheap atomic list operations (LPUSH / BRPOP) and
                cheap atomic counters (INCR with EXPIRE). Postgres can
                do both but pays a transaction cost we don't need for
                ephemeral state. Single-purpose, single-process,
                trivially backed up by skipping the backup.
              </p>
            </div>

            <div className="card">
              <h3>MinIO (S3 API)</h3>
              <p>
                <strong>What:</strong> object storage for originals,
                served (compressed) variants, and face crops — three
                logical buckets with SSE-S3 or SSE-KMS encryption at
                rest.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why:</strong> the S3 wire protocol means we
                can swap MinIO out for real AWS / R2 / Backblaze
                without changing a single line of application code.
                MinIO itself runs anywhere — a NUC, a Raspberry Pi,
                or a Kubernetes cluster — same binary.
              </p>
            </div>

            <div className="card">
              <h3>Caddy (TLS edge)</h3>
              <p>
                <strong>What:</strong> reverse proxy + automatic
                Let's Encrypt for TLS, HSTS, HTTP/3, and the security
                headers the app expects to find on inbound requests.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why:</strong> nginx + certbot is two tools to
                configure and one cron job to forget. Caddy is one
                config file, certificates auto-renew, and the defaults
                are already tighter than most hand-rolled nginx
                blocks. Saves operators from having to know HTTPS to
                run the stack.
              </p>
            </div>

            <div className="card">
              <h3>fastapi-users + JWT + TOTP</h3>
              <p>
                <strong>What:</strong> account creation, password
                reset, email verification, JWT issuance, TOTP 2FA via
                pyotp, and Argon2id password hashing — all from one
                pluggable library.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why:</strong> rolling your own auth is the
                fastest way to ship a security bug. fastapi-users is
                production-tested, plugs into FastAPI's dependency
                system natively, and lets us layer our own
                consent-bundle hook on top of the user-create path
                without forking.
              </p>
            </div>

            <div className="card">
              <h3>Fernet for at-rest secrets</h3>
              <p>
                <strong>What:</strong> AES-128-CBC + HMAC-SHA-256
                envelope for OAuth refresh tokens, TOTP secrets, and
                anything else the server holds that an attacker
                shouldn't be able to read straight out of a database
                dump.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why:</strong> Fernet is the boring-correct
                choice — authenticated encryption, no nonce reuse
                footguns, key rotation built in. The key itself comes
                from <code>CLOUD_ENCRYPTION_KEY</code> in the
                operator environment; a boot-time validator refuses
                to start in prod without it.
              </p>
            </div>

            <div className="card">
              <h3>React 18 + Vite + TanStack Query</h3>
              <p>
                <strong>What:</strong> the SPA you actually use. Vite
                for dev + build, TanStack Query for every server-state
                fetch (caching, retries, optimistic updates), Zustand
                for the small slice of client state that isn't
                server-derived.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why:</strong> TanStack Query removes most of
                the reasons people reach for Redux. Vite's HMR is the
                fastest dev loop we've used. React 18 because the
                concurrent features (transitions for the gallery
                filter chips, Suspense for the lightbox) are load-
                bearing for perceived responsiveness.
              </p>
            </div>

            <div className="card">
              <h3>Prism (≈ 40 grammars)</h3>
              <p>
                <strong>What:</strong> syntax highlighting for the
                code-file preview surface — open a <code>.py</code>{" "}
                or <code>.ts</code> in your library and it renders
                with proper tokens.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why:</strong> highlight.js auto-detect mis-
                fires on small files. Prism's per-language grammars
                are explicit, ship as small modules, and we
                eager-load the 40 grammars that cover almost every
                source file people store in personal clouds.
              </p>
            </div>

            <div className="card">
              <h3>Docker Compose</h3>
              <p>
                <strong>What:</strong> one <code>docker compose up -d</code>{" "}
                brings up the API container, ML worker container,
                Postgres, Redis, MinIO, and Caddy with TLS.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why:</strong> self-host is the headline
                deployment story. Compose is the lowest-friction way
                to spin a multi-container app on a single host — no
                Kubernetes, no Helm chart, no cluster you need to
                manage. Optional overlays add TLS, encrypted backups,
                and Intel iGPU/NPU device-passthrough without
                touching the base file.
              </p>
            </div>

            <div className="card">
              <h3>pytest + pytest-asyncio</h3>
              <p>
                <strong>What:</strong> end-to-end test suite covering
                upload validation, codec dispatch, RLS enforcement,
                consent gates, auth flows, deletion completeness, and
                migration idempotency.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why:</strong> we lean on the test suite to
                prove security properties — there's a test that uploads
                + deletes + asserts 0 rows + 0 objects, a test that
                tries to read another tenant's data with RLS active
                and asserts the empty result, and a test for every
                hygiene contract (no real PII in fixtures, no binary
                blobs under <code>tests/</code>, gitleaks runs full
                history).
              </p>
            </div>

            <div className="card">
              <h3>Stripe (Embedded Checkout)</h3>
              <p>
                <strong>What:</strong> Free / Pro / Business tiers via
                Stripe-hosted checkout + signature-verified webhooks +
                Customer Portal for invoices and plan management.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why:</strong> Embedded Checkout means we
                never see a card number or PCI scope. Empty Stripe
                env vars short-circuit <code>/billing/*</code> to 503
                in dev so the operator never has to wire it up to run
                self-host.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ===================== Models ===================== */}
      <section className="section">
        <div className="container">
          <span className="eyebrow">Models we chose</span>
          <h2>The AI side: what we run, and why we run it.</h2>
          <p className="lead" style={{ marginTop: 12, maxWidth: 720 }}>
            Every model below is pre-trained with frozen weights —{" "}
            <strong>we never fine-tune anything on your library</strong>.
            Why we picked each over the obvious alternative is in the
            second paragraph of every card.
          </p>
          <div className="cards" style={{ marginTop: 24 }}>
            <div className="card">
              <h3>OpenCLIP ViT-L-14</h3>
              <p>
                <strong>What it does:</strong> embeds every image
                into a 768-dim vector that captures visual meaning.
                Query text embeds into the same space; pgvector
                cosine-similarity ranks results. That's how "snowy
                roof at sunset" finds the photo without ever indexing
                that string.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why this one:</strong> OpenAI's original CLIP
                is closed and never got a proper open release.
                OpenCLIP is the open re-implementation trained on
                LAION-2B — same architecture, openly licensed weights,
                stronger benchmarks. ViT-L-14 is the sweet spot
                between accuracy and inference cost on a single
                consumer GPU. Larger variants (ViT-H, ViT-G) gain
                ~3-5 % recall at 3-4× the cost; not worth it at this
                scale.
              </p>
            </div>

            <div className="card">
              <h3>Florence-2-large</h3>
              <p>
                <strong>What it does:</strong> generates the detailed
                caption for every image ("a black-and-white photo of
                two people standing on a rocky beach at sunset"). Also
                handles inline OCR — scene-gated so we only run it on
                images likely to contain text.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why this one:</strong> the obvious alternative
                is BLIP-2 or LLaVA. Florence-2 from Microsoft Research
                is purpose-built for vision tasks (caption, detect,
                segment, OCR — all in one model), runs comfortably on
                a mid-tier GPU, and produces noticeably more concrete
                captions than BLIP-2 ("auth-flow review on a
                whiteboard" vs. "a person near a whiteboard"). Apache-
                2.0 licensed and small enough that 8-bit / 4-bit quant
                paths are tractable for self-hosters.
              </p>
            </div>

            <div className="card">
              <h3>Qwen2.5-Instruct</h3>
              <p>
                <strong>What it does:</strong> rewrites Florence-2's
                raw caption into the human-readable summary you see
                in the library, splices in detected scene labels,
                handles document summarization (PDFs, code, markdown)
                via map-reduce over chunked content.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why this one:</strong> Llama-3.2-Instruct was
                the runner-up; Qwen2.5 was consistently better on
                instruction following at the same parameter count and
                ships in 0.5B / 1.5B / 3B / 7B variants so operators
                can pick by hardware. Apache-2.0 licensed (no
                license-acceptance gate). The 1.5B variant runs at
                acceptable latency on CPU for self-hosters without a
                GPU; the 4-bit GGUF quant path is well-trodden.
              </p>
            </div>

            <div className="card">
              <h3>RetinaFace + ArcFace (insightface buffalo_l)</h3>
              <p>
                <strong>What it does:</strong> when face recognition
                is enabled (opt-in only — off by default), RetinaFace
                detects faces in the image and ArcFace produces a
                512-dim embedding per face. Embeddings cluster into
                Me + tagged people; everyone else is grouped but never
                named without your input.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why this one:</strong> insightface is the
                standard reference implementation for both — used by
                most face-recognition production stacks. ArcFace's
                angular margin loss outperforms FaceNet's triplet loss
                on standard benchmarks and the embeddings are stable
                across age + lighting. The buffalo_l bundle ships
                both models together with consistent preprocessing,
                so we don't have to wire two pipelines.
              </p>
            </div>

            <div className="card">
              <h3>rawpy + LibRaw</h3>
              <p>
                <strong>What it does:</strong> decodes camera RAW
                files — Nikon NEF, Canon CR2, Sony ARW, Adobe DNG,
                Fuji RAF, Olympus ORF, Panasonic RW2, Pentax PEF —
                into the full sensor image, not just the small
                embedded JPEG preview most apps fall back to.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why this one:</strong> LibRaw is the open-
                source standard that dcraw evolved into; rawpy is the
                Python binding. Pillow's RAW support is read-only and
                grabs only the embedded preview. We re-encode the
                decoded full-resolution image at JPEG q=95 for
                thumbnails so the gallery shows what your camera
                actually captured, not what the manufacturer's
                preview JPEG decided was good enough.
              </p>
            </div>

            <div className="card">
              <h3>LinUCB contextual bandit (compression)</h3>
              <p>
                <strong>What it does:</strong> picks the best codec
                (WebP / MozJPEG / AVIF / JXL) and quality level
                (55-92) for each image, from a 32-dim feature vector
                covering resolution, aspect ratio, detected
                screenshot vs. photo, color count, dynamic range,
                file-size class, and a few others.
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why this one:</strong> a fixed "JPEG q=85"
                policy gives mediocre results on everything. A neural
                policy would need training data per user. LinUCB is
                the contextual-bandit baseline that learns online,
                converges in dozens of impressions per arm, and stays
                explainable — you can read the per-arm coefficients
                and explain exactly why it picked AVIF over WebP for
                a given image. Per-user telemetry feeds reward back
                privately; we never share arm state across users.
              </p>
            </div>

            <div className="card">
              <h3>WordNet (via NLTK)</h3>
              <p>
                <strong>What it does:</strong> expands every search
                query into related terms before scoring. "vibrant"
                also matches summaries that say "colorful", "vivid",
                "bright", "saturated"; "sunset" matches "dusk",
                "evening", "golden hour", "twilight".
              </p>
              <p style={{ marginTop: 8 }}>
                <strong>Why this one:</strong> CLIP already handles
                most of the semantic stretch, but the FTS pass over
                summaries is token-exact and benefits from explicit
                synonym expansion. WordNet is offline (no API), tiny
                (~10 MB), exhaustive for English nouns and adjectives,
                and has been the standard since 1995 for good
                reason. We layer a small visual-domain overlay on top
                for terms WordNet doesn't link well in the
                photographer sense.
              </p>
            </div>
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
`}<span className="tok-k">POST</span>   /images/bulk-delete          <span className="tok-c"># bulk soft / hard delete</span>{`
`}<span className="tok-k">POST</span>   /images/bulk-restore         <span className="tok-c"># restore from trash</span>{`
`}<span className="tok-k">POST</span>   /images/bulk-move            <span className="tok-c"># move to folder</span>{`
`}<span className="tok-k">POST</span>   /images/best-of              <span className="tok-c"># rank N selected (sharpness +</span>{`
                                  `}<span className="tok-c">#   exposure + face + use-case)</span>{`

`}<span className="tok-c"># Folders, tags & sharing</span>{`
`}<span className="tok-k">GET</span>    /folders/                    <span className="tok-c"># tree (?contains_type=image|video|doc)</span>{`
`}<span className="tok-k">POST</span>   /folders/with-images         <span className="tok-c"># create + move N images atomically</span>{`
`}<span className="tok-k">GET</span>    /tags/                       <span className="tok-c"># user's tag list</span>{`
`}<span className="tok-k">POST</span>   /images/{`{id}`}/tags            <span className="tok-c"># attach by id or label</span>{`
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
