/* Legal notice / Impressum. Required by:
   - EU eCommerce Directive 2000/31/EC Art. 5 (any commercial site
     targeting EU users must publish operator identity + contact).
   - German Telemediengesetz (TMG) §5 — the "Impressum" requirement.
   - Italy / Austria / many EU states have equivalent rules.
   Even though neuthek is a US operator, the moment we accept EU
   waitlist signups we're targeting EU users — so we publish this
   now, not at launch. */

import { usePageSeo, webPage, breadcrumbs } from "../seo";

const LAST_UPDATED = "May 27, 2026";

export default function LegalNotice() {
  usePageSeo({
    title: "Legal notice & Impressum — neuthek",
    description:
      "Operator identity, contact details, EU + UK GDPR Article 27 representatives, supervisory authority routing, and hosting provider for neuthek. Required by EU eCommerce Directive Art. 5 and German TMG §5.",
    path: "/legal-notice",
    jsonLd: [
      webPage({
        name: "Legal notice / Impressum",
        description:
          "Operator identity + contacts + EU/UK Art. 27 representatives + supervisory authority routing + hosting details, per the EU eCommerce Directive Art. 5 and German Telemediengesetz §5.",
        path: "/legal-notice",
        about: "Operator identification (EU eCommerce Directive)",
      }),
      breadcrumbs([
        { name: "Home", path: "/" },
        { name: "Legal notice", path: "/legal-notice" },
      ]),
    ],
  });
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Legal notice / Impressum</span>
          <h1>Who's behind this site.</h1>
          <p className="lead">
            Required under EU eCommerce Directive Art. 5 and German
            TMG §5. Operator identity + contact + regulatory
            references, in one discoverable place.
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
            active development. The team behind it is a solo
            developer in the United States, pre-incorporation. A
            legal entity (LLC or C-corp) will be formed before the
            hosted service opens for paid sign-ups, and this page
            will update with the entity name + registered agent at
            that time. EU and UK Article 27 representatives will be
            appointed at the same milestone. Until then, all
            data-subject and legal contact runs through the email
            addresses below — we respond within statutory windows.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>Site operator.</h2>
          <p style={{ marginTop: 12 }}>
            <strong>neuthek</strong> is operated by an individual
            developer based in the United States. The product is
            pre-release; a legal entity (LLC or C-corp) will be
            formed before the hosted service opens for sign-ups,
            and the operator identity below will be updated to the
            entity name + registered agent at that time.
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>Operator:</strong> Jason K. (individual operator, US, pre-incorporation)</li>
            <li><strong>Contact for legal + privacy:</strong> <code>legal@neuthek.com</code></li>
            <li><strong>Contact for security:</strong> published with hosted launch</li>
            <li><strong>Contact for press:</strong> <code>press@neuthek.com</code></li>
            <li><strong>Postal address:</strong> Will be published when the legal entity is formed and registers a business address.</li>
            <li><strong>VAT ID:</strong> Not yet assigned — service is pre-revenue.</li>
          </ul>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>EU & UK representatives (GDPR Art. 27).</h2>
          <p style={{ marginTop: 12 }}>
            Under Art. 27 of the EU GDPR and UK GDPR, an operator
            outside those jurisdictions that processes personal data
            of residents must appoint a written representative
            within each. The neuthek service does not yet collect
            personal data from EU or UK residents <em>at scale</em>{" "}
            — only a small waitlist of email addresses — but we
            commit to appointing both representatives before the
            hosted service opens for sign-ups in the EU and UK.
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>
              <strong>EU representative:</strong> To be appointed
              (likely Prighter, EDPO, or VeraSafe). Contact will be
              published here.
            </li>
            <li>
              <strong>UK representative:</strong> To be appointed
              separately (the EU rep does not cover the UK post-Brexit).
              Contact will be published here.
            </li>
          </ul>
          <p style={{ marginTop: 12 }}>
            If you are an EU or UK resident and you need to exercise
            a data-subject right before either representative is in
            place, email <code>privacy@neuthek.com</code> directly.
            We respond within the GDPR's one-month statutory window
            (Art. 12(3)).
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>Supervisory authority.</h2>
          <p style={{ marginTop: 12 }}>
            You have the right to lodge a complaint with a data
            protection supervisory authority. Useful contacts:
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>EU residents:</strong> the supervisory authority of your country of residence — full list at <a href="https://edpb.europa.eu/about-edpb/about-edpb/members_en" target="_blank" rel="noreferrer">edpb.europa.eu</a>.</li>
            <li><strong>UK residents:</strong> the Information Commissioner's Office — <a href="https://ico.org.uk" target="_blank" rel="noreferrer">ico.org.uk</a>.</li>
            <li><strong>California residents:</strong> the California Privacy Protection Agency — <a href="https://cppa.ca.gov" target="_blank" rel="noreferrer">cppa.ca.gov</a>.</li>
          </ul>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>Hosting + DNS.</h2>
          <p style={{ marginTop: 12 }}>
            The marketing site is hosted on Render (Delaware, US),
            with edge DNS and TLS through Cloudflare (California,
            US). See the <a href="/subprocessors">Subprocessors</a>{" "}
            page for the full list and transfer mechanisms.
          </p>
        </div>
      </section>
    </>
  );
}
