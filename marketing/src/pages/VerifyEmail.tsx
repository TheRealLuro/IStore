import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { verifyWaitlistEmail } from "../api";

/* Landing page reached by clicking the link in the verification email.
   Reads the `token` query param, posts it to /api/waitlist/verify, and
   shows the result. No user action required beyond the click. */

type Status =
  | { kind: "verifying" }
  | { kind: "ok"; email: string }
  | { kind: "missing-token" }
  | { kind: "invalid" }
  | { kind: "network" };

export default function VerifyEmail() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const [status, setStatus] = useState<Status>({ kind: "verifying" });

  useEffect(() => {
    document.title = "Verify email — neuthek";
    if (!token) {
      setStatus({ kind: "missing-token" });
      return;
    }
    let cancelled = false;
    verifyWaitlistEmail(token).then(
      (data) => {
        if (cancelled) return;
        setStatus({ kind: "ok", email: data.email });
      },
      (err) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : "";
        if (msg === "invalid-token") setStatus({ kind: "invalid" });
        else setStatus({ kind: "network" });
      },
    );
    return () => { cancelled = true; };
  }, [token]);

  return (
    <section className="section">
      <div className="container" style={{ maxWidth: 560 }}>
        <span className="eyebrow">Waitlist</span>

        {status.kind === "verifying" && (
          <>
            <h1 style={{ fontSize: 36, marginTop: 4 }}>Verifying your email…</h1>
            <p className="lead" style={{ fontSize: 16, marginTop: 12 }}>
              One second. We're checking the link.
            </p>
          </>
        )}

        {status.kind === "ok" && (
          <>
            <h1 style={{ fontSize: 36, marginTop: 4 }}>You're verified.</h1>
            <p className="lead" style={{ fontSize: 16, marginTop: 12 }}>
              <code>{status.email}</code> is confirmed. We'll email you when
              the hosted version opens for early users and again at general
              availability. That's it — no drip campaigns.
            </p>
            <p style={{ marginTop: 20 }}>
              <Link to="/" className="btn btn--primary btn--lg">Back to neuthek</Link>
            </p>
          </>
        )}

        {status.kind === "missing-token" && (
          <>
            <h1 style={{ fontSize: 36, marginTop: 4 }}>Missing verification link.</h1>
            <p className="lead" style={{ fontSize: 16, marginTop: 12 }}>
              This page expects a <code>?token=…</code> query string from
              the email we sent. If you copied the link by hand, paste the
              whole thing into your browser address bar.
            </p>
            <p style={{ marginTop: 20 }}>
              <Link to="/waitlist" className="btn btn--ghost">Resend the email</Link>
            </p>
          </>
        )}

        {status.kind === "invalid" && (
          <>
            <h1 style={{ fontSize: 36, marginTop: 4 }}>Link expired or invalid.</h1>
            <p className="lead" style={{ fontSize: 16, marginTop: 12 }}>
              Verification links expire after 7 days. Head back to the
              waitlist page and sign up again — we'll send a fresh link.
            </p>
            <p style={{ marginTop: 20 }}>
              <Link to="/waitlist" className="btn btn--primary btn--lg">Back to waitlist</Link>
            </p>
          </>
        )}

        {status.kind === "network" && (
          <>
            <h1 style={{ fontSize: 36, marginTop: 4 }}>Couldn't reach the server.</h1>
            <p className="lead" style={{ fontSize: 16, marginTop: 12 }}>
              The verify endpoint isn't responding right now. Try again in
              a minute. If it keeps failing, ping us from the footer.
            </p>
          </>
        )}
      </div>
    </section>
  );
}
