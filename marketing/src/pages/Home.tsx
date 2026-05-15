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
            neuthek is the AI-aware personal cloud we're building from
            scratch — semantic search by what you remember,
            content-aware compression, and a privacy-first design.
            Nothing is released yet. The waitlist is where you find
            out first.
          </p>
          <div className="hero__ctas">
            <Link to="/waitlist" className="btn btn--primary btn--lg">
              Join the waitlist
            </Link>
            <Link to="/roadmap" className="btn btn--ghost btn--lg">
              See the roadmap
            </Link>
          </div>
        </div>
        <div className="container">
          <TechCarousel />
        </div>
      </section>

      <section className="section">
        <div className="container">
          <span className="eyebrow">What we're building</span>
          <h2>Three things, done right.</h2>
          <div className="cards">
            <div className="card">
              <div className="card__icon">01</div>
              <h3>Search by memory</h3>
              <p>
                Type "snowy roof at sunset" and the right photos come
                back — no filenames, no manual tagging. We use
                CLIP-class embeddings and a vector index so the
                search matches the way you actually remember things.
              </p>
            </div>
            <div className="card">
              <div className="card__icon">02</div>
              <h3>Content-aware compression</h3>
              <p>
                Photos compress with modern codecs at sensible
                defaults. Screenshots, documents, illustrations, and
                icons fall to lossless automatically — because every
                pixel of text matters even if a beach photo doesn't.
              </p>
            </div>
            <div className="card">
              <div className="card__icon">03</div>
              <h3>Privacy by design</h3>
              <p>
                Your bytes, your embeddings, your search history —
                none of it trains a public model. Two ways to use it
                are planned: self-host on your own hardware, or
                managed hosting we run for you. Your data, either
                way.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="section section--ink">
        <div className="container split">
          <div>
            <span className="eyebrow" style={{ color: "rgba(255,255,255,0.5)" }}>
              Why we're building it
            </span>
            <h2>The big-cloud trade is bad.</h2>
            <p style={{ marginTop: 16 }}>
              You hand over the bytes, the embeddings, the EXIF, the
              social graph — and in return you get a search box that
              still can't find "the one with the dog at the beach."
            </p>
            <p style={{ marginTop: 12 }}>
              neuthek is being built around the same modern stack the
              big providers use — CLIP-class embeddings, vector
              search, content-aware codecs — but with one rule: the
              data, the models, and the keys stay yours.
            </p>
          </div>
          <div>
            <dl className="kv" style={{ color: "rgba(255,255,255,0.78)" }}>
              <dt>Auth</dt><dd>JWT with TOTP 2FA</dd>
              <dt>Storage</dt><dd>S3-compatible object storage</dd>
              <dt>Database</dt><dd>PostgreSQL + pgvector</dd>
              <dt>Embeddings</dt><dd>OpenCLIP ViT-L-14, 768 dims</dd>
              <dt>Compression</dt><dd>WebP / WebP lossless / AVIF / JPEG XL</dd>
              <dt>Originals</dt><dd>Configurable retention window</dd>
              <dt>Goal</dt><dd>Open-source on launch, hosted shortly after</dd>
            </dl>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>Two ways to use it, both coming.</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            Same product, two delivery models. Both are in active
            development; neither is publicly available yet.
          </p>
          <div className="cards">
            <div className="card">
              <div className="card__icon">SH</div>
              <h3>Self-host (planned)</h3>
              <p>
                When the open-source build is released, you'll be
                able to clone the repo, boot the stack with Docker
                Compose, and run everything on hardware you own.
                Free, forever, for personal use.
              </p>
              <p style={{ marginTop: 16 }}>
                <Link to="/hosting" className="btn btn--ghost">Learn more</Link>
              </p>
            </div>
            <div className="card">
              <div className="card__icon">MA</div>
              <h3>Managed (planned)</h3>
              <p>
                The hosted plan will run the same stack on
                infrastructure we operate — backups, GPU inference,
                HTTPS, account lifecycle handled — with the same
                privacy posture. Plans published with the launch.
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
