/* Subprocessor list. Required by GDPR Art. 28 (every controller
   working with us needs to know who else touches the data) and a
   prerequisite for any B2B DPA we sign. The marketing site has very
   few subprocessors today; this page enumerates them so the list is
   public and updates trigger a 30-day notice as committed in the
   DPA template. */

import { usePageSeo, webPage, breadcrumbs } from "../seo";

const LAST_UPDATED = "May 27, 2026";

interface Sub {
  name: string;
  service: string;
  purpose: string;
  data_handled: string;
  hq: string;
  transfer: string;
  link?: string;
}

const SUBS: Sub[] = [
  {
    name: "Render",
    service: "Application + database hosting",
    purpose:
      "Runs the marketing site and the waitlist database. Provider for production hosting.",
    data_handled:
      "Waitlist email addresses, optional one-line use case, verification + unsubscribe tokens, server logs.",
    hq: "United States (Delaware)",
    transfer:
      "EU/UK → US: EU Standard Contractual Clauses (2021) + UK Addendum; data subject to Render's Data Processing Addendum.",
    link: "https://render.com/security",
  },
  {
    name: "Google LLC (Google Fonts)",
    service: "Web font delivery",
    purpose:
      "Serves the Geist typeface used across the marketing site. Single GET request per font weight on page load.",
    data_handled:
      "IP address (transient), user-agent, referrer URL — not retained for tracking per Google Fonts policy.",
    hq: "United States (California)",
    transfer:
      "EU/UK → US: Google is certified under the EU-US Data Privacy Framework.",
    link: "https://developers.google.com/fonts/faq/privacy",
  },
  {
    name: "Cloudflare, Inc.",
    service: "DNS, edge CDN, DDoS protection",
    purpose:
      "Resolves neuthek.com, terminates TLS at the edge, blocks abusive traffic.",
    data_handled:
      "IP address (transient), TLS handshake metadata, HTTP request headers. No content of waitlist signups touches Cloudflare; the form posts to Render via a non-Cloudflare path.",
    hq: "United States (California)",
    transfer:
      "EU/UK → US: EU SCCs (2021) + UK Addendum; Cloudflare publishes its DPA at the link.",
    link: "https://www.cloudflare.com/cloudflare-customer-dpa/",
  },
];

const PLANNED_SUBS: Sub[] = [
  {
    name: "Resend, Inc.",
    service: "Transactional + waitlist email delivery",
    purpose:
      "Sends the email-verification message, the launch ping, and the weekly newsletter. Not yet active — we send via direct SMTP today.",
    data_handled:
      "Email address, message content. Bounce + delivery metadata returned to neuthek.",
    hq: "United States (Delaware)",
    transfer:
      "EU/UK → US: EU SCCs + UK Addendum (Resend publishes DPA on signup).",
    link: "https://resend.com/legal/dpa",
  },
];

