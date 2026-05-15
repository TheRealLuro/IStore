import { useState } from "react";
import { postWaitlistSignup, type WaitlistUseCase } from "../api";

/* The form below POSTs to the FastAPI backend's public
   /waitlist/signup endpoint when a backend is reachable.

   When the marketing site is deployed to Render without
   VITE_API_BASE_URL pointing at a public backend (i.e. the
   pre-launch state), the POST falls through to localStorage
   so the visitor still gets confirmation and we don't lose
   the signal. The next session will replay it once the real
   endpoint is wired. */

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
  const [use, setUse] = useState<WaitlistUseCase>("personal");
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
      await postWaitlistSignup({ email: email.trim().toLowerCase(), use_case: use });
      saveLocal({ email, use, at: new Date().toISOString() });
      setStatus("done");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      // Server unreachable (e.g. on static-hosted preview without a
      // public backend yet): preserve the signal locally so the user
      // still sees confirmation, but tell them honestly.
      if (
        msg === "offline" ||
        msg.startsWith("server-error") ||
        msg === "TypeError" ||
        (err as Error)?.name === "TypeError"
      ) {
        saveLocal({ email, use, at: new Date().toISOString() });
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
                  the hosted version opens for early users.
                </p>
                <p style={{ marginTop: 12 }}>
                  In the meantime, the source code is yours to run today.
                </p>
              </div>
            )}

            {status === "done-offline" && (
              <div>
                <h2>Saved locally.</h2>
                <p style={{ marginTop: 16 }}>
                  Our signup endpoint isn't reachable from this page
                  right now, so we stored <code>{email}</code> in your
                  browser as a placeholder. When the hosted backend is
                  wired up here, we'll honor entries that were saved
                  early.
                </p>
                <p style={{ marginTop: 12, fontSize: 14, color: "var(--ink-3)" }}>
                  Running the open-source build locally? Set{" "}
                  <code>VITE_API_BASE_URL</code> to your backend and
                  redeploy.
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
                  <option value="personal">Personal photo library</option>
                  <option value="family">Shared family library</option>
                  <option value="creative">Creative work / portfolio</option>
                  <option value="research">Research / archive</option>
                  <option value="other">Something else</option>
                </select>

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
              <li>Read the roadmap to see what's shipping toward launch</li>
              <li>File issues or contribute on the public repo</li>
              <li>Check the compare table to see how it stacks up against the big providers</li>
            </ul>
          </div>
        </div>
      </section>
    </>
  );
}
