import { useState } from "react";
import {
  postWaitlistSignup,
  resendWaitlistVerify,
  type WaitlistUseCase,
} from "../api";

/* The form below POSTs to the marketing-site's own /api/waitlist/signup
   endpoint (served by ../server.mjs in the same Render Web Service as
   this SPA). No dependency on the main neuthek backend — the marketing
   surface is fully self-contained so it can run on Render while the
   rest of the product is still in development.

   After a successful submit, the server sends a verification email
   (Resend HTTP API when RESEND_API_KEY is configured) and we show a
   "check your inbox" state. The user must click the emailed link to
   flip `verified=true` in the DB. Until then the row stays as an
   unverified pending signup. */

export default function Waitlist() {
  const [email, setEmail] = useState("");
  const [use, setUse] = useState<WaitlistUseCase>("personal");
  // Newsletter consent — defaults to UNCHECKED. The form remains a
  // launch-pings-only signup unless the user explicitly opts in.
  const [newsletter, setNewsletter] = useState(false);
  const [status, setStatus] = useState<
    | "idle"
    | "submitting"
    | "done-check-inbox"
    | "done-already-verified"
    | "done-offline"
    | "error"
  >("idle");
  const [error, setError] = useState<string>("");
  // Dev fallback when no email service is wired up server-side — the
  // signup response includes the verify URL so the page can offer a
  // direct click-through. NEVER set in prod responses.
  const [devVerifyUrl, setDevVerifyUrl] = useState<string | null>(null);
  const [emailSent, setEmailSent] = useState<boolean>(false);
  const [resendBusy, setResendBusy] = useState<boolean>(false);
  const [resendNote, setResendNote] = useState<string>("");

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email) return;
    setStatus("submitting");
    setError("");
    setDevVerifyUrl(null);
    setResendNote("");

    try {
      const result = await postWaitlistSignup({
        email: email.trim().toLowerCase(),
        use_case: use,
        newsletter_opt_in: newsletter,
      });
      if (result.already_verified) {
        setStatus("done-already-verified");
      } else {
        setEmailSent(!!result.verification_email_sent);
        if (result.verify_url) setDevVerifyUrl(result.verify_url);
        setStatus("done-check-inbox");
      }
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
            {status === "done-check-inbox" && (
              <div>
                <h2>Check your inbox.</h2>
                <p style={{ marginTop: 16 }}>
                  {emailSent ? (
                    <>We sent a verification link to <code>{email}</code>. Click it
                    to confirm your email and lock in your spot. The link
                    is good for 7 days.</>
                  ) : (
                    <>We saved <code>{email}</code> and tried to send a verification
                    link — but the email service isn't configured for this
                    deployment yet. Your spot is held; an admin will
                    follow up.</>
                  )}
                </p>
                {devVerifyUrl && (
                  <div className="callout" style={{ marginTop: 16 }}>
                    <strong>Dev mode:</strong> the mailer isn't configured,
                    so here's the verify link directly:{" "}
                    <a href={devVerifyUrl} style={{ wordBreak: "break-all" }}>
                      {devVerifyUrl}
                    </a>
                  </div>
                )}
                {emailSent && (
                  <p style={{ marginTop: 12, fontSize: 14, color: "var(--ink-2)" }}>
                    Didn't get it? Check spam, then{" "}
                    <button
                      type="button"
                      onClick={async () => {
                        setResendBusy(true);
                        setResendNote("");
                        try {
                          await resendWaitlistVerify(email);
                          setResendNote("Resent. Give it a minute.");
                        } catch {
                          setResendNote("Couldn't resend. Try again in a few minutes.");
                        } finally {
                          setResendBusy(false);
                        }
                      }}
                      disabled={resendBusy}
                      style={{
                        textDecoration: "underline",
                        color: "var(--ink)",
                        font: "inherit",
                      }}
                    >
                      {resendBusy ? "resending…" : "resend it"}
                    </button>
                    .{resendNote && <span style={{ marginLeft: 8 }}>{resendNote}</span>}
                  </p>
                )}
              </div>
            )}

            {status === "done-already-verified" && (
              <div>
                <h2>You're already on the list.</h2>
                <p style={{ marginTop: 16 }}>
                  <code>{email}</code> is already verified. We've updated
                  your preferences. We'll email you when the hosted
                  version opens for early users and again at general
                  availability.
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
                <p>
                  We'll send a one-click verification email so we know
                  you actually own the address. Then we only email you
                  about the launch.
                </p>

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