export default function Subprocessors() {
  usePageSeo({
    title: "Subprocessors — neuthek",
    description:
      "Every third party neuthek contracts with to process your data, the data they touch, where they're based, and the transfer mechanism (SCCs / UK Addendum / DPF). Updated when subprocessors change, with 30-day notice on additions.",
    path: "/subprocessors",
    jsonLd: [
      webPage({
        name: "Subprocessors",
        description:
          "GDPR Article 28 disclosure: every third party processing personal data on neuthek's behalf, what they handle, transfer mechanisms (EU SCCs + UK Addendum + EU-US Data Privacy Framework), and the 30-day notice commitment on changes.",
        path: "/subprocessors",
        about: "Subprocessor disclosure (GDPR Article 28)",
      }),
      breadcrumbs([
        { name: "Home", path: "/" },
        { name: "Subprocessors", path: "/subprocessors" },
      ]),
    ],
  });
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Subprocessors</span>
          <h1>Who else touches your data, and why.</h1>
          <p className="lead">
            A subprocessor is any third party we contract with to
            process data on our behalf. GDPR Art. 28 requires us to
            list them, document the data handled, and notify you 30
            days before adding or replacing any. This is that list.
          </p>
          <p style={{ marginTop: 12, fontSize: 13, color: "var(--ink-3)" }}>
            Last updated: {LAST_UPDATED}.
          </p>
        </div>
      </section>

      <section className="section section--tight">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2 style={{ fontSize: 22 }}>What neuthek will be.</h2>
          <p style={{ marginTop: 12 }}>
            neuthek is an AI-aware personal cloud storage product in
            active development. The vision pipeline (OpenCLIP for
            search, Florence-2 for captions, Qwen2.5 for video,
            RetinaFace + ArcFace for faces) runs entirely on
            infrastructure we operate — content does NOT transit
            OpenAI, Anthropic, Gemini, or any other third-party
            inference API. The subprocessors below handle hosting,
            DNS, transactional email, and web fonts. Everything else
            stays in our tenant.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 880 }}>
          <h2>Current subprocessors (marketing site).</h2>
          <p style={{ marginTop: 12 }}>
            These are the third parties processing data for the
            marketing site + waitlist today.
          </p>
          <SubTable rows={SUBS} />

          <h2 style={{ marginTop: 56 }}>Coming with hosted launch.</h2>
          <p style={{ marginTop: 12 }}>
            Additional subprocessors we expect to add before the
            hosted service opens for sign-ups. We'll move each one to
            the "Current" table when it goes live and notify waitlist
            subscribers 30 days in advance.
          </p>
          <SubTable rows={PLANNED_SUBS} />

          <h2 style={{ marginTop: 56 }}>What we deliberately do not use.</h2>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>No third-party analytics</strong> (no Google Analytics, no Plausible, no Mixpanel, no Amplitude).</li>
            <li><strong>No advertising networks</strong> (no Google Ads, no Meta Pixel, no LinkedIn Insight Tag).</li>
            <li><strong>No customer support widgets</strong> (no Intercom, no Drift, no Crisp).</li>
            <li><strong>No social-media tracking pixels</strong>.</li>
            <li><strong>No AI training APIs</strong>. The hosted vision pipeline runs the models on our own infrastructure with frozen weights — content does not transit OpenAI / Anthropic / any external inference provider.</li>
          </ul>

          <h2 style={{ marginTop: 56 }}>Notice of changes.</h2>
          <p style={{ marginTop: 12 }}>
            We commit to giving you 30 days' notice before adding or
            replacing any subprocessor that handles your data. Notice
            will go to waitlist subscribers by email and to hosted
            customers by email + an in-app banner. You can object to
            a change and, if we can't accommodate the objection,
            terminate your subscription as your right under GDPR Art.
            28.
          </p>
          <p style={{ marginTop: 12 }}>
            Past versions of this page are kept in version control;
            we'll publish a public changelog link once the source is
            released.
          </p>
        </div>
      </section>
    </>
  );
}

function SubTable({ rows }: { rows: Sub[] }) {
  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <table className="compare" style={{ minWidth: 720 }}>
        <thead>
          <tr>
            <th style={{ width: "20%" }}>Subprocessor</th>
            <th style={{ width: "28%" }}>Service & purpose</th>
            <th style={{ width: "28%" }}>Data handled</th>
            <th style={{ width: "24%" }}>Location & transfer mechanism</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <td>
                <strong>{r.name}</strong>
                {r.link && (
                  <>
                    <br />
                    <a href={r.link} target="_blank" rel="noreferrer"
                       style={{ fontSize: 12, color: "var(--ink-3)" }}>
                      DPA / policy ↗
                    </a>
                  </>
                )}
              </td>
              <td>
                <div style={{ fontWeight: 500 }}>{r.service}</div>
                <div style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 4 }}>
                  {r.purpose}
                </div>
              </td>
              <td>{r.data_handled}</td>
              <td>
                <div style={{ fontWeight: 500 }}>{r.hq}</div>
                <div style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 4 }}>
                  {r.transfer}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
