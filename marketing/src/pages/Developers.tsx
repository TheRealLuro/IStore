export default function Developers() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Developers</span>
          <h1>Open source. Auditable. Yours to fork.</h1>
          <p className="lead">
            neuthek is built in the open. The whole backend, schema,
            migrations, compression policy, and tests live in a public
            repository you can clone, audit, and extend.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <span className="eyebrow">Five-minute setup</span>
            <h2>Boot the stack locally.</h2>
            <p style={{ marginTop: 16 }}>
              Setup scripts are provided for both Windows and Unix. They
              create a virtualenv, install dependencies, optionally install
              the ML extras, and start the API.
            </p>
          </div>
          <div className="code">{`# Linux / macOS
git clone <your-fork>
cd neuthek
./scripts/setup.sh --ml --start

# Windows PowerShell
.\\scripts\\setup.ps1 -Ml -Start

# Then in another shell:
docker compose up -d
.venv/bin/python -m alembic upgrade head
# API now on http://localhost:8000/docs`}</div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>The actual stack.</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            Every entry below is a real dependency in the repository. No
            placeholder vendors, no aspirational integrations.
          </p>
          <div className="cards">
            <div className="card">
              <h3>Backend</h3>
              <p>
                FastAPI, SQLAlchemy (async), Alembic for migrations,
                fastapi-users for JWT auth, argon2-cffi for password
                hashing, pydantic for schemas.
              </p>
            </div>
            <div className="card">
              <h3>Storage</h3>
              <p>
                PostgreSQL 16 with pgvector for embeddings, MinIO for
                object storage, Redis for rate limiting and background
                queues.
              </p>
            </div>
            <div className="card">
              <h3>Vision</h3>
              <p>
                OpenCLIP (ViT-L-14 default), PyTorch with CUDA / Intel
                XPU / Apple MPS dispatch, Pillow for decoding, optional
                pillow-heif and imagecodecs for AVIF / JPEG XL.
              </p>
            </div>
            <div className="card">
              <h3>Frontend</h3>
              <p>
                Vite + React + TypeScript, TanStack Query, Zustand for
                auth state, Geist as the type stack. Same toolchain as
                this site you're reading.
              </p>
            </div>
            <div className="card">
              <h3>Testing</h3>
              <p>
                Pytest with pytest-asyncio for the backend; tests cover
                health, codec dispatch, resize behavior, the compression
                policy decisions, and the upload validation hardening.
              </p>
            </div>
            <div className="card">
              <h3>Infra</h3>
              <p>
                Docker Compose for the data layer, an optional
                <code> docker-compose.intel.yml</code> overlay for Intel
                iGPU + NPU device passthrough, and Render-friendly static
                deploys for the marketing surface.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <span className="eyebrow">API surface</span>
            <h2>Tiny, predictable, documented.</h2>
            <p style={{ marginTop: 16 }}>
              FastAPI ships interactive OpenAPI docs at <code>/docs</code>.
              The image and search routes are gated by JWT and scoped to
              the authenticated user.
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
          <h2>Want to contribute?</h2>
          <p style={{ marginTop: 12, maxWidth: 640 }}>
            Pull requests are welcome. We keep changes small, prefer adding
            tests alongside behavior changes, and call out privacy impact
            in every PR that touches user data, embeddings, or future face
            workflows.
          </p>
          <p style={{ marginTop: 24 }}>
            <a href="https://github.com" className="btn btn--ghost btn--lg" style={{ borderColor: "rgba(255,255,255,0.3)", color: "var(--surface)" }}>
              View source on GitHub
            </a>
          </p>
          <p style={{ marginTop: 12, fontSize: 13, color: "rgba(255,255,255,0.5)" }}>
            (Repository link wires up after the public release.)
          </p>
        </div>
      </section>
    </>
  );
}
