import { Link } from "react-router-dom";

export default function Pricing() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Pricing</span>
          <h1>Free to self-host. Hosted pricing comes with the launch.</h1>
          <p className="lead">
            We won't charge for what you can run yourself. The hosted
            plans are still being scoped — we'll publish them before the
            hosted version goes live.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div className="card">
            <span className="eyebrow">Self-host</span>
            <h2 style={{ fontSize: 36, margin: "8px 0" }}>$0</h2>
            <p>The full open-source build, forever. Bring your own hardware.</p>
            <ul style={{ marginTop: 20, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
              <li>Every feature on the Features page</li>
              <li>Every roadmap item, when shipped</li>
              <li>Community support via the issue tracker</li>
              <li>Source available — clone, audit, fork</li>
            </ul>
            <p style={{ marginTop: 20 }}>
              <Link to="/developers" className="btn btn--primary">Setup guide</Link>
            </p>
          </div>

          <div className="card" style={{ borderColor: "var(--ink)" }}>
            <span className="eyebrow">Hosted (planned)</span>
            <h2 style={{ fontSize: 36, margin: "8px 0" }}>TBD</h2>
            <p>
              We'll publish hosted plans before the public launch.
              Expect a free tier sized for personal use and paid tiers
              for heavier libraries.
            </p>
            <ul style={{ marginTop: 20, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
              <li>Same product, run by us</li>
              <li>HTTPS, automated backups, off-box object storage</li>
              <li>GPU inference for embeddings</li>
              <li>Same per-user isolation as self-host</li>
            </ul>
            <p style={{ marginTop: 20 }}>
              <Link to="/waitlist" className="btn btn--primary">Get notified</Link>
            </p>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>FAQ.</h2>
          <div className="cards">
            <div className="card">
              <h3>Is the open-source build really free?</h3>
              <p>
                Yes. You can clone the repo today, run it on your own
                hardware, and use every shipped feature without paying
                anyone. The license details are in the source repo.
              </p>
            </div>
            <div className="card">
              <h3>When will hosted launch?</h3>
              <p>
                There is no public date yet. We'd rather ship the
                pre-launch checklist (encryption, deletion, export,
                consent gates, audit log, backups) properly than rush.
              </p>
            </div>
            <div className="card">
              <h3>Will paid features ever be locked out of self-host?</h3>
              <p>
                No. The self-host build will always include every
                feature in the open-source repo. Hosted-only value
                is the operational work — backups, uptime, GPU
                provisioning — not the code.
              </p>
            </div>
            <div className="card">
              <h3>Will my self-host data be portable to hosted later?</h3>
              <p>
                Portability both directions is on the pre-launch
                checklist for the hosted version. The schema and the
                blob layout are designed to migrate cleanly.
              </p>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
