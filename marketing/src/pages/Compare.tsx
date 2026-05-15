/* The comparison below is intentionally factual and conservative.
   Every "Yes" / "No" / "Limited" reflects what each provider's own
   public documentation describes as of this writing. We avoid
   speculative claims about internal infrastructure or future plans.
   Brand names below are used nominatively to describe the products,
   not to imply endorsement or affiliation. */

import { Link } from "react-router-dom";

type Cell = { label: string; tone?: "good" | "bad" | "mid" };
type Row = { feature: string; cells: Cell[] };

const HEADERS = [
  "neuthek (self-host)",
  "Google Photos",
  "Apple iCloud Photos",
  "Microsoft OneDrive",
  "Dropbox",
  "Amazon Photos",
];

const ROWS: Row[] = [
  {
    feature: "You hold the data on your hardware",
    cells: [
      { label: "Yes", tone: "good" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
    ],
  },
  {
    feature: "Source code is open and auditable",
    cells: [
      { label: "Yes", tone: "good" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
    ],
  },
  {
    feature: "Semantic / natural-language image search",
    cells: [
      { label: "Yes", tone: "good" },
      { label: "Yes", tone: "good" },
      { label: "Yes", tone: "good" },
      { label: "Limited", tone: "mid" },
      { label: "Limited", tone: "mid" },
      { label: "Yes", tone: "good" },
    ],
  },
  {
    feature: "Vector embeddings stored in your own database",
    cells: [
      { label: "Yes", tone: "good" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
    ],
  },
  {
    feature: "No mandatory cloud account",
    cells: [
      { label: "Yes", tone: "good" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
    ],
  },
  {
    feature: "Free tier",
    cells: [
      { label: "Self-host: free" },
      { label: "15 GB shared with Google account" },
      { label: "5 GB across iCloud" },
      { label: "5 GB free" },
      { label: "2 GB free" },
      { label: "5 GB (unlimited photo storage with Prime)" },
    ],
  },
  {
    feature: "Face / people grouping",
    cells: [
      { label: "Planned, consent-first", tone: "mid" },
      { label: "Yes" },
      { label: "Yes" },
      { label: "Yes" },
      { label: "No", tone: "bad" },
      { label: "Yes" },
    ],
  },
  {
    feature: "Documented export of full library",
    cells: [
      { label: "Yes (raw filesystem + DB dump)", tone: "good" },
      { label: "Yes (Google Takeout)" },
      { label: "Yes" },
      { label: "Yes" },
      { label: "Yes" },
      { label: "Yes" },
    ],
  },
  {
    feature: "Runs on your own GPU / NPU",
    cells: [
      { label: "Yes (CUDA / XPU / MPS)", tone: "good" },
      { label: "No", tone: "bad" },
      { label: "Apple devices only", tone: "mid" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
      { label: "No", tone: "bad" },
    ],
  },
  {
    feature: "Hosted (managed) version available",
    cells: [
      { label: "Coming soon", tone: "mid" },
      { label: "Yes" },
      { label: "Yes" },
      { label: "Yes" },
      { label: "Yes" },
      { label: "Yes" },
    ],
  },
];

export default function Compare() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Compare</span>
          <h1>How neuthek differs from the big-cloud default.</h1>
          <p className="lead">
            We don't think the big providers are bad at storage — they're
            very good. The trade-off is who holds your data, your
            embeddings, and your search history. Below is the honest
            picture, based on what each provider documents publicly.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="compare-wrap">
            <table className="compare">
              <thead>
                <tr>
                  <th>Capability</th>
                  {HEADERS.map((h) => <th key={h}>{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {ROWS.map((r) => (
                  <tr key={r.feature}>
                    <td>{r.feature}</td>
                    {r.cells.map((c, i) => (
                      <td key={i}>
                        <span className={`pill${c.tone ? " pill--" + c.tone : ""}`}>
                          {c.label}
                        </span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p style={{ marginTop: 14, fontSize: 12, color: "var(--ink-3)" }}>
            Brand names belong to their respective owners and are referenced
            here to describe each product. Capabilities reflect public
            documentation at the time of writing and may change.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>Picking what's right for you.</h2>
          <div className="cards">
            <div className="card">
              <h3>Stay with the big cloud if…</h3>
              <p>
                You don't want to operate any infrastructure, you trust
                the provider's privacy posture, and you're happy with
                what their search box returns today.
              </p>
            </div>
            <div className="card">
              <h3>Self-host neuthek if…</h3>
              <p>
                You want a server you own, embeddings that never leave
                your network, and a search experience you can extend or
                fork. You're comfortable running Docker.
              </p>
              <p style={{ marginTop: 12 }}>
                <Link to="/developers" className="btn btn--primary">Get started</Link>
              </p>
            </div>
            <div className="card">
              <h3>Wait for managed neuthek if…</h3>
              <p>
                You want the same product as self-host, but operated
                by us — backups, GPU inference, HTTPS, account
                lifecycle handled. We'll email you when it goes live.
              </p>
              <p style={{ marginTop: 12 }}>
                <Link to="/waitlist" className="btn btn--ghost">Join waitlist</Link>
              </p>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
