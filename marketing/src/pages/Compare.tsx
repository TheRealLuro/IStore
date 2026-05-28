/* Compare — your experience first, never anywhere else.
 *
 * The page is ordered around the experience the user actually has
 * with the product. Speed, search quality, what they see when they
 * open the app — those rows go first. Ownership, openness, and
 * hardware come after, because they only matter if the day-to-day
 * experience is good.
 *
 * Third-party "Yes / No / Limited" calls reflect what each provider
 * publicly documents at time of writing. The neuthek column reflects
 * what is actually working in our development build today — items
 * are marked "Shipped" when they exist in the engine with tests,
 * "Planned" when they're committed pre-launch but not yet built. The
 * product itself is not publicly released.
 *
 * Brand names belong to their respective owners and appear here
 * nominatively to describe each product. */

import { Link } from "react-router-dom";
import { usePageSeo, webPage, breadcrumbs } from "../seo";

type Cell = { label: string; tone?: "good" | "bad" | "mid" };
type Row = { feature: string; cells: Cell[] };
type Group = { title: string; subtitle: string; rows: Row[] };

const HEADERS = [
  "neuthek",
  "Google Photos",
  "Apple iCloud Photos",
  "Microsoft OneDrive",
  "Dropbox",
  "Amazon Photos",
];

// ----- Group 1: Experience (the day-to-day) -----
// neuthek's column states what is actually in our engine (good tone). Other
// providers' columns use neutral, publicly-verifiable language only — no
// disparaging or unverifiable claims (legal: truthful comparative use).
const EXPERIENCE: Row[] = [
  {
    feature: "Search by what you remember (natural language)",
    cells: [
      { label: "Shipped — core feature", tone: "good" },
      { label: "Offered" },
      { label: "Offered" },
      { label: "Varies by plan" },
      { label: "Varies by plan" },
      { label: "Offered" },
    ],
  },
  {
    feature: "Search index kept in your own account / instance",
    cells: [
      { label: "Shipped — per-user isolation", tone: "good" },
      { label: "Provider-managed" },
      { label: "Provider-managed" },
      { label: "Provider-managed" },
      { label: "Provider-managed" },
      { label: "Provider-managed" },
    ],
  },
  {
    feature: "Browse without ads or upsell prompts",
    cells: [
      { label: "By design — no ads, ever", tone: "good" },
      { label: "Varies" },
      { label: "Varies" },
      { label: "Varies" },
      { label: "Varies" },
      { label: "Varies" },
    ],
  },
  {
    feature: "Face / people grouping",
    cells: [
      { label: "Shipped — opt-in, consent-first", tone: "good" },
      { label: "Offered" },
      { label: "Offered" },
      { label: "Offered" },
      { label: "Not a focus" },
      { label: "Offered" },
    ],
  },
  {
    feature: "Content-aware compression (lossless for documents)",
    cells: [
      { label: "Shipped — picks codec per image", tone: "good" },
      { label: "Provider default" },
      { label: "Provider default" },
      { label: "Keeps originals" },
      { label: "Keeps originals" },
      { label: "Provider default" },
    ],
  },
];

// ----- Group 2: Trust (what's behind the experience) -----
const TRUST: Row[] = [
  {
    feature: "Can run on hardware you control",
    cells: [
      { label: "Self-host planned", tone: "good" },
      { label: "Provider-hosted" },
      { label: "Provider-hosted" },
      { label: "Provider-hosted" },
      { label: "Provider-hosted" },
      { label: "Provider-hosted" },
    ],
  },
  {
    feature: "Vector embeddings kept in a database you can run",
    cells: [
      { label: "Self-host planned", tone: "good" },
      { label: "Provider-managed" },
      { label: "Provider-managed" },
      { label: "Provider-managed" },
      { label: "Provider-managed" },
      { label: "Provider-managed" },
    ],
  },
  {
    feature: "Source code open and auditable",
    cells: [
      { label: "Planned (no committed date)", tone: "good" },
      { label: "Proprietary" },
      { label: "Proprietary" },
      { label: "Proprietary" },
      { label: "Proprietary" },
      { label: "Proprietary" },
    ],
  },
  {
    feature: "Documented export of your full library",
    cells: [
      { label: "Shipped — one-click ZIP", tone: "good" },
      { label: "Offered" },
      { label: "Offered" },
      { label: "Offered" },
      { label: "Offered" },
      { label: "Offered" },
    ],
  },
  {
    feature: "Account delete removes files, embeddings, and face data",
    cells: [
      { label: "Shipped — covered by a test", tone: "good" },
      { label: "Per provider policy" },
      { label: "Per provider policy" },
      { label: "Per provider policy" },
      { label: "Per provider policy" },
      { label: "Per provider policy" },
    ],
  },
  {
    feature: "No AI training on your library",
    cells: [
      { label: "Hard guarantee", tone: "good" },
      { label: "See provider policy" },
      { label: "See provider policy" },
      { label: "See provider policy" },
      { label: "See provider policy" },
      { label: "See provider policy" },
    ],
  },
];

