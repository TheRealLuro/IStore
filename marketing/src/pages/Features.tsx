export default function Features() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Features</span>
          <h1>Built for the way you actually look at your photos.</h1>
          <p className="lead">
            Every capability listed here is being built into the
            engine right now. Nothing on this page is shipped —
            "planned" or "in development" is the truthful label for
            all of it.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <span className="eyebrow">Search</span>
            <h2>Search by what you remember.</h2>
            <p style={{ marginTop: 16 }}>
              Every uploaded image will be encoded into a 768-dimension
              OpenCLIP embedding (ViT-L-14 by default). Your query text
              gets encoded by the same model, and pgvector returns the
              nearest matches by cosine similarity.
            </p>
            <p style={{ marginTop: 12 }}>
              Filename, tags, and EXIF metadata are still stored for
              filtering — but the point is you don't need them.
              "Receipt with a coffee stain" or "the trail with the
              red bridge" is enough.
            </p>
          </div>
          <div className="code">{`# How search will work (engine-level)
client> "snowy roof at sunset"
        ↓ OpenCLIP text encoder
        ↓ 768-dim vector
server> pgvector  <=>  image embeddings
        ↓ cosine similarity
        ↓ top-N owned by current user
client> renders gallery sorted by relevance`}</div>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div className="code">{`# Compression policy (deterministic)
photo            -> WebP q=82, max 4096px
screenshot       -> WebP lossless
document         -> WebP lossless
illustration     -> WebP lossless
icon             -> WebP lossless
low-confidence   -> WebP q=82 (fallback)

# Optional codecs picked up automatically
# when the host has them installed:
AVIF (pillow-heif), JPEG XL (imagecodecs)`}</div>
          <div>
            <span className="eyebrow">Compression</span>
            <h2>Smart by default. Lossless when it matters.</h2>
            <p style={{ marginTop: 16 }}>
              The compression policy is content-aware. A vacation
              photo gets quality-82 WebP; a screenshot or scanned
              document falls to lossless WebP because every pixel of
              text matters.
            </p>
            <p style={{ marginTop: 12 }}>
              Originals will be retained for a configurable window
              (default 30 days in the engine's schema). After expiry,
              downloads of the "original" route serve the
              high-quality compressed variant with an
              <code> X-Original-Expired</code> header so the client
              knows.
            </p>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <span className="eyebrow">Privacy</span>
            <h2>Your bytes do not leave your box.</h2>
            <p style={{ marginTop: 16 }}>
              When self-host launches, every part of neuthek — the
              database, object storage, Redis, the FastAPI app, the
              OpenCLIP runtime — will run on infrastructure you
              operate. One <code>docker compose up -d</code> when
              the time comes.
            </p>
            <p style={{ marginTop: 12 }}>
              Image queries and search are scoped to the
              authenticated user's <code>user_id</code> at the
              database layer. Buckets are separated by purpose
              (originals / served / faces) so a future lifecycle
              policy can target each independently.
            </p>
          </div>
          <div>
            <dl className="kv">
              <dt>User isolation</dt>
              <dd>Every read and write filters by the JWT subject's user_id.</dd>
              <dt>Auth</dt>
              <dd>fastapi-users with JWT + optional TOTP 2FA.</dd>
              <dt>Buckets</dt>
              <dd>originals / served / faces — separated for lifecycle control.</dd>
              <dt>EXIF</dt>
              <dd>Originals retain EXIF as uploaded; opt-in stripper planned before public release.</dd>
              <dt>Embeddings</dt>
              <dd>Stored in your Postgres only; never shipped to third parties.</dd>
            </dl>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <span className="eyebrow">Honest scope</span>
          <h2>What it won't do (at least at first).</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            We'd rather under-promise.
          </p>
          <div className="cards">
            <div className="card">
              <h3>Face identification</h3>
              <p>
                The engine records a face-likelihood score on each
                upload. It will not cluster identities or run
                "who is this?" workflows at launch. That feature
                requires explicit consent gating before it ships.
              </p>
            </div>
            <div className="card">
              <h3>Document &amp; video</h3>
              <p>
                The HTTP API is image-focused. Document and video
                ingestion are on the multi-data-type roadmap, but
                photo-first is the public launch posture.
              </p>
            </div>
            <div className="card">
              <h3>Production hardening</h3>
              <p>
                TLS termination, malware scanning, automated
                backups, managed CI/CD, and observability dashboards
                are operational concerns the hosted launch will
                cover. Self-hosters take those on themselves.
              </p>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
