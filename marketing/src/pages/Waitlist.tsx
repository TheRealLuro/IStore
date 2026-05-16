import { useState } from "react";
import { postWaitlistSignup, type WaitlistUseCase } from "../api";

/* The form below POSTs to the marketing-site's own /api/waitlist/signup
   endpoint (served by ../server.mjs in the same Render Web Service as
   this SPA). No dependency on the main neuthek backend — the marketing
   surface is fully self-contained so it can run on Render while the
   rest of the product is still in development. */

export default function Waitlist() {
  const [email, setEmail] = useState("");
  const [use, setUse] = useState<WaitlistUseCase>("personal");
  // Newsletter consent — defaults to UNCHECKED. The form remains a
  // launch-pings-only signup unless the user explicitly opts in.
  const [newsletter, setNewsletter] = useState(false);
  const [status, setStatus] = useState<
    "idle" | "submitting" | "done" | "done-offline" | "error"
  >("idle");
  const [error, setError] = useState<string>("");

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email) return;
    setStatus("submitting");
    setError("");

    try {
      await postWaitlistSignup({
        email: email.trim().toLowerCase(),
        use_case: use,
        newsletter_opt_in: newsletter,
      });
      setStatus("done");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      const networkFailure =
        msg.startsWith("server-error") ||
        msg === "Failed to fetch" ||
        (err as Error)?.name === "TypeError";

      if (networkFailure) {
        // Marketing site is up but the API isn't reachable — e.g. the
        // Express server hasn't been deployed yet. Tell the visitor
        // honestly that the signup didn't go through.
        setStatus("done-offline");
        return;
      }
      if (msg === "rate-limited") {
        setError("Too many signups from this network. Try again in a minute.");
      } else if (msg === "invalid-email") {
        setError("That doesn't look like a valid email address.");
      } else {
        setError("Something went wrong. Try again or email us directly.");
      }
      setStatus("error");
    }
  }

  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Waitlist</span>
          <h1>Be there when the next best cloud storage launches.</h1>
          <p className="lead">
            neuthek is shaping up to be the next best cloud storage
            solution — open-source today, hosted soon. Drop your email
            and we'll ping you exactly twice: once when the hosted
            version opens for early users, and once at general
            availability. That's it. No drip campaigns, no newsletter,
            no resale.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            {status === "done" && (
              <div>
                <h2>You're on the list.</h2>
                <p style={{ marginTop: 16 }}>
                  We saved <code>{email}</code>. We'll email you when
                  the hosted version opens for early users and again
                  at general availability.
                </p>
                <p style={{ marginTop: 12 }}>
                  In the meantime, watch the roadmap for what's
                  landing next.
                </p>
              </div>
            )}

            {status === "done-offline" && (
              <div>
                <h2>Couldn't reach the server.</h2>
                <p style={{ marginTop: 16 }}>
                  The signup endpoint isn't responding right now, so
                  <code> {email}</code> was not saved. Please try
                  again in a moment, or reach out at the email in the
                  footer.
                </p>
              </div>
            )}

            {(status === "idle" || status === "submitting" || status === "error") && (
              <form className="form" onSubmit={onSubmit} noValidate>
                <h2 style={{ marginBottom: 8 }}>Save my spot.</h2>
                <p>We only email you about the launch.</p>

                <label htmlFor="email" style={{ fontSize: 13, color: "var(--ink-2)" }}>
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  required
                  autoComplete="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />

                <label htmlFor="use" style={{ fontSize: 13, color: "var(--ink-2)" }}>
                  What will you use it for?
                </label>
                <select
                  id="use"
                  value={use}
                  onChange={(e) => setUse(e.target.value as WaitlistUseCase)}
                >
                  <option value="personal">     Personal photos &amp; memories</option>
                  <option value="family">       Family or shared household library</option>
                  <option value="creative">     Creative work or portfolio</option>
                  <option value="developer">    Developer or engineering work (screenshots, diagrams, lab photos)</option>
                  <option value="student">      Student notes &amp; coursework</option>
                  <option value="research">     Research, lab notes, or personal archive</option>
                  <option value="educator">     Teaching, faculty, or course-material archive</option>
                  <option value="professional"> Work documents, receipts, contracts</option>
                  <option value="other">        Something else</option>
                </select>

                <label
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 10,
                    marginTop: 4,
                    fontSize: 13,
                    color: "var(--ink-2)",
                    cursor: "pointer",
                    lineHeight: 1.5,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={newsletter}
                    onChange={(e) => setNewsletter(e.target.checked)}
                    style={{ marginTop: 3, flexShrink: 0 }}
                  />
                  <span>
                    Also send me the weekly newsletter — release notes
                    each Friday with what shipped and why. Optional;
                    you can unsubscribe any time.
                  </span>
                </label>

                <button
                  type="submit"
                  className="btn btn--primary btn--lg"
                  style={{ marginTop: 8 }}
                  disabled={status === "submitting"}
                >
                  {status === "submitting" ? "Adding…" : "Add me to the waitlist"}
                </button>

                {status === "error" && (
                  <p style={{ fontSize: 13, color: "var(--bad)" }}>{error}</p>
                )}

                <p style={{ fontSize: 12, color: "var(--ink-3)" }}>
                  By submitting you confirm you'd like a launch email
                  {newsletter ? " plus the weekly newsletter" : ""}.
                  No sharing, no resale.
                </p>
              </form>
            )}
          </div>

          <div>
            <h3>While you wait</h3>
            <ul style={{ paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
              <li>Read the roadmap to see what we're building toward launch</li>
              <li>Check the features page for what the engine will do</li>
              <li>Compare neuthek's design to the big providers you use today</li>
              <li>Decide whether self-host or hosted is the right fit when each opens</li>
            </ul>
          </div>
        </div>
      </section>
    </>
  );
}