// ----- Group 3: Runtime (where it runs) -----
const RUNTIME: Row[] = [
  {
    feature: "Usable without a vendor account",
    cells: [
      { label: "Self-host planned", tone: "good" },
      { label: "Account required" },
      { label: "Account required" },
      { label: "Account required" },
      { label: "Account required" },
      { label: "Account required" },
    ],
  },
  {
    feature: "Runs inference on your own GPU / NPU",
    cells: [
      { label: "Shipped — CUDA / XPU / Apple Metal", tone: "good" },
      { label: "Provider-run" },
      { label: "On Apple silicon" },
      { label: "Provider-run" },
      { label: "Provider-run" },
      { label: "Provider-run" },
    ],
  },
  {
    feature: "Free tier (as publicly listed)",
    cells: [
      { label: "Self-host: free when released" },
      { label: "15 GB shared with Google account" },
      { label: "5 GB across iCloud" },
      { label: "5 GB free" },
      { label: "2 GB free" },
      { label: "5 GB (photos with Prime)" },
    ],
  },
  {
    feature: "Available today",
    cells: [
      { label: "Pre-release — join the waitlist", tone: "mid" },
      { label: "Available" },
      { label: "Available" },
      { label: "Available" },
      { label: "Available" },
      { label: "Available" },
    ],
  },
];

const GROUPS: Group[] = [
  { title: "Experience",
    subtitle: "How it feels to actually use it — search, browse, see your memories.",
    rows: EXPERIENCE },
  { title: "Trust",
    subtitle: "Who holds the bytes, the embeddings, and the keys behind the screen.",
    rows: TRUST },
  { title: "Runtime",
    subtitle: "Where it runs, what it costs, and when you can pick it up.",
    rows: RUNTIME },
];

