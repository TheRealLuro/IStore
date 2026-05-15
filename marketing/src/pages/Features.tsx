export default function Features() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Features</span>
          <h1>Built for the way you actually look at your photos.</h1>
          <p className="lead">
            Every capability listed here is implemented in the current
            backend. We don't list what's planned next to what ships today.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <span className="eyebrow">Search</span>
            <h2>Search by what you remember.</h2>
            <p style={{ marginTop: 16 }}>
              When the optional vision pipeline is enabled, every uploaded
              image is encoded into a 768-dimension OpenCLIP embedding
              (ViT-L-14 by default). Your query text is encoded by the same
              model, and pgvector returns the nearest matches by cosine
              similarity.
            </p>
            <p style={{ marginTop: 12 }}>
              Filename, tags, and EXIF metadata are stored too — but the
              point is you don't need them. "Receipt with a coffee stain" or
              "the trail with the red bridge" is enough.
            </p>
          </div>
          <div className="code">{`GET /search/?q=snowy roof at sunset
&lt; 200 OK
[
  { "id": "...", "scene_label": "outdoor",
    "score": 0.314, "tags": ["snow","roof"] },
  { "id": "...", "scene_label": "outdoor",
    "score": 0.298, "tags": ["sunset","sky"] },
  ...
]`}</div>
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

# Optional codecs picked up
# automatically when installed:
AVIF (pillow-heif), JPEG XL (imagecodecs)`}</div>
          <div>
            <span className="eyebrow">Compression</span>
            <h2>Smart by default. Lossless when it matters.</h2>
            <p style={{ marginTop: 16 }}>
              The compression policy is content-aware. A vacation photo gets
              quality-82 WebP; a screenshot or scanned document falls to
              lossless WebP because every pixel of text matters.
            </p>
            <p style={{ marginTop: 12 }}>
              Originals are retained for a configurable window (the schema
              defaults to 30 days). Once they expire, downloads of the
              "original" route serve the high-quality compressed variant
              instead, with an <code>X-Original-Expired</code> header so
              you know.
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
              All of neuthek runs on infrastructure you operate. Postgres,
              MinIO (S3-compatible object storage), Redis, the FastAPI app,
              and the OpenCLIP runtime — all of it boots with one
              <code> docker compose up -d</code>.
            </p>
            <p style={{ marginTop: 12 }}>
              Image queries and search are scoped to the authenticated
              user's <code>user_id</code> at the database layer. Buckets
              are separated by purpose (originals / served / faces) so a
              future lifecycle policy can target each independently.
            </p>
          </div>
          <div>
            <dl className="kv">
              <dt>User isolation</dt>
              <dd>Every read and write filters by the JWT subject's user_id.</dd>
              <dt>Auth</dt>
              <dd>fastapi-users with JWT, configurable lifetime (default 24h).</dd>
              <dt>Buckets</dt>
              <dd>originals / served / faces — separated for lifecycle control.</dd>
              <dt>EXIF</dt>
              <dd>Originals retain EXIF as uploaded; the pipeline never re-uploads.</dd>
              <dt>Embeddings</dt>
              <dd>Stored in your Postgres only; never shipped to third parties.</dd>
            </dl>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <span className="eyebrow">Honest scope</span>
          <h2>What it doesn't do (yet).</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            We won't pretend a feature is shipped when it isn't.
          </p>
          <div className="cards">
            <div className="card">
              <h3>Face identification</h3>
              <p>
                The pipeline records a face-likelihood score on each upload.
                It does not cluster identities or run "who is this?"
                workflows. That work is on the roadmap and will require
                explicit consent before it ships.
              </p>
            </div>
            <div className="card">
              <h3>Document &amp; video</h3>
              <p>
                The HTTP API today is image-focused. Document and video
                ingestion are tracked on the roadmap; treat the current
                product as photo-first.
              </p>
            </div>
            <div className="card">
              <h3>Production hardening</h3>
              <p>
                TLS termination, malware scanning, automated backups,
                managed CI/CD, and observability dashboards are not
                included in the open-source repo. The hosted version will
                handle these for you when it launches.
              </p>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
