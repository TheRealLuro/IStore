/* Plain-English summary of how neuthek handles your data. The
   canonical, GDPR/CCPA/BIPA-grade Privacy Notice lives in PRIVACY.md
   in the source repo and gets published in full at hosted launch.
   This page is a readable overview kept in sync with what is
   actually in the engine today. */

import { Link } from "react-router-dom";

const LAST_UPDATED = "May 17, 2026";

export default function Privacy() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Privacy</span>
          <h1>What we hold, why, how long, and how to make it stop.</h1>
          <p className="lead">
            Plain English overview of how neuthek treats your data.
            The product itself isn't released yet — these are the
            commitments and what's already implemented in the engine
            today. A formal Privacy Notice gets published with the
            hosted launch.
          </p>
          <p style={{ marginTop: 12, fontSize: 13, color: "var(--ink-3)" }}>
            Last updated: {LAST_UPDATED}.
          </p>
        </div>
      </section>

      {/* ===== Hard guarantees ===== */}
      <section className="section">
        <div className="container">
          <h2>The promises we built the product around.</h2>
          <p className="lead" style={{ marginTop: 12, maxWidth: 720 }}>
            These aren't aspirations. They're structural decisions —
            the kind that would require redesigning the product to
            break.
          </p>
          <ul style={{ marginTop: 20, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8, maxWidth: 760 }}>
            <li><strong>No AI training on your library.</strong> Your photos, videos, documents, embeddings, summaries, and search history are never used to train any AI model — not ours, not anyone else's. The vision models we run (OpenCLIP, Florence-2, RetinaFace, ArcFace) are pre-trained with frozen weights. We never fine-tune them on user data.</li>
            <li><strong>No ads. Anywhere. Ever.</strong> There won't be advertising in the gallery, in emails, in search results, or in shared previews.</li>
            <li><strong>No data sales.</strong> We don't sell your data to brokers, ad networks, partners, or anyone else. There's no business case for it because we charge for the hosted product.</li>
            <li><strong>Every AI feature is opt-in.</strong> Face recognition, location retention, AI summaries, semantic search — each one is off by default and revocable at any time.</li>
            <li><strong>You can leave with everything.</strong> One-click export downloads your full library — files, summaries, embeddings, face data, audit history — as a ZIP.</li>
            <li><strong>Delete means delete.</strong> When you delete your account, every file, thumbnail, embedding, summary, face template, and audit row is purged in one transaction. An integration test runs on every commit to prove it.</li>
          </ul>
        </div>
      </section>

      {/* ===== This site ===== */}
      <section className="section">
        <div className="container">
          <h2>This marketing site.</h2>
          <p style={{ marginTop: 12, maxWidth: 760 }}>
            No third-party analytics. No advertising cookies. No
            tracking pixels. The site loads Google Fonts (for the
            Geist typeface) and that's the only third-party request.
          </p>
          <p style={{ marginTop: 12, maxWidth: 760 }}>
            The <Link to="/waitlist">waitlist form</Link> sends your
            email address to our server, where it's stored with a
            verification token and (optionally) a one-line use case.
            You'll get a one-click verification email; until you
            click it, the row stays "unverified" and won't get the
            launch ping. You can request removal at any time —
            replying to any email from us, or clicking the
            unsubscribe link on a future newsletter, removes you
            from the list.
          </p>
        </div>
      </section>

      {/* ===== Working today ===== */}
      <section className="section">
        <div className="container">
          <h2>What's already in the engine.</h2>
          <p className="lead" style={{ marginTop: 12, maxWidth: 720 }}>
            These items aren't "coming" — they're working in the
            development build today, with tests.
          </p>
          <ul style={{ marginTop: 20, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8, maxWidth: 760 }}>
            <li><strong>Encrypted in transit and at rest.</strong> Every connection uses HTTPS; every file on disk is encrypted; database backups are encrypted before being written. The encryption keys live on the server you control — they never reach the browser.</li>
            <li><strong>Per-user database isolation.</strong> Every row in every multi-tenant table is fenced by Postgres Row-Level Security at the database layer. Even a bug in the app code can't return another user's row.</li>
            <li><strong>EXIF and GPS stripped by default.</strong> Location coordinates and camera serial-number metadata are removed from the original file before it's stored. You can opt in to keep them if you want.</li>
            <li><strong>Account export.</strong> One-click ZIP of everything — files, summaries, embeddings, persons, faces, audit history, consent records. Rate-limited to one full export every 24 hours.</li>
            <li><strong>Hard account deletion.</strong> Deleting your account purges every byte across every storage bucket, every database table, every cache. The integration test proves 0 rows + 0 objects remain.</li>
            <li><strong>Append-only audit log.</strong> Every security-sensitive action — sign-in, delete, consent change, share, admin action — is recorded in a log the application can append to but can never modify or delete. A database trigger blocks any tampering attempt.</li>
            <li><strong>Consent before signup.</strong> The consent prompt for AI features, face recognition, and location data appears at sign-up — not buried in settings later. Granular per scope, revocable any time.</li>
            <li><strong>Consent log with full audit trail.</strong> Every consent grant or withdrawal is written with timestamp, IP address, user-agent, scope, policy version, and the literal signature text you typed.</li>
            <li><strong>Opt-in face recognition.</strong> Off by default. When you enable it, faces are detected and clustered into Me + people you tag. Embeddings stay on your server; revoking consent deletes them immediately.</li>
            <li><strong>Brute-force lockout.</strong> Repeated wrong passwords trigger an automatic lockout with exponential backoff. Every download link expires within 5 minutes.</li>
            <li><strong>Soft Trash for 30 days.</strong> Deleted files sit in Trash before permanent removal. Mistakes are recoverable; intentional deletes go through.</li>
            <li><strong>30-day account-delete grace.</strong> When you schedule an account delete, the row stays for 30 days before the sweeper purges it. Cancel within that window and your account is back.</li>
            <li><strong>No cookies.</strong> The backend never sets a cookie. A test on every commit fails the build if a route ever returns a Set-Cookie header. Authentication uses JWTs in the Authorization header; cross-site forgery is structurally impossible.</li>
          </ul>
        </div>
      </section>

      {/* ===== Self-host vs hosted ===== */}
      <section className="section">
        <div className="container">
          <h2>If you self-host vs. if you use the hosted version.</h2>
          <p style={{ marginTop: 12, maxWidth: 760 }}>
            <strong>Self-host.</strong> When you run neuthek on your
            own hardware (when the self-host build is published — no
            committed date yet), nothing leaves your machine. Files,
            embeddings, summaries, face data all live entirely in your
            Postgres + object storage. The vision pipeline runs on
            your CPU or GPU. You hold the encryption keys.
          </p>
          <p style={{ marginTop: 12, maxWidth: 760 }}>
            <strong>Hosted.</strong> When the hosted service opens
            (also no committed date — the waitlist tells you), your
            data lives in a single-tenant deployment fenced behind
            Row-Level Security at the database layer. We operate the
            infrastructure; you're still the data owner. The Privacy
            Notice for the hosted service gets published in full
            before the service collects real user data.
          </p>
        </div>
      </section>

      {/* ===== What's still being built ===== */}
      <section className="section">
        <div className="container">
          <h2>What's still being built.</h2>
          <p style={{ marginTop: 12, maxWidth: 760 }}>
            A handful of privacy / security items are still in
            flight before public launch. We won't release publicly
            until these are closed:
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8, maxWidth: 760 }}>
            <li><strong>Encryption-key rotation tool.</strong> Today, rotating the main at-rest encryption key would orphan existing data. We're shipping a re-encrypt migration tool so operators can rotate on a schedule.</li>
            <li><strong>Formal threat model.</strong> A STRIDE-style threat-model document for the public-launch release.</li>
            <li><strong>External review.</strong> An outside developer + a security person + a privacy person review the codebase before public launch.</li>
          </ul>
        </div>
      </section>

      {/* ===== Reporting & contact ===== */}
      <section className="section">
        <div className="container">
          <h2>Reporting a security issue.</h2>
          <p style={{ marginTop: 12, maxWidth: 760 }}>
            If you find a security vulnerability, email{" "}
            <a href="mailto:security@neuthek.com">security@neuthek.com</a>{" "}
            with the details. Please don't post real secrets, private
            images, EXIF data, embeddings, or face data in public
            issues or pull requests — we treat any such posting as
            its own incident. A formal disclosure process gets
            published alongside the hosted launch.
          </p>
        </div>
      </section>

      <section className="section section--ink">
        <div className="container">
          <h2>The detailed version.</h2>
          <p style={{ marginTop: 12, maxWidth: 720 }}>
            The page above is the readable summary. The canonical
            Privacy Notice — every field we collect, every legal
            basis, every retention horizon, every data-subject right —
            lives as <code>PRIVACY.md</code> in the source repo and
            gets published in full at hosted launch. The
            data-processing addendum for B2B operators lives in{" "}
            <code>DATA_PROCESSING.md</code>.
          </p>
          <p style={{ marginTop: 24 }}>
            <Link to="/waitlist" className="btn btn--ghost btn--lg"
                  style={{ borderColor: "rgba(255,255,255,0.3)", color: "var(--surface)" }}>
              Join the waitlist for the launch ping
            </Link>
          </p>
        </div>
      </section>
    </>
  );
}