export default function Compare() {
  usePageSeo({
    title: "Compare neuthek vs Google Photos, iCloud, OneDrive, Dropbox, Amazon Photos",
    description:
      "How neuthek compares to Google Photos, Apple iCloud Photos, Microsoft OneDrive, Dropbox, and Amazon Photos on search, ownership, privacy, hardware, and pricing. neuthek's column reflects our development build; other columns use neutral, publicly-documented descriptions.",
    path: "/compare",
    jsonLd: [
      webPage({
        name: "Compare — neuthek vs other cloud storage",
        description:
          "Side-by-side feature comparison of neuthek against the major personal-cloud incumbents. Honest scoping of what's shipped, planned, and not on the roadmap.",
        path: "/compare",
        about: "Comparison of personal cloud storage providers",
      }),
      breadcrumbs([
        { name: "Home", path: "/" },
        { name: "Compare", path: "/compare" },
      ]),
    ],
  });
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Compare</span>
          <h1>Your experience first, never anywhere else.</h1>
          <p className="lead">
            Storage is plumbing. What matters is how it feels when
            you open the app, type a half-remembered phrase, and
            the right photo comes back. The table below is ordered
            that way: experience first, trust next, then runtime.
            Nothing about who controls what would matter if the
            day-to-day weren't good.
          </p>
        </div>
      </section>

      {GROUPS.map((g) => (
        <section className="section" key={g.title}>
          <div className="container">
            <div className="compare-group__head">
              <h2 className="compare-group__title">{g.title}</h2>
              <p className="compare-group__sub">{g.subtitle}</p>
            </div>
            <div className="compare-wrap">
              <table className="compare">
                <colgroup>
                  <col className="compare__cap" />
                  {HEADERS.map((h) => (
                    <col className="compare__provider" key={h} />
                  ))}
                </colgroup>
                <thead>
                  <tr>
                    <th>Capability</th>
                    {HEADERS.map((h) => <th key={h}>{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {g.rows.map((r) => (
                    <tr key={r.feature}>
                      <td>{r.feature}</td>
                      {r.cells.map((c, i) => (
                        <td key={i} data-provider={HEADERS[i]}>
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
          </div>
        </section>
      ))}

      <section className="section">
        <div className="container">
          <p style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.6 }}>
            Brand names belong to their respective owners and are referenced
            here nominatively to identify each product. Descriptions of other
            providers are deliberately neutral and reflect publicly available
            documentation at the time of writing; they may change, and you
            should confirm current details with each provider directly. Only
            the neuthek column makes specific feature claims — items marked{" "}
            <strong>Shipped</strong> exist in our development build with tests;{" "}
            <strong>Planned</strong> means committed pre-launch but not yet
            built. neuthek is not publicly released — join the waitlist to be
            notified at launch.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <span className="eyebrow">Who it's for</span>
          <h2>Built for the people who make things run.</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            neuthek is being designed for working professionals,
            students, educators, and the developers, engineers, and
            researchers whose libraries are part of how they do
            their jobs. If you keep notes, screenshots, lecture
            slides, lab photos, syllabi, design refs, or research
            captures the way most people keep family photos — this
            is for you.
          </p>
          <div className="cards" style={{ marginTop: 40 }}>
            <div className="card">
              <div className="card__icon">DEV</div>
              <h3>For developers &amp; engineers</h3>
              <p>
                Screenshots, whiteboard captures, architecture
                diagrams, and lab photos searchable by intent
                instead of filename. Self-host on the same Linux
                box that runs your other services; auditable source,
                no opaque cloud in your stack.
              </p>
            </div>
            <div className="card">
              <div className="card__icon">EDU</div>
              <h3>For students &amp; researchers</h3>
              <p>
                Lecture slides, lab notebooks, field photos, paper
                captures — find them by what you remember, not by
                what you named the folder. Free self-host tier when
                it ships keeps your bibliography under your control,
                not behind a paywall.
              </p>
            </div>
            <div className="card">
              <div className="card__icon">PRO</div>
              <h3>For working professionals</h3>
              <p>
                Receipts, contracts, ID scans, work-in-progress
                screenshots. Smart compression keeps text-heavy
                images crisp; semantic search finds last spring's
                meeting whiteboard in seconds. Account portability
                planned so you're never trapped.
              </p>
            </div>
            <div className="card">
              <div className="card__icon">EDU+</div>
              <h3>For educators &amp; faculty</h3>
              <p>
                Course material archives, advisee portfolios,
                conference photos, syllabus drafts — searchable by
                topic, semester, or subject. Self-host fits
                department-policy IT setups; managed hosting fits
                personal use. Same engine, your choice of operator.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="section section--ink">
        <div className="container">
          <h2>Picking what's right for you.</h2>
          <div className="cards">
            <div className="card" style={{ background: "rgba(255,255,255,0.04)", borderColor: "rgba(255,255,255,0.1)" }}>
              <h3 style={{ color: "var(--surface)" }}>Stay with the big cloud if…</h3>
              <p>
                You don't want to operate any infrastructure, you
                trust the provider's privacy posture, and you're
                happy with what their search box returns today.
              </p>
            </div>
            <div className="card" style={{ background: "rgba(255,255,255,0.04)", borderColor: "rgba(255,255,255,0.1)" }}>
              <h3 style={{ color: "var(--surface)" }}>Plan for neuthek self-host if…</h3>
              <p>
                You want a server you own, embeddings that never
                leave your network, and a search experience you can
                extend or fork. You're comfortable running Docker.
              </p>
              <p style={{ marginTop: 12 }}>
                <Link to="/waitlist" className="btn btn--primary">Join waitlist</Link>
              </p>
            </div>
            <div className="card" style={{ background: "rgba(255,255,255,0.04)", borderColor: "rgba(255,255,255,0.1)" }}>
              <h3 style={{ color: "var(--surface)" }}>Plan for hosted neuthek if…</h3>
              <p>
                You want the same product as self-host, but operated
                by us — backups, GPU inference, HTTPS, account
                lifecycle handled. We'll email you when it goes live.
              </p>
              <p style={{ marginTop: 12 }}>
                <Link to="/waitlist" className="btn btn--ghost"
                      style={{ borderColor: "rgba(255,255,255,0.3)", color: "var(--surface)" }}>
                  Join waitlist
                </Link>
              </p>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
