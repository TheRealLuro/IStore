/* Acceptable Use Policy. Published before the service opens so that
   when sign-ups start, the AUP is already in effect. Violation gives
   us a documented basis for suspension/termination, and a clear
   public AUP is required to claim DSA Art. 14 safe harbor + various
   payment-processor and CSAM-reporting obligations. */

import { usePageSeo, webPage, breadcrumbs } from "../seo";

const LAST_UPDATED = "May 27, 2026";

export default function AcceptableUse() {
  usePageSeo({
    title: "Acceptable Use Policy — neuthek",
    description:
      "What you can and can't put on neuthek. Zero-tolerance CSAM with NCMEC reporting, no non-consensual face recognition, no copyright infringement, no malware, no scraping, no OFAC-sanctioned use. Enforcement + appeal procedure.",
    path: "/aup",
    jsonLd: [
      webPage({
        name: "Acceptable Use Policy",
        description:
          "Rules for using the neuthek hosted service and self-host instances when shared with others. Covers CSAM zero-tolerance + NCMEC reporting, face-recognition consent requirement, OFAC sanctions, copyright, abuse reporting, and enforcement.",
        path: "/aup",
        about: "Acceptable Use Policy for personal cloud storage",
      }),
      breadcrumbs([
        { name: "Home", path: "/" },
        { name: "Acceptable Use", path: "/aup" },
      ]),
    ],
  });
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Acceptable Use</span>
          <h1>What you can and can't put on neuthek.</h1>
          <p className="lead">
            The neuthek service is for storing and organizing your
            own files. The rules below apply to the marketing site,
            the hosted product when it opens, and any self-hosted
            instance you make available to other people.
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
            active development — photos, videos, and documents stored
            in your own tenant, searchable by natural language
            ("snowy roof at sunset"), with content-aware compression
            and opt-in face recognition. One-way ingest from Google
            Drive, Dropbox, iCloud, Proton Drive, and MEGA. Self-host
            (open-source, free) and managed hosted (waitlist) modes
            both planned. The acceptable-use rules below apply
            equally to whichever way you run neuthek and exist so
            that the product is safe for everyone who uses it.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>You may not use neuthek to:</h2>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>
              <strong>Store or distribute child sexual abuse
              material (CSAM)</strong>, or any image, video, or text
              that sexualizes a minor. We are required by 18 U.S.C.
              §2258A to report any CSAM we become aware of to NCMEC
              and will preserve evidence for law enforcement. There
              is no warning, no grace period, no appeal — accounts
              are terminated and reported on first discovery.
            </li>
            <li>
              <strong>Upload another person's intimate images
              without their consent</strong> (so-called "revenge
              porn"). Take a look at <a
                href="https://cybercivilrights.org/" target="_blank"
                rel="noreferrer">Cyber Civil Rights Initiative</a>{" "}
              for help and reporting if you're the victim.
            </li>
            <li>
              <strong>Run face recognition on people who haven't
              consented.</strong> Faces of people in your own photos
              are fine. Building a recognition index of strangers
              (e.g. processing CCTV feeds, scraped social photos)
              violates this policy and likely Illinois BIPA,
              California CCPA, GDPR Art. 9, and several state
              biometric statutes.
            </li>
            <li>
              <strong>Infringe copyright, trademarks, or other IP
              rights</strong> belonging to someone else. See the{" "}
              <a href="/dmca">DMCA</a> page for the takedown procedure.
            </li>
            <li>
              <strong>Store, sell, or distribute illegal content</strong>{" "}
              under your jurisdiction's law — weapons regulated by
              export law, controlled substances, malware, stolen
              credentials, doxxing material, content inciting
              violence or terrorism, or content sanctioned by OFAC.
            </li>
            <li>
              <strong>Use neuthek to send unsolicited bulk
              messages</strong>, run open relays, or facilitate spam.
              Sharing photos with people you know is the intended use;
              blasting newsletters to strangers is not.
            </li>
            <li>
              <strong>Probe, scan, or test our systems</strong>{" "}
              without prior written authorization. Coordinated
              vulnerability disclosure will get a published process
              before launch — until then, please don't pen-test the
              marketing site.
            </li>
            <li>
              <strong>Reverse-engineer, decompile, or attempt to
              extract the model weights</strong> we ship. We may use
              fine-tuned or proprietary models — extracting them
              violates trade-secret protection and the contractual
              terms of the model providers.
            </li>
            <li>
              <strong>Run automated scraping</strong> of any neuthek
              property without authorization. We don't have an API
              rate limit on the marketing site today — please don't
              make us add one.
            </li>
            <li>
              <strong>Use the service from a sanctioned
              jurisdiction</strong> (currently Cuba, Iran, North
              Korea, Syria, Crimea, Donetsk, Luhansk, and any
              jurisdiction added by OFAC). This isn't a moral judgment
              about the people who live there — it's a US sanctions
              compliance requirement.
            </li>
            <li>
              <strong>Impersonate someone else</strong>, claim
              affiliation we haven't given you, or use neuthek to
              defraud anyone.
            </li>
            <li>
              <strong>Resell the hosted service</strong> without a
              written reseller agreement. Self-host as much as you
              want — that's what the open-source release is for.
            </li>
          </ul>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>What happens when this policy is violated.</h2>
          <p style={{ marginTop: 12 }}>
            For most violations, we'll send you a notice and a
            window to fix it (typically 7 days) before suspending the
            account. For severe violations — CSAM, non-consensual
            intimate imagery, malware distribution, an active attack
            on our systems, or a credible imminent threat — we
            suspend immediately and may preserve evidence for law
            enforcement under 18 U.S.C. §2702(b)(7) or equivalent.
          </p>
          <p style={{ marginTop: 12 }}>
            You can appeal a suspension by emailing the contact
            published with the hosted launch. Under the EU Digital
            Services Act, EU users have the right to an internal
            complaint-handling system (Art. 20) and out-of-court
            dispute settlement (Art. 21) — both will be in place
            before the service opens to EU users.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>Reporting abuse.</h2>
          <p style={{ marginTop: 12 }}>
            Report violations to <code>abuse@neuthek.com</code> once
            the service opens. For CSAM, please also report to{" "}
            <a href="https://report.cybertip.org" target="_blank"
               rel="noreferrer">NCMEC's CyberTipline</a> — they have
            the legal authority and the global takedown network we
            don't.
          </p>
        </div>
      </section>
    </>
  );
}
