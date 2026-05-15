/* The lists below are pulled directly from the team's internal
   development tracker. Everything is pre-release: items marked
   "engine-complete" are working in the internal build but have
   not been published; items in "active work" are being built
   right now; items in "designed" are spec'd but not started. */

const engineComplete = [
  { t: "Sharing primitive", d: "Per-image grants with email pinning, hashed share tokens, server-enforced 1-day cap for new recipients, full audit trail." },
  { t: "Settings + 2FA", d: "TOTP-based two-factor authentication, in-app and email notification preferences, plan card, per-scope token expiry." },
  { t: "Admin overlay", d: "Live system, hardware, processes, models, tasks, and logs panels — no mock data." },
  { t: "Upload validation hardening", d: "Polyglot trailer stripping, dispatch-table validators, forensic quarantine, audit on rejection." },
  { t: "AI quality pass", d: "Image and search quality improvements landed across the vision pipeline." },
  { t: "Content-aware compression", d: "Photos at WebP q=82 (max 4096px); screenshots, documents, illustrations, icons fall to WebP lossless." },
  { t: "Semantic search", d: "768-dim OpenCLIP ViT-L-14 embeddings with pgvector cosine similarity, scoped per user." },
  { t: "Bulk-action toolbar", d: "Move to / new folder with selection / delete / pick-best-of in the gallery." },
];

const activeWork = [
  { t: "Compliance scaffolding", d: "Encryption at rest + in transit, secret management, real audit log, full deletion, pre-launch compliance, repo hygiene." },
  { t: "EXIF / GPS handling", d: "Strip or surface location/device metadata before public sharing or hosted launch." },
  { t: "Consent before signup", d: "Explicit consent gates ahead of account creation, ahead of biometric features." },
  { t: "Cross-vendor accelerator support", d: "CUDA / Intel XPU / Apple MPS dispatch is in place; widening hardware coverage and probe accuracy." },
];

const designed = [
  { t: "Folders, files, naming, organization", d: "First-class folders, drag/drop reorganization, multi-select moves." },
  { t: "Cloud sync", d: "Pull from Google Drive / iCloud / GitHub into your neuthek library." },
  { t: "Hybrid search", d: "CLIP semantic + Postgres full-text fused with reciprocal-rank scoring." },
  { t: "Better summaries", d: "Image and document summaries with user-tunable verbosity." },
  { t: "Multi-data-type platform", d: "Contacts, password vault, game saves, IoT data — same privacy posture, same per-user isolation." },
  { t: "Comments + real-time editing", d: "Layered on top of the sharing primitive once that surface stabilizes." },
  { t: "Model quantization", d: "Smaller-footprint vision models for low-resource self-hosters." },
  { t: "Docs hygiene", d: "README rewrite, comment balance, GitHub-ready docs, CI / lint tightening." },
];

export default function Roadmap() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Roadmap</span>
          <h1>What's working in dev, what's being built, what's on the horizon.</h1>
          <p className="lead">
            All items below describe the internal development build.
            Nothing on this page is publicly released yet — neither
            the open-source nor the hosted version. "Engine-complete"
            means it works in our development environment; "active
            work" means we're building it now; "designed" means it's
            spec'd but not started.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="roadmap">
            <div className="roadmap__col roadmap__col--shipped">
              <h3>Engine-complete</h3>
              <ul className="roadmap__list">
                {engineComplete.map((x) => (
                  <li key={x.t}><strong>{x.t}.</strong> {x.d}</li>
                ))}
              </ul>
            </div>
            <div className="roadmap__col roadmap__col--building">
              <h3>Active work</h3>
              <ul className="roadmap__list">
                {activeWork.map((x) => (
                  <li key={x.t}><strong>{x.t}.</strong> {x.d}</li>
                ))}
              </ul>
            </div>
            <div className="roadmap__col roadmap__col--planned">
              <h3>Designed</h3>
              <ul className="roadmap__list">
                {designed.map((x) => (
                  <li key={x.t}><strong>{x.t}.</strong> {x.d}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>Pre-launch checklist.</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            We won't release publicly — open source or hosted —
            before every item below is in place. These are commitments,
            not aspirations.
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
