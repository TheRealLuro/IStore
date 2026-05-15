import { Link } from "react-router-dom";
import TechCarousel from "../components/TechCarousel";

export default function Home() {
  return (
    <>
      <section className="hero">
        <div className="container fade-in">
          <span className="eyebrow">The next best cloud storage solution</span>
          <h1 className="hero__title">
            Storage that <em>thinks for you.</em>
          </h1>
          <p className="lead hero__sub">
            neuthek is the open-source, AI-aware cloud storage stack
            you can run yourself — semantic image search by what you
            remember, content-aware compression, privacy by design.
            The managed hosted version is on the way; the source code
            is available today.
          </p>
          <div className="hero__ctas">
            <Link to="/developers" className="btn btn--primary btn--lg">
              Run it yourself
            </Link>
            <Link to="/waitlist" className="btn btn--ghost btn--lg">
              Join the waitlist
            </Link>
          </div>
        </div>
        <div className="container">
          <TechCarousel />
        </div>
      </section>

      <section className="section">
        <div className="container">
          <span className="eyebrow">What it does today</span>
          <h2>Three things, well.</h2>
          <div className="cards">
            <div className="card">
              <div className="card__icon">01</div>
              <h3>Search by memory</h3>
              <p>
                Type "snowy roof at sunset" and pgvector returns the closest
                matches from your library using OpenCLIP embeddings. No
                filename, no manual tagging required.
              </p>
            </div>
            <div className="card">
              <div className="card__icon">02</div>
              <h3>Content-aware compression</h3>
              <p>
                Photos compress with WebP at quality 82, capped at 4096px on
                the longest side. Screenshots, documents, illustrations, and
                icons fall to lossless WebP automatically.
              </p>
            </div>
            <div className="card">
              <div className="card__icon">03</div>
              <h3>Yours, on your server</h3>
              <p>
                Postgres, MinIO, and Redis run under Docker Compose on
                hardware you own. No cloud account, no EXIF mining, no model
                trained on your data.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="section section--ink">
        <div className="container split">
          <div>
            <span className="eyebrow" style={{ color: "rgba(255,255,255,0.5)" }}>
              Why we built it
            </span>
            <h2>The big-cloud trade is bad.</h2>
            <p style={{ marginTop: 16 }}>
              You hand over the bytes, the embeddings, the EXIF, the social
              graph — and in return you get a search box that still can't
              find "the one with the dog at the beach."
            </p>
            <p style={{ marginTop: 12 }}>
              neuthek runs the same modern stack the big providers use —
              CLIP-class embeddings, vector search, content-aware codecs —
              but the data, the models, and the keys all stay on a machine
              you control.
            </p>
          </div>
          <div>
            <dl className="kv" style={{ color: "rgba(255,255,255,0.78)" }}>
              <dt>Auth</dt><dd>JWT via FastAPI Users</dd>
              <dt>Storage</dt><dd>MinIO (S3-compatible)</dd>
              <dt>Database</dt><dd>PostgreSQL 16 + pgvector</dd>
              <dt>Embeddings</dt><dd>OpenCLIP ViT-L-14, 768 dims</dd>
              <dt>Compression</dt><dd>WebP / WebP lossless / AVIF / JPEG XL (when codecs available)</dd>
              <dt>Originals</dt><dd>Retained on a configurable expiry (default 30 days)</dd>
              <dt>License</dt><dd>Open source — clone, fork, audit</dd>
            </dl>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>Two ways to run it.</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            The same product, two delivery models. Pick whichever fits your
            comfort with infrastructure.
          </p>
          <div className="cards">
            <div className="card">
              <div className="card__icon">SH</div>
              <h3>Self-host (today)</h3>
              <p>
                Clone the repo, run <code>docker compose up -d</code>, and
                you're live on a server you own. Free for personal use, the
                full stack is yours.
              </p>
              <p style={{ marginTop: 16 }}>
                <Link to="/hosting" className="btn btn--ghost">Learn more</Link>
              </p>
            </div>
            <div className="card">
              <div className="card__icon">MA</div>
              <h3>Managed (coming soon)</h3>
              <p>
                When the hosted version goes live, we'll run the same stack
                for you — backups, GPU inference, HTTPS — with the same
                privacy posture. No date yet; join the waitlist for the
                launch ping.
              </p>
              <p style={{ marginTop: 16 }}>
                <Link to="/waitlist" className="btn btn--primary">Join waitlist</Link>
              </p>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
