/* DMCA / copyright takedown policy. The marketing site itself hosts
   no user-generated content, but we publish this page now so that
   when the hosted service opens, the takedown procedure is already
   public and we can plausibly claim §512(c) safe harbor from day
   one. The designated agent has not yet been registered with the US
   Copyright Office — that's a §512(c)(2) requirement we complete
   before public launch. */

const LAST_UPDATED = "May 27, 2026";

export default function Dmca() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Copyright</span>
          <h1>Copyright complaints and DMCA notices.</h1>
          <p className="lead">
            How to send a copyright takedown notice (US DMCA) or
            illegal-content notice (EU DSA) when the hosted service
            opens. The marketing site itself doesn't host
            user-uploaded content, but the procedure below is the one
            we'll follow the moment we do.
          </p>
          <p style={{ marginTop: 12, fontSize: 13, color: "var(--ink-3)" }}>
            Last updated: {LAST_UPDATED}.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>If you believe content infringes your copyright.</h2>
          <p style={{ marginTop: 12 }}>
            Neuthek complies with the U.S. Digital Millennium
            Copyright Act (17 U.S.C. §512) and with the EU Digital
            Services Act (Regulation 2022/2065) notice-and-action
            requirements. If you are the rights holder (or their
            authorized agent) and a user has uploaded your work
            without permission, you can ask us to remove it.
          </p>

          <h3 style={{ marginTop: 32 }}>What a takedown notice must include.</h3>
          <ul style={{ marginTop: 12, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>Your physical or electronic signature, name, and a contact email or postal address.</li>
            <li>A clear identification of the copyrighted work you claim has been infringed.</li>
            <li>The URL or other location on neuthek where the allegedly infringing copy lives, in enough detail for us to find it.</li>
            <li>A statement that you have a good-faith belief the use is not authorized by the copyright owner, its agent, or the law.</li>
            <li>A statement, under penalty of perjury, that the information in the notice is accurate and that you are authorized to act on behalf of the rights holder.</li>
          </ul>
          <p style={{ marginTop: 16, fontSize: 14, color: "var(--ink-3)" }}>
            Submitting a knowingly false claim is a federal crime
            under 17 U.S.C. §512(f) and creates liability for damages
            including attorneys' fees.
          </p>

          <h3 style={{ marginTop: 32 }}>Where to send it.</h3>
          <p style={{ marginTop: 12 }}>
            Send takedown notices to <code>dmca@neuthek.com</code>{" "}
            once the hosted service opens. We will publish the
            registered DMCA Designated Agent's name and address
            (registered with the U.S. Copyright Office under{" "}
            <a href="https://www.copyright.gov/dmca-directory/"
               target="_blank" rel="noreferrer">37 CFR §201.38</a>)
            on this page and at <code>dmca.copyright.gov</code> before
            the service opens for sign-ups.
          </p>
          <p style={{ marginTop: 12 }}>
            Until the service opens, no user content is hosted on
            neuthek and so no takedown is required. Please hold any
            pre-launch notices.
          </p>

          <h3 style={{ marginTop: 32 }}>Counter-notification (DMCA §512(g)).</h3>
          <p style={{ marginTop: 12 }}>
            If your content was removed and you believe it was
            removed in error or by mistaken identification, you can
            submit a counter-notification. It must include your name,
            address, phone number, a statement under penalty of
            perjury that you have a good-faith belief the material
            was removed in error, and your consent to the
            jurisdiction of the U.S. Federal District Court for the
            judicial district where you live (or the Northern District
            of California, if you live outside the U.S.). On receipt
            of a valid counter-notice we will forward it to the
            original complainant; the material may be reinstated 10
            to 14 business days later unless we receive notice of an
            action seeking a court order.
          </p>

          <h3 style={{ marginTop: 32 }}>Repeat infringers.</h3>
          <p style={{ marginTop: 12 }}>
            Per 17 U.S.C. §512(i), neuthek has a policy of
            terminating, in appropriate circumstances, the accounts
            of users who are repeat copyright infringers.
          </p>

          <h3 style={{ marginTop: 32 }}>EU Digital Services Act — illegal content notices.</h3>
          <p style={{ marginTop: 12 }}>
            For users in the EU, you can also use this channel under
            Articles 16–17 of the Digital Services Act to notify us
            of any content you believe is illegal (copyright,
            defamation, CSAM, terrorism content, hate speech, etc.).
            Provide: a sufficiently precise description of the
            content, its location, the legal grounds for considering
            it illegal, and a statement of your good faith. We will
            send a statement of reasons explaining any action taken,
            as required by Art. 17.
          </p>

          <h3 style={{ marginTop: 32 }}>Child sexual abuse material (CSAM).</h3>
          <p style={{ marginTop: 12 }}>
            Reports of CSAM are escalated immediately. Outside this
            takedown channel, please report directly to the National
            Center for Missing & Exploited Children's CyberTipline:{" "}
            <a href="https://report.cybertip.org" target="_blank"
               rel="noreferrer">report.cybertip.org</a>. We are
            required by 18 U.S.C. §2258A to report any CSAM we become
            aware of to NCMEC.
          </p>
        </div>
      </section>
    </>
  );
}
