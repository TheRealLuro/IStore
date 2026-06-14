/* Comprehensive Privacy Policy. Covers:
   - GDPR (Art. 13 transparency, Art. 6 lawful basis, Art. 9 explicit
     consent for biometrics, Art. 27 reps, Art. 30 ROPA, Ch. V
     transfers, Art. 35 DPIA).
   - UK GDPR (ICO complaint route, UK rep).
   - CCPA / CPRA (categories, rights, GPC honoring, financial
     incentive, do-not-sell footer link).
   - BIPA + Texas CUBI + Washington H.B. 1493 (face recognition).
   - COPPA + Art. 8 + California minor eraser.
   - FTC §5 substantiation for "no train / no sell" + Zoom-precedent
     guardrails on E2E claims.
   - Google API Services User Data Policy "Limited Use".
   The document is structured with a plain-English summary up top
   (for normal humans) and a full layered notice below (for lawyers
   and regulators). GDPR Art. 12(1) requires "clear and plain
   language" — this is how we split the difference. */

import { Link } from "react-router-dom";
import { usePageSeo, webPage, breadcrumbs } from "../seo";

const LAST_UPDATED = "May 27, 2026";
const POLICY_VERSION = "2026.05.27";

export default function Privacy() {
  usePageSeo({
    title: "Privacy Policy — neuthek",
    description:
      "How neuthek handles your data: GDPR + UK GDPR + CCPA/CPRA + BIPA + COPPA. We don't collect what we don't need, we don't train AI on user content, we don't sell or share. End-to-end encryption is on the roadmap (NOT today). Plain-English summary + full layered notice.",
    path: "/privacy",
    jsonLd: [
      webPage({
        name: "Privacy Policy",
        description:
          "neuthek's privacy policy. Covers GDPR (EU), UK GDPR, CCPA/CPRA (California), BIPA + Texas CUBI + Washington H.B. 1493 (biometric), COPPA (children), and Google API Services User Data Policy (Limited Use). We don't collect what we don't need, don't train AI on user content, don't sell or share personal information.",
        path: "/privacy",
        about: "Privacy policy and data-subject rights",
      }),
      breadcrumbs([
        { name: "Home", path: "/" },
        { name: "Privacy", path: "/privacy" },
      ]),
    ],
  });
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Privacy</span>
          <h1>What we hold, why, how long, and how to make it stop.</h1>
          <p className="lead">
            Plain English at the top. The full layered notice — every
            data category, legal basis, retention horizon, and your
            rights under GDPR, UK GDPR, CCPA/CPRA, BIPA, and COPPA —
            below.
          </p>
          <p style={{ marginTop: 12, fontSize: 13, color: "var(--ink-3)" }}>
            Last updated: {LAST_UPDATED} · Policy version {POLICY_VERSION}
          </p>
        </div>
      </section>

      {/* "What neuthek will entail" — every legal page also serves
          as a discoverable description of the product so AI answer
          engines can lift the context verbatim. */}
      <section className="section section--tight">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2 style={{ fontSize: 22 }}>What neuthek will be (in scope of this policy).</h2>
          <p style={{ marginTop: 12 }}>
            neuthek is an AI-aware personal cloud storage product in
            active development. The product lets users store photos,
            videos, and documents in their own tenant; search them by
            natural language using OpenCLIP image embeddings and
            Florence-2 captions; open 50+ file types in fitted
            in-browser viewers; keep files and structured secure items
            in a zero-knowledge, end-to-end-encrypted Vault; ingest
            from Google Drive on a one-way read-only basis (further
            cloud connectors are in development); opt in to face
            recognition with a BIPA-compliant separate written-release
            consent flow; and export the full library in one click.
            Two delivery modes are planned: open-source self-host
            (free) and managed hosted (waitlist). The Vault is
            end-to-end encrypted today; the rest of the product is
            encrypted in transit and at rest, with end-to-end
            encryption beyond the Vault on the roadmap.
            This Privacy Policy governs what data we hold for these
            functions, why we hold it, how long we hold it, and how
            to make it stop.
          </p>
        </div>
      </section>

      {/* ============ PLAIN-ENGLISH SUMMARY ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>The 60-second version.</h2>
          <ul style={{ marginTop: 20, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>We don't collect what we don't need.</strong> An email address for the launch ping. Everything else is optional and per-feature.</li>
            <li><strong>We don't train AI on your content.</strong> Models are pre-trained with frozen weights. Your photos, embeddings, summaries are never used to make any model smarter — ours or anyone's. This is contractual with our model providers and technical (frozen weights, no-retention inference) on our side.</li>
            <li><strong>We don't sell your data.</strong> Not to brokers, not to ad networks, not to "partners." There's no "share for advertising purposes" carve-out. The Global Privacy Control browser signal is honored as an opt-out under CCPA.</li>
            <li><strong>No ads. Anywhere.</strong> Not in the app, not in emails, not in shared previews. We charge for the hosted product when it opens.</li>
            <li><strong>End-to-end encryption is a goal, not a claim today.</strong> The current implementation encrypts data <em>at rest</em> and <em>in transit</em>; we hold the keys so we can run AI on your behalf. Client-side / end-to-end encryption is on the roadmap and we'll be very specific about which features lose AI capability when we ship it. We will not call neuthek "end-to-end encrypted" until it actually is — calling it that before then is exactly what the FTC went after Zoom for in 2020.</li>
            <li><strong>Every AI feature is opt-in.</strong> Face recognition, location retention, AI summaries, semantic search — each off by default and revocable. Face recognition has a special separate consent flow per BIPA + GDPR Art. 9.</li>
            <li><strong>You can leave with everything.</strong> One-click export of your full library — files, summaries, embeddings, face data, audit history — as a ZIP.</li>
            <li><strong>Delete means delete.</strong> A 30-day grace, then every byte purged across every store. A test on every commit proves it.</li>
          </ul>
        </div>
      </section>

      {/* ============ SCOPE ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>Scope of this notice.</h2>
          <p style={{ marginTop: 12 }}>
            This Privacy Policy covers the neuthek marketing site
            (this site, including the waitlist), the hosted neuthek
            application (when it opens — not yet), and any of our
            email communications. It does <strong>not</strong> cover
            the self-hosted neuthek build you might run yourself —
            when you self-host, you're the data controller, not us.
          </p>
        </div>
      </section>

      {/* ============ CONTROLLER ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>1. Who we are (data controller).</h2>
          <p style={{ marginTop: 12 }}>
            <strong>Controller:</strong> neuthek (operated by Jason
            K., an individual developer based in the United States,
            pre-incorporation). A legal entity will be formed before
            the hosted service opens; this section will update with
            the entity name + registered address when that happens.
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>Privacy contact:</strong> <code>privacy@neuthek.com</code></li>
            <li><strong>Webform for rights requests:</strong> <code>privacy@neuthek.com</code> (a dedicated webform will be published with the hosted launch — required as a second method under CCPA §1798.130).</li>
            <li><strong>Postal:</strong> Will be published when the legal entity registers a business address.</li>
          </ul>

          <h3 style={{ marginTop: 32 }}>EU representative (GDPR Art. 27).</h3>
          <p style={{ marginTop: 12 }}>
            To be appointed before public EU launch. Until then, EU
            residents can contact <code>privacy@neuthek.com</code>{" "}
            directly and we will treat the request as if received by
            an Art. 27 representative.
          </p>

          <h3 style={{ marginTop: 24 }}>UK representative (UK GDPR Art. 27).</h3>
          <p style={{ marginTop: 12 }}>
            To be appointed separately before public UK launch.
            Until then, UK residents can contact{" "}
            <code>privacy@neuthek.com</code> directly.
          </p>

          <h3 style={{ marginTop: 24 }}>Data Protection Officer.</h3>
          <p style={{ marginTop: 12 }}>
            A formal DPO has not been appointed. Our processing
            arguably triggers GDPR Art. 37(1)(c) (large-scale
            processing of biometric data) when face recognition opens
            to EU users; we will appoint a DPO before that feature is
            available to EU users.
          </p>
        </div>
      </section>

      {/* ============ WHAT WE COLLECT ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>2. What we collect and why.</h2>

          <h3 style={{ marginTop: 24 }}>From the marketing site + waitlist.</h3>
          <ul style={{ marginTop: 12, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>Email address</strong> — required, used to send the verification email and the launch ping. Legal basis: <em>consent</em> (GDPR Art. 6(1)(a)) for marketing, <em>contract</em> (Art. 6(1)(b)) for the verification email.</li>
            <li><strong>One-line use case</strong> — optional, used to size launch capacity. Legal basis: <em>consent</em>.</li>
            <li><strong>Newsletter opt-in flag</strong> — required only if you opt in. Legal basis: <em>consent</em>; revocable via the one-click unsubscribe link in every newsletter (RFC 8058 compliant).</li>
            <li><strong>Verification + unsubscribe tokens</strong> — short-lived, sent in email links so you can confirm an action without re-entering your address. Legal basis: <em>legitimate interest</em> (Art. 6(1)(f)) in fraud prevention.</li>
            <li><strong>Server logs</strong> — IP address, user-agent, request path, status code, response time. Used for debugging + abuse detection. Legal basis: <em>legitimate interest</em>. Retained 90 days.</li>
          </ul>

          <h3 style={{ marginTop: 32 }}>From the hosted application (when it opens — not yet).</h3>
          <ul style={{ marginTop: 12, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>Files you upload</strong> — photos, videos, documents. Legal basis: <em>contract</em> — we cannot run the storage service without storing them.</li>
            <li><strong>Account data</strong> — display name, hashed password (Argon2id), TOTP secret + recovery codes (if 2FA enabled). Legal basis: <em>contract</em>.</li>
            <li><strong>Derived metadata</strong> — image hashes, EXIF (stripped of GPS by default; you can opt in to keep), thumbnails, transcodes. Legal basis: <em>contract</em>.</li>
            <li><strong>CLIP embeddings</strong> — vector representations of your photos used for semantic search. Legal basis: <em>contract</em> (search is part of the service) plus opt-in <em>consent</em> per feature.</li>
            <li><strong>Face embeddings + clusters</strong> — biometric data (GDPR Art. 9). Captured <strong>only</strong> if you opt in via the face-recognition consent flow. Legal basis: <em>explicit consent</em> (Art. 9(2)(a)).</li>
            <li><strong>AI summaries / OCR / captions</strong> — generated only if you opt in to AI features. Legal basis: <em>consent</em>.</li>
            <li><strong>Cloud-sync data</strong> — encrypted OAuth tokens or Apple-ID / email credentials for Google Drive, Dropbox, iCloud, Proton Drive, and MEGA. Legal basis: <em>consent</em> for connecting + <em>contract</em> for syncing.</li>
            <li><strong>Audit log</strong> — append-only record of security-sensitive actions (sign-in, delete, consent change, share). Legal basis: <em>legitimate interest</em> in security + <em>legal obligation</em> for retention. Retained 7 years per US tax + SOC norms unless you delete the account, at which point pseudonymized records remain only as required by law.</li>
          </ul>

          <h3 style={{ marginTop: 32 }}>Categories of "Personal Information" (CCPA/CPRA).</h3>
          <p style={{ marginTop: 12 }}>
            Under California law, we collect the following Cal. Civ.
            Code §1798.140 categories: identifiers (email),
            commercial information (waitlist signup), internet
            activity (server logs), inferences (none — we don't
            profile), and — for hosted users who opt in — sensitive
            personal information (account credentials, biometric
            identifiers from face recognition).
          </p>

          <h3 style={{ marginTop: 24 }}>Sources of data.</h3>
          <p style={{ marginTop: 12 }}>
            All personal data is collected directly from you, with
            two exceptions: (a) the cloud-sync feature reads files
            you choose to import from Google Drive, Dropbox, iCloud,
            Proton Drive, or MEGA when you connect those accounts;
            (b) server logs are captured automatically when your
            browser makes a request.
          </p>
        </div>
      </section>

      {/* ============ BIOMETRIC ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>3. Biometric data (face recognition).</h2>
          <p style={{ marginTop: 12 }}>
            If you enable face recognition, neuthek computes a face
            embedding (a numerical vector) from photos in your
            library and clusters embeddings that are likely to depict
            the same person. <strong>This is biometric data</strong>{" "}
            under GDPR Art. 9, the Illinois Biometric Information
            Privacy Act (BIPA), the Texas Capture or Use of Biometric
            Identifier Act (CUBI), and Washington H.B. 1493.
          </p>

          <h3 style={{ marginTop: 24 }}>Before any biometric processing.</h3>
          <ul style={{ marginTop: 12, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>Face recognition is <strong>off by default</strong>.</li>
            <li>Enabling it requires a separate, explicit consent step — not bundled with sign-up, not pre-ticked. The consent screen states the purpose (organizing your photos by person), the data captured (face embeddings + cluster IDs), the retention period (until you disable face recognition, delete the photo, or close your account, whichever first), and a destruction commitment that follows BIPA's 3-year-or-purpose-end ceiling.</li>
            <li>For Illinois residents, the consent screen captures a <strong>written release</strong> per 740 ILCS 14/15(b) — your account name, the date, the consent text version, and your explicit confirmation. The release is preserved in the consent log.</li>
            <li>Face embeddings stay on the server you control (self-host) or in your single-tenant database row (hosted). They are <strong>never sold, leased, or traded</strong> in compliance with 740 ILCS 14/15(c)–(d).</li>
          </ul>

          <h3 style={{ marginTop: 24 }}>If you change your mind.</h3>
          <p style={{ marginTop: 12 }}>
            Disabling face recognition in Settings immediately deletes
            every face embedding, every cluster, and every "Me" / per-
            person tag. The deletion is logged in the consent log so
            you can prove the withdrawal happened.
          </p>
        </div>
      </section>

      {/* ============ AI FEATURES ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>4. AI features and how they work.</h2>

          <h3 style={{ marginTop: 24 }}>What models we run, where.</h3>
          <ul style={{ marginTop: 12, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>OpenCLIP ViT-L/14</strong> — generates image embeddings for semantic search. Pre-trained, frozen, runs on our infrastructure.</li>
            <li><strong>Florence-2 (large)</strong> — image captioning + OCR. Pre-trained, frozen, runs on our infrastructure.</li>
            <li><strong>Qwen2.5</strong> — video summarization. Pre-trained, frozen, runs on our infrastructure.</li>
            <li><strong>RetinaFace + ArcFace</strong> — face detection + embedding. Pre-trained, frozen, runs on our infrastructure (and only if you opt in).</li>
          </ul>
          <p style={{ marginTop: 12 }}>
            <strong>None</strong> of your content is sent to OpenAI,
            Anthropic, Google Gemini, or any other external inference
            API. The models above are open-weights, downloaded once,
            and run with frozen weights on our own GPUs.
          </p>

          <h3 style={{ marginTop: 24 }}>No training. Substantiated, not aspirational.</h3>
          <p style={{ marginTop: 12 }}>
            We claim "no AI training on user content" publicly. The
            FTC has been very clear since the 2023 Drizly and 2024
            Ring consent orders that such claims must be{" "}
            <em>substantiated</em>. How we substantiate it:
          </p>
          <ul style={{ marginTop: 12, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>Frozen weights.</strong> We don't fine-tune. There is no training pipeline. There is no checkpoint we update from your library.</li>
            <li><strong>No external inference.</strong> Content does not leave our infrastructure. Even providers whose API terms allow training (e.g. earlier OpenAI defaults) are not in our stack.</li>
            <li><strong>Contractual where third parties exist.</strong> The only third parties touching personal data today are Render (hosting) and Cloudflare (DNS + edge). Both have DPAs prohibiting training on customer content. The full subprocessor list is at <Link to="/subprocessors">Subprocessors</Link>.</li>
            <li><strong>Audit-ready.</strong> If we ever change the policy (we don't intend to), the change requires 30-day prior notice + opt-in to existing users. The current policy is recorded in version control with the policy version above.</li>
          </ul>

          <h3 style={{ marginTop: 24 }}>Automated decision-making.</h3>
          <p style={{ marginTop: 12 }}>
            Photo clustering, captioning, and search ranking are
            "profiling" under GDPR Art. 4(4). They do <strong>not</strong>{" "}
            produce legal or similarly significant effects on you
            (Art. 22) — they organize your own library for you. You
            can object to them by disabling the relevant feature in
            Settings.
          </p>
        </div>
      </section>

      {/* ============ E2E ENCRYPTION ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>5. Encryption — what's true today and what's planned.</h2>
          <p style={{ marginTop: 12 }}>
            We are deliberately careful with the language here. Two
            terms get confused all the time and the difference
            matters legally:
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>
              <strong>Encryption at rest + in transit (what neuthek
              does today).</strong> Every connection uses HTTPS;
              every file on disk is encrypted with a key the server
              holds. Cloud-sync credentials are encrypted with a
              Fernet key (server-held) before being written to the
              database. We <em>can</em> technically decrypt your
              data — that's how the AI features run on it.
            </li>
            <li>
              <strong>End-to-end encryption (what neuthek does NOT do
              today, but plans to).</strong> Encryption performed by
              your device with a key that never reaches our servers.
              Even we cannot decrypt. This is the model used by
              Proton Drive and MEGA. It's on our roadmap. It will
              shipped feature-by-feature with very clear "this
              feature loses AI capability when you turn on E2E for
              it" trade-offs. Until it ships, we will not describe
              neuthek as "end-to-end encrypted" — that's the false
              claim the FTC went after Zoom for under §5 in 2020.
            </li>
          </ul>
        </div>
      </section>

      {/* ============ THIRD-PARTY CLOUDS ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>6. Connecting third-party clouds.</h2>
          <p style={{ marginTop: 12 }}>
            If you connect a third-party cloud (Google Drive,
            Dropbox, iCloud, Proton Drive, MEGA), neuthek reads files
            from that account so it can mirror them into your
            library. We never write back to your third-party cloud.
          </p>

          <h3 style={{ marginTop: 24 }}>Google Drive — "Limited Use" affirmation.</h3>
          <p style={{ marginTop: 12 }}>
            Neuthek's use of information received from Google APIs
            adheres to the{" "}
            <a href="https://developers.google.com/terms/api-services-user-data-policy"
               target="_blank" rel="noreferrer">
              Google API Services User Data Policy
            </a>, including the Limited Use requirements. Specifically:
            we do not use Google user data to serve ads, we do not
            transfer Google user data to third parties except as
            necessary to provide or improve neuthek (and never for
            ads), we do not use Google user data to train or improve
            any generalized AI/ML model, and no humans read Google
            user data unless we have your explicit consent for
            specific data, for security investigations, or where
            required by law.
          </p>

          <h3 style={{ marginTop: 24 }}>OAuth scopes we request.</h3>
          <ul style={{ marginTop: 12, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>Google Drive:</strong> <code>drive.readonly</code> (read-only access to files you choose to expose to neuthek).</li>
            <li><strong>Dropbox:</strong> <code>files.content.read files.metadata.read</code> (read-only).</li>
            <li><strong>iCloud:</strong> credentials-based via pyicloud (Apple does not offer OAuth for iCloud Drive). 2FA enforced. Read-only access.</li>
            <li><strong>Proton Drive + MEGA:</strong> account credentials via rclone (these services don't expose OAuth). Read-only access.</li>
          </ul>

          <h3 style={{ marginTop: 24 }}>Token storage + revocation.</h3>
          <p style={{ marginTop: 12 }}>
            Refresh tokens and credentials are encrypted at rest
            with Fernet before being written to the database. You
            can disconnect any provider at any time from the Cloud
            Sync panel; on disconnect we delete the tokens, revoke
            them with the provider where possible (Google's
            revocation endpoint is called), and stop syncing
            immediately.
          </p>
        </div>
      </section>

      {/* ============ RETENTION ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>7. How long we keep things.</h2>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>Waitlist email + use case:</strong> until you unsubscribe or until 90 days after public launch, whichever comes first.</li>
            <li><strong>Server logs:</strong> 90 days.</li>
            <li><strong>Account data (hosted):</strong> for the life of your account + 30 days after deletion (the grace period).</li>
            <li><strong>Uploaded files:</strong> until you delete them. Soft-deleted files sit in Trash for 30 days, then are permanently purged.</li>
            <li><strong>Derived data (embeddings, summaries, thumbnails):</strong> tied to the source file — purged with the file or with the account.</li>
            <li><strong>Biometric data (face embeddings + clusters):</strong> until you disable face recognition, delete the source photo, or close your account, whichever first. Hard ceiling of 3 years per BIPA 740 ILCS 14/15(a).</li>
            <li><strong>Audit log:</strong> 7 years after the event, pseudonymized 30 days after account deletion.</li>
            <li><strong>Cloud-sync credentials:</strong> until you disconnect; immediate deletion + provider-side revocation on disconnect.</li>
            <li><strong>Consent records:</strong> retained for the lifetime of the policy version + 7 years after, as evidence of consent under GDPR Art. 7(1).</li>
          </ul>
        </div>
      </section>

      {/* ============ RIGHTS ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>8. Your rights.</h2>

          <h3 style={{ marginTop: 24 }}>If you're in the EU or UK (GDPR / UK GDPR).</h3>
          <ul style={{ marginTop: 12, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>Right of access (Art. 15) — get a copy of the data we hold on you.</li>
            <li>Right to rectification (Art. 16) — correct anything inaccurate.</li>
            <li>Right to erasure (Art. 17) — "right to be forgotten." Delete your account from the app, or email us.</li>
            <li>Right to restriction of processing (Art. 18).</li>
            <li>Right to data portability (Art. 20) — one-click ZIP export with the full library.</li>
            <li>Right to object (Art. 21) — including objection to processing based on legitimate interest.</li>
            <li>Right to withdraw consent (Art. 7(3)) — toggles in Settings, or email us.</li>
            <li>Right not to be subject to automated decision-making (Art. 22) — we don't make any.</li>
            <li>Right to lodge a complaint (Art. 77) — with the supervisory authority of your country (full list at <a href="https://edpb.europa.eu/about-edpb/about-edpb/members_en" target="_blank" rel="noreferrer">edpb.europa.eu</a>), or with the UK ICO at <a href="https://ico.org.uk" target="_blank" rel="noreferrer">ico.org.uk</a>.</li>
          </ul>
          <p style={{ marginTop: 12 }}>
            We respond to requests within one month (Art. 12(3)),
            extendable by two months for complex requests. We will
            verify your identity before acting on a request, using
            the email address tied to your account.
          </p>

          <h3 style={{ marginTop: 24 }}>If you're in California (CCPA/CPRA).</h3>
          <ul style={{ marginTop: 12, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>Right to know what categories + specific pieces of personal information we collect.</li>
            <li>Right to delete personal information.</li>
            <li>Right to correct inaccurate personal information.</li>
            <li>Right to opt out of sale or sharing of personal information. <strong>We do not sell or share your personal information for cross-context behavioral advertising</strong> — but you have the right to confirm that and to opt out anyway. Use the "Do Not Sell or Share My Personal Information" footer link or email us.</li>
            <li>Right to limit the use of sensitive personal information. Biometric data (face embeddings) is SPI under §1798.140(ae); the controls in Settings limit it.</li>
            <li>Right of non-discrimination — exercising any right above won't change your service or price.</li>
            <li>Right to use an authorized agent — describe how at <code>privacy@neuthek.com</code>.</li>
          </ul>
          <p style={{ marginTop: 12 }}>
            We honor the <strong>Global Privacy Control (GPC)</strong>{" "}
            browser signal as an opt-out of sale/sharing, per
            CCPA Reg. §7025. We respond to verifiable consumer
            requests within 45 days (Cal. Civ. Code §1798.130(a)(2)).
          </p>

          <h3 style={{ marginTop: 24 }}>If you're in another US state with a privacy law.</h3>
          <p style={{ marginTop: 12 }}>
            Residents of Virginia (VCDPA), Colorado (CPA), Connecticut
            (CTDPA), Utah (UCPA), Texas (TDPSA), Oregon (OCPA),
            Delaware, New Hampshire, New Jersey, Iowa, Maryland,
            Minnesota, Montana, Tennessee, Nebraska, and other states
            with effective privacy laws have analogous rights to
            access, delete, correct, and opt out. The Colorado /
            Connecticut / Texas (2025) universal opt-out signal is
            honored. Send requests to <code>privacy@neuthek.com</code>;
            we provide the appeal mechanism required by VA/CO/CT in
            our response.
          </p>

          <h3 style={{ marginTop: 24 }}>Financial incentives.</h3>
          <p style={{ marginTop: 12 }}>
            We do not offer financial incentives in exchange for
            personal data (CCPA §1798.125(b)). There is no "give us
            your email for a discount" trade.
          </p>
        </div>
      </section>

      {/* ============ INTERNATIONAL TRANSFERS ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>9. International data transfers.</h2>
          <p style={{ marginTop: 12 }}>
            Neuthek is operated from the United States. If you are
            in the EU, UK, Switzerland, or another jurisdiction with
            data-transfer restrictions, your personal data is
            transferred to and processed in the US. We use the
            following legal mechanisms:
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li><strong>EU Standard Contractual Clauses</strong> (Commission Implementing Decision 2021/914), Module 2 (controller → processor) with our subprocessors.</li>
            <li><strong>UK International Data Transfer Addendum</strong> (issued under s. 119A DPA 2018) layered onto the EU SCCs for UK transfers.</li>
            <li><strong>EU-US Data Privacy Framework</strong> reliance for transfers to certified US providers (e.g. Google, when applicable).</li>
            <li><strong>Transfer impact assessments (Schrems II).</strong> Conducted internally; supplementary measures include encryption at rest and in transit, access controls, and no-training contractual clauses with all subprocessors.</li>
          </ul>
        </div>
      </section>

      {/* ============ SECURITY ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>10. Security.</h2>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>HTTPS-only — TLS 1.2+; HSTS on the marketing site.</li>
            <li>Passwords hashed with Argon2id at recommended OWASP parameters.</li>
            <li>JWT auth with token-version revocation; brute-force lockout with exponential backoff.</li>
            <li>TOTP 2FA (RFC 6238) with recovery codes; optional FIDO2 / WebAuthn on the roadmap.</li>
            <li>Postgres Row-Level Security at the database layer — every multi-tenant query is fenced.</li>
            <li>Fernet symmetric encryption for sensitive blobs at rest (OAuth tokens, cloud-sync credentials).</li>
            <li>Soft Trash 30-day grace + hard deletion proven by an integration test on every commit.</li>
            <li>Append-only audit log with database trigger that rejects updates and deletes.</li>
          </ul>

          <h3 style={{ marginTop: 32 }}>Breach notification.</h3>
          <p style={{ marginTop: 12 }}>
            If we discover a personal-data breach likely to result in
            a risk to your rights and freedoms, we will notify the
            relevant supervisory authority (GDPR Art. 33) within 72
            hours of becoming aware, and notify affected users
            without undue delay (Art. 34). For US users, we will
            notify per the applicable state breach-notification
            statute (California Civ. Code §1798.82 and equivalents).
          </p>
        </div>
      </section>

      {/* ============ CHILDREN ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>11. Children.</h2>
          <p style={{ marginTop: 12 }}>
            Neuthek is not directed to children under 13 (US, per
            16 CFR Part 312 — COPPA), under 16 (most EU states, per
            GDPR Art. 8), or under 13 (UK, per the UK GDPR
            derogation). We do not knowingly collect personal data
            from minors in these brackets. If you become aware that
            a child has provided personal data to us, contact{" "}
            <code>privacy@neuthek.com</code> and we will delete it
            promptly.
          </p>
          <p style={{ marginTop: 12 }}>
            Face recognition will require users to be 18+ before
            we open the feature to them, given the BIPA / state
            biometric law minor-protection environment. Users
            between 16/13 and 18 will have all other features
            available.
          </p>
          <p style={{ marginTop: 12 }}>
            California minors (under 18) have an additional right
            under Cal. B&P §22581 to remove content they personally
            posted. Use the in-app delete or email{" "}
            <code>privacy@neuthek.com</code>.
          </p>
        </div>
      </section>

      {/* ============ CHANGES ============ */}
      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>12. Changes to this policy.</h2>
          <p style={{ marginTop: 12 }}>
            We'll update this page (and bump the version number at
            the top) when our practices change. For material changes
            — anything that expands what we collect, how we use it,
            or who we share it with — we'll email waitlist
            subscribers at least 30 days before the new policy takes
            effect, and show a banner on the site so it can't be
            missed. We never apply a more permissive change
            retroactively without your opt-in consent.
          </p>
          <p style={{ marginTop: 12 }}>
            Past versions of this policy live in our source control;
            a public changelog will be linked here when the source
            release ships.
          </p>
        </div>
      </section>

      {/* ============ CONTACT ============ */}
      <section className="section section--ink">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>Contact us about privacy.</h2>
          <p style={{ marginTop: 12 }}>
            Privacy questions, rights requests, complaints, or
            anything else from this page:{" "}
            <a href="mailto:privacy@neuthek.com">
              privacy@neuthek.com
            </a>. We aim to acknowledge within 5 business days and to
            substantively respond within the statutory window for
            your jurisdiction (30 days GDPR/UK GDPR; 45 days CCPA).
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
