import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { unsubscribeNewsletter } from "../api";

/* Landing page reached by clicking the "Unsubscribe" link in a newsletter
   email. We GET the API endpoint with the token (server reads token →
   flips unsubscribed_at + newsletter_opt_in=false) and show the result.
   The same API endpoint also accepts POST for Gmail / Apple Mail one-
   click compliance; this SPA path is the human-friendly route. */

type Status =
  | { kind: "working" }
  | { kind: "ok"; email: string }
  | { kind: "missing-token" }
  | { kind: "invalid" }
  | { kind: "network" };

export default function Unsubscribe() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const [status, setStatus] = useState<Status>({ kind: "working" });

  useEffect(() => {
    document.title = "Unsubscribed — neuthek";
    if (!token) {
      setStatus({ kind: "missing-token" });
      return;
    }
    let cancelled = false;
    unsubscribeNewsletter(token).then(
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
        <span className="eyebrow">Newsletter</span>

        {status.kind === "working" && (
          <>
            <h1 style={{ fontSize: 36, marginTop: 4 }}>Unsubscribing…</h1>
            <p className="lead" style={{ fontSize: 16, marginTop: 12 }}>
              One second.
            </p>
          </>
        )}

        {status.kind === "ok" && (
          <>
            <h1 style={{ fontSize: 36, marginTop: 4 }}>You're unsubscribed.</h1>
            <p className="lead" style={{ fontSize: 16, marginTop: 12 }}>
              <code>{status.email}</code> won't get the weekly newsletter
              anymore. We'll still send the two launch emails you
              originally signed up for (early-access open + general
              availability). To stop those too, reply to any neuthek
              email with "remove me" and we'll delete the row entirely.
            </p>
            <p style={{ marginTop: 20 }}>
              <Link to="/" className="btn btn--primary btn--lg">Back to neuthek</Link>
            </p>
          </>
        )}

        {status.kind === "missing-token" && (
          <>
            <h1 style={{ fontSize: 36, marginTop: 4 }}>Missing unsubscribe link.</h1>
            <p className="lead" style={{ fontSize: 16, marginTop: 12 }}>
              This page expects a <code>?token=…</code> query string from
              the newsletter footer. If you copied the link by hand,
              paste the whole thing into your browser address bar.
            </p>
          </>
        )}

        {status.kind === "invalid" && (
          <>
            <h1 style={{ fontSize: 36, marginTop: 4 }}>Link expired or invalid.</h1>
            <p className="lead" style={{ fontSize: 16, marginTop: 12 }}>
              Unsubscribe links are good for a year. If yours has
              expired, reply to any neuthek email and we'll take you off
              the list manually.
            </p>
          </>
        )}

        {status.kind === "network" && (
          <>
            <h1 style={{ fontSize: 36, marginTop: 4 }}>Couldn't reach the server.</h1>
            <p className="lead" style={{ fontSize: 16, marginTop: 12 }}>
              The unsubscribe endpoint isn't responding right now. Try
              again in a minute — your address is still on the list
              until this completes.
            </p>
          </>
        )}
      </div>
    </section>
  );
}
