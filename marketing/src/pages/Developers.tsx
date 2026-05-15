import { Link } from "react-router-dom";

export default function Developers() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Developers</span>
          <h1>Auditable. Forkable. Soon.</h1>
          <p className="lead">
            When we release, the whole backend, schema, migrations,
            compression policy, and tests will live in a public
            repository you can clone, audit, and extend. The source
            isn't published yet — we want it polished before it goes
            out.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>The stack we're building on.</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            Every name below is a real dependency in our development
            tree. No placeholder vendors, no aspirational integrations.
          </p>
          <div className="cards">
            <div className="card">
              <h3>Backend</h3>
              <p>
                FastAPI, SQLAlchemy (async), Alembic for migrations,
                fastapi-users for JWT auth, argon2-cffi for password
                and token hashing, pydantic for schemas.
              </p>
            </div>
            <div className="card">
              <h3>Storage</h3>
              <p>
                PostgreSQL 16 with pgvector for embeddings,
                S3-compatible object storage (MinIO in development),
                Redis for rate limiting and background queues.
              </p>
            </div>
            <div className="card">
              <h3>Vision</h3>
              <p>
                OpenCLIP (ViT-L-14 default), PyTorch with CUDA /
                Intel XPU / Apple MPS dispatch, Pillow for decoding,
                optional pillow-heif and imagecodecs for AVIF /
                JPEG XL.
              </p>
            </div>
            <div className="card">
              <h3>Frontend</h3>
              <p>
                Vite + React + TypeScript, TanStack Query, Zustand
                for auth state, Geist as the type stack. Same
                toolchain as this site you're reading.
              </p>
            </div>
            <div className="card">
              <h3>Testing</h3>
              <p>
                Pytest with pytest-asyncio for the backend. Tests
                cover health, codec dispatch, resize behavior, the
                compression policy decisions, and the upload
                validation hardening.
              </p>
            </div>
            <div className="card">
              <h3>Infra</h3>
              <p>
                Docker Compose for the data layer, optional Intel
                iGPU + NPU device-passthrough overlay, and a small
                static surface (this site) that can be hosted
                independently.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <span className="eyebrow">API surface (planned)</span>
            <h2>Small, predictable, documented.</h2>
            <p style={{ marginTop: 16 }}>
              FastAPI ships interactive OpenAPI docs at
              <code> /docs</code>. The image and search routes will
              be gated by JWT and scoped to the authenticated user.
            </p>
            <p style={{ marginTop: 12, color: "var(--ink-3)" }}>
              The exact endpoint shapes below are what the engine
              currently exposes internally. The published API may
              differ once the release passes design review.
            </p>
          </div>
          <div className="code">{`POST   /auth/jwt/login           # JWT login
POST   /auth/register            # account create
GET    /users/me                 # current user

POST   /images/                  # upload
GET    /images/                  # list (filters: scene,
                                 #   content_type, tag, indoor_outdoor)
GET    /images/{id}              # metadata
GET    /images/{id}/original     # original bytes
GET    /images/{id}/served       # compressed served bytes
DELETE /images/{id}              # soft delete

GET    /search/?q=<text>         # semantic search
                                 # (requires [ml] extras)`}</div>
        </div>
      </section>

      <section className="section section--ink">
        <div className="container">
          <h2>Want to contribute when it opens?</h2>
          <p style={{ marginTop: 12, maxWidth: 640 }}>
            When the public repository lands, pull requests will be
            welcome. We aim to keep changes small, prefer adding
            tests alongside behavior changes, and call out privacy
            impact in every PR that touches user data, embeddings,
            or future face workflows.
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
