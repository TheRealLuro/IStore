/* This page describes the terms applicable to the marketing site
   itself. Hosted-service terms will be published as a separate
   document before the hosted version launches. */

export default function Terms() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Terms</span>
          <h1>Terms for this site.</h1>
          <p className="lead">
            These terms cover your use of this marketing site. The
            open-source build is governed by its own license; the
            hosted service will publish its own terms before launch.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h3 style={{ marginTop: 32 }}>1. This site</h3>
          <p style={{ marginTop: 8 }}>
            The neuthek marketing site is provided for informational
            purposes. Content describes the open-source project's
            current state and the team's plans. Plans may change.
          </p>

          <h3 style={{ marginTop: 32 }}>2. The open-source build</h3>
          <p style={{ marginTop: 8 }}>
            The neuthek source code is distributed under the license
            stated in the source repository. Your use of the
            self-hosted build is governed by that license, not by
            these site terms.
          </p>

          <h3 style={{ marginTop: 32 }}>3. The hosted service (when live)</h3>
          <p style={{ marginTop: 8 }}>
            The hosted version of neuthek is not currently available.
            When it launches, it will be governed by a separate
            hosted-service agreement and a separate privacy notice,
            both published before any real user data is collected.
          </p>

          <h3 style={{ marginTop: 32 }}>4. Trademarks and brand references</h3>
          <p style={{ marginTop: 8 }}>
            Third-party product names referenced on this site
            (Google Photos, Apple iCloud Photos, Microsoft OneDrive,
            Dropbox, Amazon Photos, and others) belong to their
            respective owners. They are referenced nominatively to
            describe and compare features. Reference does not imply
            endorsement, partnership, or affiliation.
          </p>

          <h3 style={{ marginTop: 32 }}>5. No warranty</h3>
          <p style={{ marginTop: 8 }}>
            This site and the open-source software are provided
            "as is" without warranty of any kind. Use them at your
            own risk.
          </p>

          <h3 style={{ marginTop: 32 }}>6. Contact</h3>
          <p style={{ marginTop: 8 }}>
            Questions about these terms can be sent to the contact
            address listed in the footer.
          </p>
        </div>
      </section>
    </>
  );
}
