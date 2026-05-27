import { Link } from "react-router-dom";
import TechCarousel from "../components/TechCarousel";
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
        <div className="container fade-in">
          <span className="eyebrow">The next best cloud storage solution</span>
          <h1 className="hero__title">
            Storage that <em>thinks for you.</em>
          </h1>
          <p className="lead hero__sub">
            neuthek is the AI-aware personal cloud we're building from
            scratch — semantic search by what you remember,
            content-aware compression, and a privacy-first design.
            The product isn't released yet — the marketing site is
            public, and the waitlist tells you when the app launches.
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

      <section className="section section--tight">
        <div className="container">
          <div className="privacy-stance" role="note" aria-label="Privacy posture">
            <span className="privacy-stance__icon" aria-hidden="true">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/></svg>
            </span>
            <div>
              <h2 className="privacy-stance__title">
                We don't collect what we don't need. We don't train on it. We don't sell it.
              </h2>
              <p className="privacy-stance__body">
                Three commitments, written into the product. We don't
                collect data we don't need — an email address for the
                launch ping is the entire mandatory collection today.
                We don't train AI on your content — the vision models
                are open-weights, downloaded once, frozen, never
                fine-tuned. We don't sell or share personal information;
                the Global Privacy Control browser signal is honored as
                an opt-out under CCPA. AI features (search, summaries,
                face recognition) are opt-in per scope; face
                recognition has a separate BIPA-compliant consent step.
              </p>
              <p className="privacy-stance__body" style={{ marginTop: 12 }}>
                <strong>End-to-end encryption is on the roadmap.</strong>{" "}
                Today neuthek is encrypted in transit and at rest, with
                the server holding the keys so AI can run on your
                behalf. Mega/Proton-class client-side encryption will
                ship feature by feature, with very clear "this feature
                loses AI capability when you turn on E2E" trade-offs.
                Until it lands we will <em>not</em> call neuthek
                end-to-end encrypted — saying that before it's true is
                exactly the misrepresentation the FTC sanctioned Zoom
                for in 2020. See{" "}
                <Link to="/privacy">Privacy</Link> for the full picture.
              </p>
            </div>
          </div>
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
            <span className="eyebrow">
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
            <dl className="kv">
              <dt>Auth</dt><dd>JWT with TOTP 2FA</dd>
              <dt>Storage</dt><dd>S3-compatible object storage</dd>
              <dt>Database</dt><dd>PostgreSQL + pgvector</dd>
              <dt>Embeddings</dt><dd>OpenCLIP ViT-L-14, 768 dims</dd>
              <dt>Compression</dt><dd>WebP / WebP lossless / AVIF / JPEG XL</dd>
              <dt>Originals</dt><dd>Configurable retention window</dd>
              <dt>Goal</dt><dd>Hosted launches first; open-source self-host planned</dd>
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
                When the self-host build launches, you'll be able
                to boot the stack with Docker Compose and run
                everything on hardware you own. Free, forever, for
                personal use.
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
