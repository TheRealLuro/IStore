import { Link } from "react-router-dom";
import { usePageSeo, webPage, breadcrumbs } from "../seo";

export default function Hosting() {
  usePageSeo({
    title: "Hosting — self-host or managed — neuthek",
    description:
      "Two ways to run neuthek: free open-source self-host via docker-compose, or managed hosted on infrastructure we operate. Plans + pricing for the managed tier are announced with launch — nothing pre-announced.",
    path: "/hosting",
    jsonLd: [
      webPage({
        name: "Hosting — self-host vs managed",
        description:
          "Self-host (docker-compose, free, open-source) vs managed hosted (we run it, pricing announced soon) for neuthek personal cloud storage. Hardware profile recommendations for self-host.",
        path: "/hosting",
        about: "Self-hosted and managed cloud storage hosting options",
      }),
      breadcrumbs([
        { name: "Home", path: "/" },
        { name: "Hosting", path: "/hosting" },
      ]),
    ],
  });
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Hosting</span>
          <h1>Run it yourself, or let us run it for you.</h1>
          <p className="lead">
            Both paths will use the same code, the same database
            schema, and the same compression policy when they
            launch. The difference is who runs the infrastructure
            underneath.
          </p>
          <div className="callout">
            <strong>Heads up:</strong> Neither path is live yet. The
            self-host build will be released as open source; the
            hosted plan is in development. Join the waitlist on the
            <Link to="/waitlist"> waitlist page</Link> to find out
            which lands first.
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div className="card">
            <span className="eyebrow">Self-host (planned)</span>
            <h2 style={{ fontSize: 28, marginBottom: 8 }}>Free when released</h2>
            <p>
              You'll bring a Linux box (or a Windows/macOS
              workstation with Docker) and we'll hand you the entire
              stack — open source, audit-friendly.
            </p>
            <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.7 }}>
              <li>Docker Compose orchestrates Postgres + Redis + S3-compatible object storage</li>
              <li>FastAPI runs on port 8000</li>
              <li>Optional GPU PyTorch wheels for CUDA 12.4 / 12.6 / 12.8</li>
              <li>Intel iGPU + NPU passthrough via opt-in compose overlay</li>
              <li>Backups are your responsibility</li>
            </ul>
          </div>

          <div className="card" style={{ borderColor: "var(--ink)" }}>
            <span className="eyebrow">Managed (planned)</span>
            <h2 style={{ fontSize: 28, marginBottom: 8 }}>Pricing announced soon</h2>
            <p>
              The hosted plan will run the same backend on
              infrastructure we operate, with the privacy posture
              intact. No plans, tiers, or prices announced yet — we'd
              rather price it once we know our actual run costs than
              guess publicly.
            </p>
            <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.7 }}>
              <li>HTTPS, automated backups, off-box object storage</li>
              <li>GPU inference for CLIP embeddings on upload</li>
              <li>Same JWT + per-user isolation as the self-hosted build</li>
              <li>Per-account export and account deletion</li>
              <li><strong>Plans + pricing: announced soon</strong> — waitlist gets the email the moment they go live</li>
            </ul>
            <p style={{ marginTop: 20 }}>
              <Link to="/waitlist" className="btn btn--primary">Join waitlist</Link>
            </p>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>What we expect self-host hardware to look like.</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            None of these will be hard requirements when the
            open-source build releases. They're the comfort zones
            we develop against today.
          </p>
          <table className="compare" style={{ marginTop: 24 }}>
            <thead>
              <tr>
                <th>Profile</th>
                <th>CPU</th>
                <th>RAM</th>
                <th>Vision pipeline</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Personal (no ML)</td>
                <td>Any modern x86 / ARM</td>
                <td>4 GB</td>
                <td>Disabled</td>
                <td>Upload, compress, store. No semantic search.</td>
              </tr>
              <tr>
                <td>Personal (CPU ML)</td>
                <td>4+ cores</td>
                <td>8 GB</td>
                <td>OpenCLIP on CPU</td>
                <td>First embedding per image takes a few seconds.</td>
              </tr>
              <tr>
                <td>Workstation (GPU)</td>
                <td>Modern x86</td>
                <td>16 GB</td>
                <td>CUDA, XPU, or MPS</td>
                <td>NVIDIA, Intel Arc, or Apple Silicon all targeted.</td>
              </tr>
              <tr>
                <td>Server</td>
                <td>8+ cores</td>
                <td>32 GB</td>
                <td>GPU recommended</td>
                <td>Add an off-box backup target for the object store.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
