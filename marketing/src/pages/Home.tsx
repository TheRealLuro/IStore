import { Link } from "react-router-dom";
import TechCarousel from "../components/TechCarousel";
import Reveal from "../components/Reveal";
import { usePageSeo, webPage } from "../seo";

export default function Home() {
  usePageSeo({
    title: "neuthek — AI-aware personal cloud storage",
    description:
      "neuthek is the next best cloud storage solution: AI-aware personal cloud in active development. Semantic search by what you remember, content-aware compression, privacy-first design. We don't collect what we don't need, don't train AI on your content, don't sell or share your data. Self-host + hosted both planned — join the waitlist.",
    path: "/",
    jsonLd: [
      webPage({
        name: "neuthek — AI-aware personal cloud storage",
        description:
          "Home page for neuthek, the AI-aware personal cloud storage product in active development. Semantic image search via OpenCLIP, content-aware compression, BIPA-compliant face recognition, five-provider cloud-sync ingest (Google Drive, Dropbox, iCloud, Proton Drive, MEGA), encrypted in transit + at rest, end-to-end encryption on the roadmap.",
        path: "/",
        about: "AI-aware personal cloud storage",
      }),
    ],
  });
  return (
    <>
      <section className="hero">
        <div className="container hero__stagger">
          <span className="eyebrow">The next best cloud storage solution</span>
          <h1 className="hero__title">
            Storage that <em>thinks for you.</em>
          </h1>
          <p className="lead hero__sub">
            An AI-aware personal cloud. Search by what you remember,
            compression that knows a screenshot from a sunset, and a
            privacy model that doesn't sell you out. In active
            development — join the waitlist for launch.
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
        <Reveal className="container" delay={120}>
          <TechCarousel />
        </Reveal>
      </section>

      <section className="section section--tight">
        <Reveal className="container">
          <div className="privacy-stance" role="note" aria-label="Privacy posture">
            <span className="privacy-stance__icon" aria-hidden="true">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/></svg>
            </span>
            <div>
              <h2 className="privacy-stance__title">
                We don't collect what we don't need. We don't train on it. We don't sell it.
              </h2>
              <p className="privacy-stance__body">
                Three commitments built into the product. The only
                thing we require is an email for the launch ping. The
                vision models are open-weights, frozen, never
                fine-tuned on your library. We don't sell or share —
                and we honor the Global Privacy Control signal. Every
                AI feature is opt-in; face recognition has its own
                BIPA-compliant consent step.
              </p>
              <p className="privacy-stance__body" style={{ marginTop: 12 }}>
                <strong>End-to-end encryption is on the roadmap.</strong>{" "}
                Today we encrypt in transit and at rest, holding the
                keys so AI can run for you. Mega/Proton-class client-
                side encryption ships feature by feature — and we
                won't call neuthek "end-to-end encrypted" until it
                actually is. (That false claim is what cost Zoom an
                FTC order in 2020.){" "}
                <Link to="/privacy">Read the Privacy page →</Link>
              </p>
            </div>
          </div>
        </Reveal>
      </section>

      <section className="section">
        <div className="container">
          <Reveal>
            <span className="eyebrow">What we're building</span>
            <h2>Three things, done right.</h2>
          </Reveal>
          <div className="cards">
            <Reveal as="div" className="card" delay={0}>
              <div className="card__icon">01</div>
              <h3>Search by memory</h3>
              <p>
                Type "snowy roof at sunset" — the right photos come
                back. No filenames, no tagging. CLIP-class embeddings
                + a vector index match the way you actually remember.
              </p>
            </Reveal>
            <Reveal as="div" className="card" delay={100}>
              <div className="card__icon">02</div>
              <h3>Content-aware compression</h3>
              <p>
                Photos compress with modern codecs; screenshots,
                docs, and icons fall to lossless automatically —
                because every pixel of text matters even when a beach
                photo doesn't.
              </p>
            </Reveal>
            <Reveal as="div" className="card" delay={200}>
              <div className="card__icon">03</div>
              <h3>Privacy by design</h3>
              <p>
                Your bytes, embeddings, and search history never train
                a public model. Self-host on your own hardware or use
                managed hosting — your data, either way.
              </p>
            </Reveal>
          </div>
        </div>
      </section>

      <section className="section section--ink">
        <div className="container split">
          <Reveal>
            <span className="eyebrow">
              Why we're building it
            </span>
            <h2>The big-cloud trade is bad.</h2>
            <p style={{ marginTop: 16 }}>
              You hand over the bytes, the embeddings, the EXIF, the
              social graph — and get back a search box that still
              can't find "the one with the dog at the beach."
            </p>
            <p style={{ marginTop: 12 }}>
              neuthek uses the same modern stack the big providers do
              — CLIP-class embeddings, vector search, content-aware
              codecs — with one rule: the data, the models, and the
              keys stay yours.
            </p>
          </Reveal>
          <Reveal delay={120}>
            <dl className="kv">
              <dt>Auth</dt><dd>JWT with TOTP 2FA</dd>
              <dt>Storage</dt><dd>S3-compatible object storage</dd>
              <dt>Database</dt><dd>PostgreSQL + pgvector</dd>
              <dt>Embeddings</dt><dd>OpenCLIP ViT-L-14, 768 dims</dd>
              <dt>Compression</dt><dd>WebP / WebP lossless / AVIF / JPEG XL</dd>
              <dt>Originals</dt><dd>Configurable retention window</dd>
              <dt>Goal</dt><dd>Hosted launches first; open-source self-host planned</dd>
            </dl>
          </Reveal>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <Reveal>
            <h2>Two ways to use it, both coming.</h2>
            <p className="lead" style={{ marginTop: 12 }}>
              Same product, two delivery models — both in active
              development, neither public yet.
            </p>
          </Reveal>
          <div className="cards">
            <Reveal as="div" className="card" delay={0}>
              <div className="card__icon">SH</div>
              <h3>Self-host (planned)</h3>
              <p>
                Boot the whole stack with Docker Compose on hardware
                you own. Free, forever, for personal use.
              </p>
              <p style={{ marginTop: 16 }}>
                <Link to="/hosting" className="btn btn--ghost">Learn more</Link>
              </p>
            </Reveal>
            <Reveal as="div" className="card" delay={120}>
              <div className="card__icon">MA</div>
              <h3>Managed (planned)</h3>
              <p>
                The same stack on infrastructure we operate — backups,
                GPU inference, HTTPS, account lifecycle — same privacy
                posture. Pricing announced at launch.
              </p>
              <p style={{ marginTop: 16 }}>
                <Link to="/waitlist" className="btn btn--primary">Join waitlist</Link>
              </p>
            </Reveal>
          </div>
        </div>
      </section>
    </>
  );
}
