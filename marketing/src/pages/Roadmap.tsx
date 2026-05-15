/* The lists below are pulled directly from the project's todo.md.
   Anything labeled "shipped" has a SHIPPED marker in the source repo.
   Anything labeled "building" or "planned" reflects the priority order
   the team has actually committed to — no marketing fiction. */

const shipped = [
  { t: "Sharing primitive (G1)", d: "Per-image grants with email pinning, argon2-hashed tokens, server-enforced 1-day cap for new recipients, and full audit trail." },
  { t: "Settings backlog (1.2)", d: "TOTP-based 2FA, in-app and email notification preferences, per-account plan card, per-scope token expiry." },
  { t: "Admin overlay un-mock (1.3)", d: "Live system, hardware, processes, models, tasks, and logs panels — no mock data." },
  { t: "Upload validation hardening (A1)", d: "Polyglot trailer stripping, dispatch-table validators, forensic quarantine, audit on rejection." },
  { t: "Sprint B — AI quality", d: "Image and search quality improvements landed across the vision pipeline." },
  { t: "Content-aware compression", d: "Photos at WebP q=82 (max 4096px); screenshots, documents, illustrations, icons fall to WebP lossless." },
  { t: "Semantic search", d: "768-dim OpenCLIP ViT-L-14 embeddings with pgvector cosine similarity, scoped per user." },
  { t: "Bulk-action toolbar", d: "Move to / new folder with selection / delete / pick-best-of in the gallery." },
];

const building = [
  { t: "Compliance scaffolding (Sprint C)", d: "Encryption at rest + in transit (A2), secret management (A3), real audit log (A4), full deletion (A5), pre-launch compliance (A6), repo hygiene (A7)." },
  { t: "EXIF / GPS handling (B1)", d: "Strip or surface location/device metadata before public sharing or hosted launch." },
  { t: "Consent before signup (B2)", d: "Explicit consent gates ahead of account creation, ahead of biometric features." },
  { t: "Backend across all major GPU/CPU vendors (F1)", d: "CUDA / Intel XPU / Apple MPS dispatch in place; widening hardware coverage and probe accuracy." },
];

const planned = [
  { t: "Folders, files, naming, organization (C1)", d: "First-class folders, drag/drop reorganization, multi-select moves." },
  { t: "Cloud sync (C2)", d: "Pull from Google Drive / iCloud / GitHub into your neuthek library." },
  { t: "Hybrid search (D3)", d: "CLIP semantic + Postgres full-text fused with rank reciprocal scoring." },
  { t: "Better summaries (D1, D2)", d: "Image and document summaries with user-tunable verbosity." },
  { t: "Multi-data-type platform (E1+)", d: "Contacts, password vault, game saves, IoT data — same privacy posture, same per-user isolation." },
  { t: "Comments + real-time editing (G2, G3)", d: "Layered on top of the sharing primitive once that surface stabilizes." },
  { t: "Model quantization (F2)", d: "Smaller-footprint vision models for low-resource self-hosters." },
  { t: "Repo &amp; docs hygiene (H1–H4)", d: "README rewrite, comment balance, GitHub-ready docs, CI / lint tightening." },
];

export default function Roadmap() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Roadmap</span>
          <h1>What's shipped, what's next, and what's on the horizon.</h1>
          <p className="lead">
            This page is generated from the same todo.md the team works
            against. If something has not landed yet, it sits in
            "building" or "planned" — never in "shipped."
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="roadmap">
            <div className="roadmap__col roadmap__col--shipped">
              <h3>Shipped</h3>
              <ul className="roadmap__list">
                {shipped.map((x) => (
                  <li key={x.t}><strong>{x.t}.</strong> {x.d}</li>
                ))}
              </ul>
            </div>
            <div className="roadmap__col roadmap__col--building">
              <h3>Building now</h3>
              <ul className="roadmap__list">
                {building.map((x) => (
                  <li key={x.t}><strong>{x.t}.</strong> {x.d}</li>
                ))}
              </ul>
            </div>
            <div className="roadmap__col roadmap__col--planned">
              <h3>Planned</h3>
              <ul className="roadmap__list">
                {planned.map((x) => (
                  <li key={x.t} dangerouslySetInnerHTML={{ __html: `<strong>${x.t}.</strong> ${x.d}` }} />
                ))}
              </ul>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>Pre-launch checklist for the hosted version.</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            We will not enable hosted accounts before all of this is in
            place. These are commitments, not aspirations.
          </p>
          <div className="cards">
            <div className="card"><h3>Encryption</h3><p>At rest and in transit, with managed keys and a documented rotation policy.</p></div>
            <div className="card"><h3>Account deletion</h3><p>Every byte: originals, served files, embeddings, tags, audit rows, derived metadata.</p></div>
            <div className="card"><h3>Export</h3><p>One-click portable archive of your library. No vendor lock-in.</p></div>
            <div className="card"><h3>Consent gates</h3><p>Explicit opt-in before any biometric or face-clustering surface is enabled.</p></div>
            <div className="card"><h3>Audit log</h3><p>Real, append-only audit trail for security-sensitive actions.</p></div>
            <div className="card"><h3>Backups</h3><p>Tested restore path; the database is not the only copy of your data.</p></div>
          </div>
        </div>
      </section>
    </>
  );
}
