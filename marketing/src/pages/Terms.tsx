/* Terms applicable to the marketing site itself. The self-host build
   and hosted service each have their own license / terms documents
   published with the source and at hosted launch respectively. The
   canonical source-available license while we're pre-release lives
   as TERMS.md in the repo. */

const LAST_UPDATED = "May 17, 2026";

export default function Terms() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Terms</span>
          <h1>Terms for this site.</h1>
          <p className="lead">
            These terms cover your use of this marketing site only.
            The self-host build will be governed by its own license
            when the source is published; the hosted service will
            publish its own terms before launch.
          </p>
          <p style={{ marginTop: 12, fontSize: 13, color: "var(--ink-3)" }}>
            Last updated: {LAST_UPDATED}.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h3 style={{ marginTop: 32 }}>1. This site</h3>
          <p style={{ marginTop: 8 }}>
            The neuthek marketing site is provided for informational
            purposes. Content describes what's currently in the
            development build and what's planned. Roadmap items may
            change. The "Working today" items on the{" "}
            <a href="/roadmap">roadmap</a> are present in the
            development build with tests, but the product itself is
            not yet publicly released — neither the self-host build
            nor the hosted service.
          </p>

          <h3 style={{ marginTop: 32 }}>2. The self-host build</h3>
          <p style={{ marginTop: 8 }}>
            The self-host distribution will be released under an
            open-source license. The codebase isn't fully cleaned up
            for public release yet, and we'd rather get that right
            than rush — there's no committed date for the public
            source drop. When the source is published, the license
            shipped with it (LICENSE / TERMS in the repository) is
            what governs your use of the self-hosted build, not
            these site terms.
          </p>

          <h3 style={{ marginTop: 32 }}>3. The hosted service</h3>
          <p style={{ marginTop: 8 }}>
            The hosted version of neuthek is not currently available.
            When it launches, it will be governed by a separate
            hosted-service agreement and a separate Privacy Notice,
            both published before any real user data is collected.
            Joining the <a href="/waitlist">waitlist</a> does not
            create an account or accept any hosted-service terms —
            you'll see those before sign-up at launch.
          </p>

          <h3 style={{ marginTop: 32 }}>4. Waitlist data</h3>
          <p style={{ marginTop: 8 }}>
            The waitlist form collects an email address (required)
            and an optional one-line use case. We send a one-click
            verification email so the launch ping only goes to people
            who actually meant to sign up. The email address is used
            for the launch ping and (if you opt in) a weekly
            newsletter. You can unsubscribe from any newsletter or
            request removal at any time. See the{" "}
            <a href="/privacy">Privacy</a> page for the full handling.
          </p>

          <h3 style={{ marginTop: 32 }}>5. Trademarks and brand references</h3>
          <p style={{ marginTop: 8 }}>
            Third-party product names referenced on this site
            (Google Photos, Apple iCloud Photos, Microsoft OneDrive,
            Dropbox, Amazon Photos, and others) belong to their
            respective owners. They're referenced nominatively to
            describe and compare features. Reference doesn't imply
            endorsement, partnership, or affiliation.
          </p>

          <h3 style={{ marginTop: 32 }}>6. No warranty</h3>
          <p style={{ marginTop: 8 }}>
            This site is provided "as is" without warranty of any
            kind. Use it at your own risk. We try to keep the
            roadmap and feature claims accurate, but the product is
            in active development and details may change between
            today and launch.
          </p>

          <h3 style={{ marginTop: 32 }}>7. Contact</h3>
          <p style={{ marginTop: 8 }}>
            Security vulnerabilities go to{" "}
            <a href="mailto:security@neuthek.app">security@neuthek.app</a>{" "}
            — see the <a href="/privacy">Privacy</a> page for the
            disclosure norms. Questions about these terms or the
            site itself can be sent to the address listed in the
            footer.
          </p>
        </div>
      </section>
    </>
  );
}
