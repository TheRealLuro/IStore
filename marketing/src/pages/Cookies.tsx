/* Cookies & similar technologies policy for the marketing site.
   Required by ePrivacy Directive Art. 5(3) + UK PECR Reg. 6 + GDPR
   Art. 13. The site does not currently set non-essential cookies,
   but we still need a discoverable policy that says so — and scaffold
   for the consent banner we'll add the moment we introduce analytics
   or any storage that isn't strictly necessary. */

import { Link } from "react-router-dom";

const LAST_UPDATED = "May 27, 2026";

export default function Cookies() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Cookies & storage</span>
          <h1>What we set in your browser. Spoiler: nothing yet.</h1>
          <p className="lead">
            Plain English summary of what cookies, localStorage, and
            similar browser storage neuthek uses. Required by the EU
            ePrivacy Directive and the UK PECR — and a fair thing for
            anyone visiting a site to be able to check.
          </p>
          <p style={{ marginTop: 12, fontSize: 13, color: "var(--ink-3)" }}>
            Last updated: {LAST_UPDATED}.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>This site doesn't set cookies.</h2>
          <p style={{ marginTop: 12 }}>
            The neuthek marketing site doesn't set any cookies. We
            don't run third-party analytics (no Google Analytics, no
            Plausible, no anything). We don't run advertising pixels.
            The only third-party request the site makes is to Google
            Fonts for the Geist typeface, and that request does not
            set cookies in your browser.
          </p>
          <p style={{ marginTop: 12 }}>
            A CI test fails the build if any backend route ever
            returns a <code>Set-Cookie</code> header. The marketing
            site is structurally cookie-free.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>Browser storage we do use.</h2>
          <p style={{ marginTop: 12 }}>
            A few small bits of <code>localStorage</code> /{" "}
            <code>sessionStorage</code> are used where they're strictly
            necessary for a feature to work. None of these are sent to
            our server, none are used for tracking, and you can clear
            them at any time from your browser's site-data settings.
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>
              <strong>Admin sign-in token</strong> (sessionStorage,
              admin route only) — kept while an operator is logged
              into the waitlist viewer. Cleared on tab close.
            </li>
            <li>
              <strong>Theme preference</strong> (localStorage, if you
              ever choose a light/dark override) — remembers your
              choice between visits. Not used today; reserved.
            </li>
          </ul>
          <p style={{ marginTop: 12 }}>
            We currently set <strong>no</strong> non-essential
            storage. If we ever add analytics or any storage that
            isn't strictly necessary, we'll surface a consent banner
            with an Accept / Reject choice of equal prominence and
            won't load anything until you've chosen. That's required
            by the ePrivacy Directive (Art. 5(3)) and the UK PECR
            (Reg. 6).
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>The hosted product, when it launches.</h2>
          <p style={{ marginTop: 12 }}>
            The hosted application (when it opens — not yet) uses{" "}
            <code>localStorage</code> to hold a short-lived JWT for
            sign-in. It is essential for the product to function and
            is exempt from consent under ePrivacy Art. 5(3). It is
            scoped to the application origin, not the marketing site,
            and never sent to a third party. The hosted-launch
            Privacy Notice will list every item of storage the
            application sets, by name and purpose.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>Updates to this policy.</h2>
          <p style={{ marginTop: 12 }}>
            We'll update this page (and bump the "last updated" date
            above) whenever the set of cookies / storage changes. For
            material changes — e.g. adding any non-essential storage
            — we'll show a banner on the site to make sure you see
            the change before continuing.
          </p>
          <p style={{ marginTop: 12 }}>
            For broader detail on what data we collect, why, and how
            long, see the <Link to="/privacy">Privacy</Link> page.
          </p>
        </div>
      </section>
    </>
  );
}
