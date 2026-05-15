import { useState } from "react";

/* The form below intentionally does not POST anywhere yet. The
   project is pre-launch; we don't want to collect emails into a
   mailing list before the privacy notice and storage policy are
   final. The submit handler stores the entry in localStorage so
   the user gets local confirmation; a real signup endpoint will
   replace this once the hosted backend is wired up. */

type Stored = { email: string; use: string; at: string };

function saveLocal(entry: Stored) {
  try {
    const raw = localStorage.getItem("neuthek.waitlist") || "[]";
    const list: Stored[] = JSON.parse(raw);
    list.push(entry);
    localStorage.setItem("neuthek.waitlist", JSON.stringify(list));
  } catch {
    /* ignore quota / private-mode failures */
  }
}

export default function Waitlist() {
  const [email, setEmail] = useState("");
  const [use, setUse] = useState("personal");
  const [done, setDone] = useState(false);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email) return;
    saveLocal({ email, use, at: new Date().toISOString() });
    setDone(true);
  }

  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Waitlist</span>
          <h1>Be there when hosted goes live.</h1>
          <p className="lead">
            We'll email you exactly twice: once when the hosted version
            opens for early users, and once at general availability.
            That's it. No drip campaigns, no newsletter, no resells.
          </p>

          <div className="callout">
            <strong>Pre-launch state:</strong> This form does not yet
            submit to a backend. We are not collecting email addresses
            into a hosted mailing list until the privacy notice is
            finalized. Your entry is held in your browser's local
            storage as a placeholder; we'll replace this with a real
            signup endpoint before launch.
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            {done ? (
              <div>
                <h2>You're on the local list.</h2>
                <p style={{ marginTop: 16 }}>
                  We saved <code>{email}</code> in your browser. When we
                  switch this form over to the real waitlist endpoint,
                  we'll honor entries that were here first.
                </p>
                <p style={{ marginTop: 12 }}>
                  In the meantime, the source code is yours to run today.
                </p>
              </div>
            ) : (
              <form className="form" onSubmit={onSubmit} noValidate>
                <h2 style={{ marginBottom: 8 }}>Save my spot.</h2>
                <p>We'll only email you about the hosted launch.</p>

                <label htmlFor="email" style={{ fontSize: 13, color: "var(--ink-2)" }}>Email</label>
                <input
                  id="email"
                  type="email"
                  required
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />

                <label htmlFor="use" style={{ fontSize: 13, color: "var(--ink-2)" }}>What will you use it for?</label>
                <select id="use" value={use} onChange={(e) => setUse(e.target.value)}>
                  <option value="personal">Personal photo library</option>
                  <option value="family">Shared family library</option>
                  <option value="creative">Creative work / portfolio</option>
                  <option value="research">Research / archive</option>
                  <option value="other">Something else</option>
                </select>

                <button type="submit" className="btn btn--primary btn--lg" style={{ marginTop: 8 }}>
                  Add me to the waitlist
                </button>
                <p style={{ fontSize: 12, color: "var(--ink-3)" }}>
                  By submitting you confirm you'd like a launch email.
                  No other use, no sharing, no resale.
                </p>
              </form>
            )}
          </div>

          <div>
            <h3>While you wait</h3>
            <ul style={{ paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
              <li>Spin up the open-source build on your own hardware</li>
              <li>Read the roadmap to see what we're building toward launch</li>
              <li>File issues or contribute on the public repo</li>
              <li>Check the compare table to decide if hosted is even the right fit for you</li>
            </ul>
          </div>
        </div>
      </section>
    </>
  );
}
