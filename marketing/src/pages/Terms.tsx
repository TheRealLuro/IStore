/* Terms of Service for the marketing site + waitlist + future hosted
   service. Covers items 43-63 of the legal compliance checklist:
   acceptance, account responsibilities, AUP reference, CSAM
   reporting, content license, ownership, DMCA reference, DSA notice
   reference, termination, warranty disclaimer, liability cap,
   EU/UK consumer carve-out, indemnification, governing law +
   consumer-jurisdiction carve-out, arbitration (US, EU/UK opted
   out), beta disclaimer, sanctions, force majeure, severability. */

import { Link } from "react-router-dom";
import { usePageSeo, webPage, breadcrumbs } from "../seo";

const LAST_UPDATED = "May 27, 2026";
const TERMS_VERSION = "2026.05.27";

export default function Terms() {
  usePageSeo({
    title: "Terms of Service — neuthek",
    description:
      "Terms of Service for the neuthek marketing site, waitlist, and (when it opens) hosted application. Narrow purpose-limited content license (NO training on your content), DMCA + DSA notice-and-action, EU/UK consumer carve-outs, OFAC sanctions, beta disclaimer.",
    path: "/terms",
    jsonLd: [
      webPage({
        name: "Terms of Service",
        description:
          "neuthek's Terms of Service. Account responsibilities, narrow purpose-limited content license (does NOT permit training), acceptable use, DMCA + DSA copyright procedures, disclaimer of warranties, limited liability with EU/UK consumer carve-outs, US arbitration with 30-day opt-out (EU/UK opted out), sanctions compliance.",
        path: "/terms",
        about: "Terms of Service",
      }),
      breadcrumbs([
        { name: "Home", path: "/" },
        { name: "Terms", path: "/terms" },
      ]),
    ],
  });
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Terms of Service</span>
          <h1>The rules that apply when you use neuthek.</h1>
          <p className="lead">
            These terms cover the marketing site, the waitlist, and
            the hosted neuthek application when it opens. The self-
            hosted build is governed by its own open-source license
            when published.
          </p>
          <p style={{ marginTop: 12, fontSize: 13, color: "var(--ink-3)" }}>
            Last updated: {LAST_UPDATED} · Terms version {TERMS_VERSION}
          </p>
        </div>
      </section>

      <section className="section section--tight">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2 style={{ fontSize: 22 }}>What neuthek will be.</h2>
          <p style={{ marginTop: 12 }}>
            neuthek is an AI-aware personal cloud storage product in
            active development. Users store photos, videos, and
            documents in their own tenant and search them by natural
            language; AI features (semantic search, captions, video
            summaries, opt-in face recognition) run on infrastructure
            we operate with frozen pre-trained model weights — your
            content never leaves to a third-party inference API and
            is never used to train any model, ours or anyone else's.
            Google Drive is wired for one-way read-only ingest today;
            connectors for Dropbox, iCloud, Proton Drive, and MEGA are
            in development. Over 50 file types open in fitted in-browser
            viewers, and a zero-knowledge, end-to-end-encrypted Vault
            holds files plus structured secure items. Two delivery
            modes planned: open-source self-host (free) and managed
            hosted (waitlist, pricing announced with launch). The Vault
            is end-to-end encrypted today; the rest is encrypted in
            transit + at rest, with end-to-end encryption beyond the
            Vault on the roadmap. These Terms set out the rules that
            apply when you use any of it.
          </p>
        </div>
      </section>

      {/* ============ ACCEPTANCE ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>1. Accepting these terms.</h2>
          <p style={{ marginTop: 12 }}>
            By using this site, joining the waitlist, or (when it
            opens) creating an account on the hosted service, you
            agree to these Terms and to our{" "}
            <Link to="/privacy">Privacy Policy</Link> and{" "}
            <Link to="/aup">Acceptable Use Policy</Link>. If you do
            not agree, do not use the service.
          </p>
          <p style={{ marginTop: 12 }}>
            You must be at least the age of digital consent in your
            jurisdiction to use neuthek: 13 in the UK and most US
            states, 16 in most EU member states (some allow lower,
            down to 13). The face-recognition feature, when enabled,
            requires you to be 18 or older.
          </p>
          <p style={{ marginTop: 12 }}>
            If you're using neuthek on behalf of an organization, you
            represent that you have authority to bind that
            organization to these Terms.
          </p>
        </div>
      </section>

      {/* ============ SERVICE DESCRIPTION ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>2. What's currently available.</h2>
          <p style={{ marginTop: 12 }}>
            Right now, neuthek is <strong>pre-release</strong>. The
            only thing available is this marketing site and the
            waitlist. No paid plans exist yet, no hosted application
            exists yet, and no public source release exists yet. We
            haven't committed dates because we'd rather get them
            right than be punctual.
          </p>
          <p style={{ marginTop: 12 }}>
            When the hosted service opens, it will be governed by
            these Terms (updated to reference the live service) plus
            any service-specific addenda we publish at the time.
            When the open-source self-host build is published, it
            will be governed by the license shipped with the
            repository, <em>not</em> by these Terms.
          </p>
        </div>
      </section>

      {/* ============ WAITLIST ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>3. The waitlist.</h2>
          <p style={{ marginTop: 12 }}>
            Joining the waitlist gives us permission to email you a
            verification message, the launch ping when the service
            opens, and (if you opt in) a weekly newsletter. It does{" "}
            <strong>not</strong> create an account, does not entitle
            you to any specific feature or launch date, and is not
            an agreement to any future hosted-service terms — those
            will be presented separately before you sign up.
          </p>
          <p style={{ marginTop: 12 }}>
            You can leave the waitlist at any time by clicking the
            unsubscribe link in any email from us, or by emailing{" "}
            <code>privacy@neuthek.com</code>.
          </p>
        </div>
      </section>

      {/* ============ ACCOUNT ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>4. Your account (when the service opens).</h2>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>Keep your account credentials confidential — you're responsible for what happens under your account.</li>
            <li>One account per person; do not share credentials. Multi-user functionality will exist in the form of explicit shares, not credential sharing.</li>
            <li>Provide accurate information when you sign up; keep it accurate when it changes.</li>
            <li>Tell us at <code>security@neuthek.com</code> (channel published with launch) if you believe your account has been compromised.</li>
          </ul>
        </div>
      </section>

      {/* ============ AUP ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>5. Acceptable Use.</h2>
          <p style={{ marginTop: 12 }}>
            Your use of neuthek is governed by our{" "}
            <Link to="/aup">Acceptable Use Policy</Link>, which is
            part of these Terms. Key items:
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>No CSAM, no non-consensual intimate imagery, no illegal content.</strong> CSAM is reported to NCMEC's CyberTipline per 18 U.S.C. §2258A.</li>
            <li>No face recognition on people who haven't consented.</li>
            <li>No copyright infringement; see <Link to="/dmca">DMCA</Link> for the takedown procedure.</li>
            <li>No automated scraping, probing, malware, or attack tooling.</li>
            <li>Sanctions: no use from Cuba, Iran, North Korea, Syria, Crimea, Donetsk, Luhansk, or other OFAC-designated jurisdictions.</li>
          </ul>
        </div>
      </section>

      {/* ============ CONTENT LICENSE ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>6. Your content.</h2>
          <h3 style={{ marginTop: 16 }}>You own your stuff.</h3>
          <p style={{ marginTop: 12 }}>
            Files you upload to neuthek are yours. We claim no
            ownership of them. That's not a concession — it's the
            point of the product.
          </p>

          <h3 style={{ marginTop: 24 }}>What you let us do with them.</h3>
          <p style={{ marginTop: 12 }}>
            To run the service for you, you grant us a narrow,
            non-exclusive, royalty-free, worldwide license to store,
            process, back up, transcode, generate derived data
            (thumbnails, embeddings, captions, summaries — only the
            features you opted into) from, and display back to you
            the files you upload.
          </p>
          <p style={{ marginTop: 12 }}>
            This license is <strong>purpose-limited</strong>: it
            exists solely so we can provide the service to you. It
            does <strong>not</strong> let us:
          </p>
          <ul style={{ marginTop: 12, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>Train any AI model on your content. We don't, and the license doesn't allow us to.</li>
            <li>Use your content in marketing, demos, or showcases.</li>
            <li>Sell, sublicense, or transfer your content to anyone (except subprocessors strictly providing hosting; see <Link to="/subprocessors">Subprocessors</Link>).</li>
            <li>Continue using your content after you delete it or your account.</li>
          </ul>
          <p style={{ marginTop: 12 }}>
            The license terminates immediately when you delete the
            file or the account.
          </p>

          <h3 style={{ marginTop: 24 }}>Your warranties about your content.</h3>
          <p style={{ marginTop: 12 }}>
            You represent that you have the right to upload the
            content you upload — you own it, you have permission, or
            it's in the public domain. You're responsible for the
            content; we're not.
          </p>
        </div>
      </section>

      {/* ============ DMCA ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>7. Copyright + illegal-content notices.</h2>
          <p style={{ marginTop: 12 }}>
            We comply with the DMCA (17 U.S.C. §512) and with EU
            Digital Services Act (Regulation 2022/2065) Art. 16
            notice-and-action obligations. See the{" "}
            <Link to="/dmca">DMCA / illegal-content notices</Link>{" "}
            page for the procedure, what to include in a notice,
            and the counter-notification process. Repeat infringers
            have their accounts terminated.
          </p>
        </div>
      </section>

      {/* ============ TERMINATION ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>8. Suspension and termination.</h2>
          <p style={{ marginTop: 12 }}>
            <strong>By you.</strong> You can terminate your account
            at any time from the in-app Settings. Termination starts
            a 30-day grace period during which your account is
            disabled but recoverable; after that, every file,
            embedding, summary, face template, and audit row is
            purged in one transaction (the integration test proves
            it).
          </p>
          <p style={{ marginTop: 12 }}>
            <strong>By us.</strong> We may suspend or terminate your
            account if you materially violate these Terms or the
            AUP. For most violations, we'll send you notice and a
            reasonable window to fix it (typically 7 days). For
            severe violations — CSAM, an active attack on our
            systems, sanctions violations — we suspend immediately.
            EU users have the right to an internal complaint-
            handling system (DSA Art. 20) and out-of-court dispute
            settlement (Art. 21).
          </p>
        </div>
      </section>

      {/* ============ NO WARRANTY ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>9. Disclaimer of warranties.</h2>
          <p style={{ marginTop: 12, textTransform: "none" }}>
            The service is provided "AS IS" and "AS AVAILABLE"
            without warranties of any kind, whether express or
            implied, including but not limited to merchantability,
            fitness for a particular purpose, non-infringement, or
            any warranty arising from course of dealing. We do not
            warrant that the service will be uninterrupted, error-
            free, or that data will not be lost — we run an
            integration test on every commit that proves account
            deletion purges every byte, but no system is infallible.
            Keep your own backups, especially during pre-release.
          </p>
          <p style={{ marginTop: 12 }}>
            Some jurisdictions don't allow the exclusion of certain
            warranties, so the exclusions above may not fully apply
            to you. Nothing in these Terms limits any statutory
            warranty you have under the UK Consumer Rights Act 2015
            or the EU Unfair Contract Terms Directive (93/13/EEC).
          </p>
        </div>
      </section>

      {/* ============ LIABILITY ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>10. Limitation of liability.</h2>
          <p style={{ marginTop: 12 }}>
            To the maximum extent permitted by law:
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>We are not liable for indirect, incidental, special, consequential, or punitive damages, or for lost profits, lost revenue, or lost data — even if we've been advised of the possibility.</li>
            <li>Our total aggregate liability arising out of or related to these Terms or the service is capped at the greater of (i) the fees you paid us in the 12 months before the event giving rise to the claim, or (ii) USD 100.</li>
          </ul>
          <p style={{ marginTop: 12 }}>
            <strong>Carve-outs.</strong> Nothing in these Terms
            limits or excludes liability for (a) gross negligence,
            (b) willful misconduct, (c) fraud, (d) death or personal
            injury caused by negligence, or (e) anything else that
            cannot be excluded under applicable law. UK and EU
            consumers retain all statutory rights regardless of
            anything in this section.
          </p>
        </div>
      </section>

      {/* ============ INDEMNIFICATION ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>11. Indemnification.</h2>
          <p style={{ marginTop: 12 }}>
            You agree to indemnify and hold harmless neuthek (and its
            operators, contractors, and successors) from any third-
            party claim, demand, or liability — including reasonable
            attorneys' fees — arising out of (a) content you upload
            in violation of these Terms or the AUP, (b) your
            violation of any law or third-party right, or (c) your
            misuse of the service. This section does not apply to
            consumers in the EU/UK to the extent prohibited by
            applicable consumer-protection law.
          </p>
        </div>
      </section>

      {/* ============ GOVERNING LAW ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>12. Governing law and venue.</h2>
          <p style={{ marginTop: 12 }}>
            These Terms are governed by the laws of the State of
            Delaware (USA), without regard to its conflict-of-laws
            rules. Any dispute arising under these Terms will be
            brought in the state or federal courts located in
            Delaware, and you consent to the exclusive jurisdiction
            and venue of those courts.
          </p>
          <p style={{ marginTop: 12 }}>
            <strong>EU consumers</strong> retain the right to bring
            proceedings in the courts of their country of residence
            under the Brussels I bis Regulation (Reg. 1215/2012). Nothing
            here deprives EU consumers of mandatory consumer-
            protection rights under their local law.{" "}
            <strong>UK consumers</strong> have an equivalent right
            under the UK Civil Jurisdiction and Judgments Act.
          </p>
        </div>
      </section>

      {/* ============ ARBITRATION ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>13. Arbitration and class waiver (US residents only).</h2>
          <p style={{ marginTop: 12 }}>
            <strong>This section does not apply to EU or UK
            residents.</strong> US residents agree that any dispute
            arising under these Terms will be resolved by binding
            arbitration administered by the American Arbitration
            Association (AAA) under its Consumer Arbitration Rules,
            on an individual basis, not as a class action or
            collective proceeding.
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>You may opt out of arbitration by emailing <code>legal@neuthek.com</code> within 30 days of first accepting these Terms.</li>
            <li>Small-claims court remains available for disputes within that court's jurisdictional limit.</li>
            <li>Either party may seek injunctive relief in court for IP infringement, unauthorized access, or breach of confidentiality.</li>
          </ul>
        </div>
      </section>

      {/* ============ CHANGES ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>14. Changes to these Terms.</h2>
          <p style={{ marginTop: 12 }}>
            We will give you at least 30 days' notice of any
            material change — by email if you have an account or
            are on the waitlist, and by an on-site banner. Continued
            use of the service after the change takes effect
            constitutes acceptance. If you don't agree with the
            change, you can close your account or leave the waitlist
            before it takes effect.
          </p>
        </div>
      </section>

      {/* ============ BOILERPLATE ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>15. The boilerplate.</h2>

          <h3 style={{ marginTop: 16 }}>Entire agreement.</h3>
          <p style={{ marginTop: 8 }}>
            These Terms, the Privacy Policy, the AUP, and any other
            policies linked from the site or the hosted application
            constitute the entire agreement between you and neuthek
            and supersede any prior agreement.
          </p>

          <h3 style={{ marginTop: 24 }}>Severability.</h3>
          <p style={{ marginTop: 8 }}>
            If any provision of these Terms is held unenforceable,
            the rest stays in effect.
          </p>

          <h3 style={{ marginTop: 24 }}>Assignment.</h3>
          <p style={{ marginTop: 8 }}>
            You may not assign these Terms without our written
            consent. We may assign these Terms to a successor in a
            merger, acquisition, or sale of substantially all assets,
            with notice.
          </p>

          <h3 style={{ marginTop: 24 }}>Force majeure.</h3>
          <p style={{ marginTop: 8 }}>
            Neither party is liable for failure or delay caused by
            events beyond reasonable control — war, terrorism,
            pandemic, natural disaster, governmental action, internet
            backbone outage, third-party infrastructure failure.
          </p>

          <h3 style={{ marginTop: 24 }}>Third-party trademarks.</h3>
          <p style={{ marginTop: 8 }}>
            Third-party product names referenced on this site
            (Google Drive, Dropbox, iCloud, Proton Drive, MEGA,
            Google Photos, Apple iCloud Photos, Microsoft OneDrive,
            Amazon Photos, and others) belong to their respective
            owners. They're referenced nominatively to describe and
            compare features. Reference doesn't imply endorsement,
            partnership, or affiliation.
          </p>

          <h3 style={{ marginTop: 24 }}>Beta / pre-release disclaimer.</h3>
          <p style={{ marginTop: 8 }}>
            While neuthek is pre-release, the service is provided at
            no charge, without any service-level commitment, and may
            change or be withdrawn at any time. Keep your own
            backups of anything important.
          </p>

          <h3 style={{ marginTop: 24 }}>How to contact us about these Terms.</h3>
          <p style={{ marginTop: 8 }}>
            Email <code>legal@neuthek.com</code>. Past versions of
            these Terms are kept in version control; a public
            changelog will be published when the source release ships.
          </p>
        </div>
      </section>
    </>
  );
}
