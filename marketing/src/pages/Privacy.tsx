/* This page describes the privacy posture of the open-source neuthek
   build today, and the commitments the team is making for the hosted
   version before it launches. It is not a substitute for a final
   privacy notice — that document will be published before the hosted
   service collects any user data. */

export default function Privacy() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Privacy</span>
          <h1>What we hold, why, and for how long.</h1>
          <p className="lead">
            This is a plain-English overview. A formal privacy notice
            will be published before the hosted version of neuthek
            collects any user data.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>The marketing site (this page).</h2>
          <p style={{ marginTop: 12 }}>
            We do not use third-party analytics on this site. We do not
            set advertising cookies. The waitlist form on the
            <a href="/#/waitlist"> waitlist page</a> currently saves
            your input only to your own browser's local storage — no
            data leaves your device. When we switch that form to a
            real signup endpoint, we will update this page first.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>The self-hosted build (when it releases).</h2>
          <p style={{ marginTop: 12, maxWidth: 760 }}>
            When the open-source build is published and you run it
            on your own hardware, the project will not transmit
            your images, embeddings, or metadata to us or to any
            third party. The application stack — Postgres, S3-compatible
            object storage, Redis, FastAPI — will run entirely under
            your control.
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8, maxWidth: 760 }}>
            <li>Originals stay on your object store until you delete them or their configurable expiry passes.</li>
            <li>Embeddings, tags, scenes, and content-type labels live in your Postgres only.</li>
            <li>EXIF metadata is preserved on uploaded originals; an opt-in stripper is planned before release.</li>
            <li>JWTs are issued and validated locally with a secret you provide.</li>
            <li>The optional vision pipeline runs on your CPU or GPU; it does not call any remote API.</li>
          </ul>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>Commitments for the hosted version.</h2>
          <p style={{ marginTop: 12, maxWidth: 760 }}>
            Before the hosted plan opens for real users, the following
            will be in place. This is the bar we are holding ourselves
            to — not features we hope to add later.
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8, maxWidth: 760 }}>
            <li>Encryption at rest for all blobs and database backups.</li>
            <li>TLS in transit, with a documented cipher policy.</li>
            <li>Per-account export of the entire library, including embeddings and tags.</li>
            <li>Account deletion that removes originals, served files, embeddings, tags, audit rows, and any future biometric data.</li>
            <li>Explicit consent gates before any biometric or face-clustering surface is enabled.</li>
            <li>An append-only audit log for security-sensitive actions.</li>
            <li>No training of public models on your data — full stop.</li>
          </ul>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>Reporting a security issue.</h2>
          <p style={{ marginTop: 12, maxWidth: 760 }}>
            Please do not post real secrets, private images, EXIF data,
            embeddings, or face data in public issues or pull
            requests. A formal security disclosure process will be
            published alongside the hosted launch.
          </p>
        </div>
      </section>
    </>
  );
}
