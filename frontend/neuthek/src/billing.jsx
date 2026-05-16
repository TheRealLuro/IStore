// Billing UI — pricing page + Embedded Checkout + post-redirect return.
//
// The route dispatch in main.tsx maps three paths here:
//   /billing               → <BillingPage/>          pricing cards
//   /billing/checkout      → <BillingCheckoutPage/>  embedded Stripe form
//   /billing/return        → <BillingReturnPage/>    post-Stripe-redirect
//
// Stripe Embedded Checkout: instead of redirecting to checkout.stripe.com,
// we mount the form inside our own page via @stripe/react-stripe-js.
// The backend creates a Session with `ui_mode="embedded"` and returns a
// `client_secret`; the FE feeds that to <EmbeddedCheckoutProvider/> and
// Stripe renders the form inside a Stripe-managed iframe. PCI scope stays
// minimal — card data never touches our domain — but the chrome around
// the form is ours.
//
// Source of truth for tier flip is the webhook, NOT the return URL. The
// return page polls /billing/subscription until the tier changes, then
// shows a success state and sends the user back to /.
//
// Theme: all colors come from the CSS variable scheme defined in
// frontend/neuthek/styles.css (`:root` for light, `html[data-theme="dark"]`
// for dark). No hardcoded `#fff` / `#111` fallbacks — those would pin
// the color in both themes. main.tsx applies `data-theme` before this
// file mounts so the first paint is in the right scheme.
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { loadStripe } from "@stripe/stripe-js";
import {
  EmbeddedCheckoutProvider,
  EmbeddedCheckout,
} from "@stripe/react-stripe-js";
import toast from "react-hot-toast";

import { useAuthStore } from "@/stores/authStore";
import {
  listPlans,
  getSubscription,
  createCheckout,
  openPortal,
} from "@/api/billing";

const fmtBytes = (bytes) => {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
};

const fmtPrice = (cents) => {
  if (cents == null) return "—";
  return `$${(cents / 100).toFixed(cents % 100 === 0 ? 0 : 2)}`;
};

// ----- Auth bootstrap shared by the three pages -----
//
// Pricing is public — viewing it doesn't require sign-in. The auth
// click-through gating happens at the upgrade button + on the
// /billing/checkout route (Stripe's session create requires auth).

function useAuthBoot() {
  const user = useAuthStore((s) => s.user);
  const loading = useAuthStore((s) => s.loading);
  const bootstrap = useAuthStore((s) => s.bootstrap);
  React.useEffect(() => { bootstrap(); }, [bootstrap]);
  return { user, loading };
}

function Shell({ children, title, subtitle }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        background: "var(--surface-2)",
        color: "var(--ink)",
        padding: "48px 24px",
      }}
    >
      <div style={{ maxWidth: 1080, margin: "0 auto" }}>
        <div style={{ marginBottom: 28 }}>
          <a
            href="/"
            style={{
              fontSize: 13,
              color: "var(--ink-3)",
              textDecoration: "none",
            }}
          >
            ← Back to neuthek
          </a>
          <h1
            style={{
              fontSize: 32,
              fontWeight: 700,
              marginTop: 16,
              letterSpacing: "-0.01em",
              color: "var(--ink)",
            }}
          >
            {title}
          </h1>
          {subtitle && (
            <p style={{ color: "var(--ink-2)", marginTop: 8, fontSize: 15 }}>
              {subtitle}
            </p>
          )}
        </div>
        {children}
      </div>
    </div>
  );
}

// ----- /billing — pricing cards -----

export function BillingPage() {
  const { user } = useAuthBoot();
  const [interval, setInterval] = React.useState("monthly");

  const { data: plans } = useQuery({
    queryKey: ["billing-plans"],
    queryFn: listPlans,
    staleTime: 60_000,
  });
  const { data: sub } = useQuery({
    queryKey: ["billing-subscription"],
    queryFn: getSubscription,
    staleTime: 30_000,
    enabled: !!user,
  });

  const currentTier = sub?.tier || "free";

  const onUpgrade = async (tier) => {
    if (!user) {
      // Encode the inner URL so its `?` and `&` stay part of the
      // `next` value rather than reading as outer query separators
      // — without this, `tier` and `interval` evaporated on the
      // bounce back from auth.
      const dest = `/billing/checkout?tier=${encodeURIComponent(tier)}&interval=${encodeURIComponent(interval)}`;
      window.location.href = `/?next=${encodeURIComponent(dest)}`;
      return;
    }
    try {
      await createCheckout(tier, interval);
      window.location.href = `/billing/checkout?tier=${tier}&interval=${interval}`;
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not start checkout");
    }
  };

  const onManage = async () => {
    try {
      const { url } = await openPortal(window.location.origin + "/");
      window.location.href = url;
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not open billing portal");
    }
  };

  return (
    <Shell
      title="Pick a plan"
      subtitle="Pay for the storage and rate limits you need. Cancel any time from the customer portal — every invoice and receipt lives in your Stripe account."
    >
      <IntervalToggle value={interval} onChange={setInterval} />
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: 16,
          marginTop: 24,
        }}
      >
        {(plans || []).map((p) => (
          <PlanCardOnPage
            key={p.tier}
            plan={p}
            interval={interval}
            current={currentTier === p.tier}
            onUpgrade={() => onUpgrade(p.tier)}
            onManage={onManage}
            disabled={
              p.tier !== "free" &&
              ((interval === "monthly" && !p.monthly_available) ||
                (interval === "annual" && !p.annual_available))
            }
            stripeConfigured={!!sub?.stripe_configured}
          />
        ))}
      </div>
      <p
        style={{
          marginTop: 40,
          fontSize: 13,
          color: "var(--ink-3)",
          textAlign: "center",
        }}
      >
        Prices in USD. Tax (where applicable) shown at checkout. Cards processed
        by Stripe — neuthek never sees your card number.
      </p>
    </Shell>
  );
}

function IntervalToggle({ value, onChange }) {
  return (
    <div
      style={{
        display: "inline-flex",
        padding: 4,
        background: "var(--surface-3)",
        border: "1px solid var(--line)",
        borderRadius: 999,
        gap: 4,
      }}
    >
      {[
        ["monthly", "Monthly"],
        ["annual", "Annual — save ~17%"],
      ].map(([key, label]) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          style={{
            padding: "8px 18px",
            borderRadius: 999,
            border: "none",
            background: value === key ? "var(--surface)" : "transparent",
            boxShadow: value === key ? "var(--shadow-1)" : "none",
            fontWeight: value === key ? 600 : 500,
            fontSize: 13,
            cursor: "pointer",
            color: "var(--ink)",
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function PlanCardOnPage({ plan, interval, current, onUpgrade, onManage, disabled, stripeConfigured }) {
  const isFree = plan.tier === "free";
  const cents = interval === "annual" ? plan.annual_cents : plan.monthly_cents;
  const cadence = interval === "annual" ? "/year" : "/month";
  const featured = plan.tier === "pro";

  let ctaLabel = "Upgrade";
  let ctaAction = onUpgrade;
  let ctaDisabled = disabled;
  let ctaReason = "";
  let ctaPrimary = true;

  if (current && !isFree) {
    ctaLabel = "Manage subscription";
    ctaAction = onManage;
    ctaDisabled = false;
  } else if (current && isFree) {
    ctaLabel = "Current plan";
    ctaDisabled = true;
  } else if (isFree) {
    ctaLabel = "Always free";
    ctaDisabled = true;
  } else if (!stripeConfigured) {
    ctaLabel = "Not available yet";
    ctaDisabled = true;
    ctaReason = "Billing isn't configured on this deployment.";
  } else if (disabled) {
    ctaLabel = "Not available";
    ctaReason = "This plan isn't set up in Stripe yet.";
  }

  return (
    <div
      style={{
        background: "var(--surface)",
        border: featured
          ? "2px solid var(--ink)"
          : "1px solid var(--line)",
        borderRadius: 16,
        padding: 28,
        position: "relative",
        display: "flex",
        flexDirection: "column",
        gap: 16,
        boxShadow: featured ? "var(--shadow-2)" : "var(--shadow-1)",
      }}
    >
      {featured && (
        <span
          style={{
            position: "absolute",
            top: -12,
            left: 24,
            background: "var(--ink)",
            color: "var(--surface)",
            fontSize: 11,
            fontWeight: 600,
            padding: "4px 10px",
            borderRadius: 999,
            letterSpacing: "0.04em",
          }}
        >
          MOST POPULAR
        </span>
      )}
      <div>
        <div
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "var(--ink-3)",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          {plan.display_name}
        </div>
        <div style={{ marginTop: 12, display: "flex", alignItems: "baseline", gap: 6 }}>
          <span
            style={{
              fontSize: 36,
              fontWeight: 700,
              letterSpacing: "-0.02em",
              color: "var(--ink)",
            }}
          >
            {isFree ? "$0" : fmtPrice(cents)}
          </span>
          {!isFree && <span style={{ color: "var(--ink-3)", fontSize: 14 }}>{cadence}</span>}
          {isFree && <span style={{ color: "var(--ink-3)", fontSize: 14 }}>forever</span>}
        </div>
        {interval === "annual" && plan.monthly_cents && plan.annual_cents && (
          <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>
            {fmtPrice(plan.monthly_cents)}/mo billed annually
            {" — save "}
            {fmtPrice(plan.monthly_cents * 12 - plan.annual_cents)}/yr
          </div>
        )}
      </div>
      <ul
        style={{
          listStyle: "none",
          padding: 0,
          margin: 0,
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        <Feature label={`${fmtBytes(plan.quota_bytes)} storage`} on />
        <Feature label={`${plan.upload_max_per_hour.toLocaleString()} uploads/hour`} on />
        <Feature label={`${fmtBytes(plan.upload_max_bytes_per_day)} /day upload`} on />
        <Feature label="Semantic search + AI summaries" on={!!plan.features.ai_summaries} />
        <Feature label="Sharing + recipient pinning" on={!!plan.features.sharing} />
        <Feature label="Priority inference queue" on={!!plan.features.priority_queue} />
        <Feature label="Audit log export" on={!!plan.features.audit_export} />
        {plan.features.b2b_migration && <Feature label="B2B migration tools" on />}
      </ul>
      <button
        onClick={ctaAction}
        disabled={ctaDisabled}
        style={{
          marginTop: "auto",
          padding: "12px 16px",
          borderRadius: 10,
          border: ctaDisabled ? "1px solid var(--line)" : "none",
          background: ctaDisabled
            ? "var(--surface-3)"
            : "var(--ink)",
          color: ctaDisabled ? "var(--ink-3)" : "var(--surface)",
          fontWeight: 600,
          fontSize: 14,
          cursor: ctaDisabled ? "default" : "pointer",
        }}
      >
        {ctaLabel}
      </button>
      {ctaReason && (
        <div style={{ fontSize: 11, color: "var(--ink-3)", textAlign: "center" }}>
          {ctaReason}
        </div>
      )}
    </div>
  );
}

function Feature({ label, on }) {
  return (
    <li
      style={{
        display: "flex",
        gap: 10,
        alignItems: "center",
        fontSize: 14,
        color: on ? "var(--ink-2)" : "var(--ink-3)",
        opacity: on ? 1 : 0.6,
      }}
    >
      <span
        style={{
          width: 16,
          height: 16,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          color: on ? "var(--success)" : "var(--ink-3)",
          fontWeight: 600,
        }}
      >
        {on ? "✓" : "—"}
      </span>
      <span>{label}</span>
    </li>
  );
}

// ----- /billing/checkout — Embedded Checkout iframe -----

export function BillingCheckoutPage() {
  const { user, loading } = useAuthBoot();
  const params = new URLSearchParams(window.location.search);
  const tier = params.get("tier") || "pro";
  const interval = params.get("interval") || "monthly";
  const [clientSecret, setClientSecret] = React.useState(null);
  const [publishableKey, setPublishableKey] = React.useState(null);
  const [error, setError] = React.useState(null);
  // After 12s with no client_secret, surface a "still working" banner so
  // the user sees there's a problem rather than staring at
  // "Preparing secure checkout…" forever. The Stripe SDK can hang
  // indefinitely on flaky networks or blocked third-party content.
  const [slow, setSlow] = React.useState(false);

  React.useEffect(() => {
    if (loading || !user) return;
    let cancelled = false;
    setSlow(false);
    const slowTimer = setTimeout(() => { if (!cancelled) setSlow(true); }, 12_000);
    createCheckout(tier, interval)
      .then((r) => {
        if (cancelled) return;
        setClientSecret(r.client_secret);
        setPublishableKey(r.publishable_key);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e?.detail || e?.message || "Could not start checkout");
      });
    return () => { cancelled = true; clearTimeout(slowTimer); };
  }, [loading, user, tier, interval]);

  React.useEffect(() => {
    if (!loading && !user) window.location.href = "/?next=/billing";
  }, [loading, user]);

  const stripePromise = React.useMemo(
    () => (publishableKey ? loadStripe(publishableKey) : null),
    [publishableKey],
  );

  // Theme-aware Stripe Appearance options so the iframe doesn't read
  // white-on-light-grey in dark mode. We read the live CSS variables
  // from <html data-theme>; Stripe doesn't see our CSS, so we marshal
  // the resolved colors at render time.
  const appearance = React.useMemo(() => {
    if (typeof document === "undefined") return undefined;
    const root = document.documentElement;
    const styles = getComputedStyle(root);
    const isDark = root.getAttribute("data-theme") === "dark";
    const getVar = (name, fallback) => {
      const v = styles.getPropertyValue(name).trim();
      return v || fallback;
    };
    return {
      theme: isDark ? "night" : "stripe",
      variables: {
        colorPrimary:    getVar("--ink", isDark ? "#f5f5f5" : "#0a0a0a"),
        colorBackground: getVar("--surface", isDark ? "#161616" : "#ffffff"),
        colorText:       getVar("--ink", isDark ? "#f5f5f5" : "#0a0a0a"),
        colorDanger:     getVar("--danger", "#b91c1c"),
        fontFamily: '"Geist", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
        borderRadius: "10px",
      },
    };
  }, [publishableKey, clientSecret]);

  return (
    <Shell
      title="Checkout"
      subtitle={`${tier === "pro" ? "Pro" : tier === "business" ? "Business" : tier} — ${interval}`}
    >
      {error && (
        <div
          style={{
            padding: 20,
            background: "var(--danger-soft)",
            border: "1px solid var(--danger)",
            borderRadius: 12,
            color: "var(--danger)",
          }}
        >
          {error}
          <div style={{ marginTop: 12 }}>
            <a
              href="/billing"
              style={{ fontSize: 13, color: "var(--danger)" }}
            >
              ← Back to plans
            </a>
          </div>
        </div>
      )}
      {!error && (!clientSecret || !stripePromise) && (
        <div
          style={{
            padding: 40,
            textAlign: "center",
            color: "var(--ink-3)",
            background: "var(--surface)",
            border: "1px solid var(--line)",
            borderRadius: 16,
          }}
        >
          {slow ? (
            <>
              <div style={{ fontSize: 15, color: "var(--ink)", fontWeight: 500 }}>
                Stripe is taking longer than usual.
              </div>
              <div style={{ marginTop: 8, fontSize: 13 }}>
                Check your connection — if you have a script blocker or
                strict tracking-protection enabled, Stripe's checkout
                iframe can be blocked. Refresh, or head back to plans and
                try again in a moment.
              </div>
              <div style={{ marginTop: 16, display: "flex", gap: 10, justifyContent: "center" }}>
                <button
                  onClick={() => window.location.reload()}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 8,
                    background: "var(--ink)",
                    color: "var(--surface)",
                    fontWeight: 600,
                    fontSize: 13,
                    border: "none",
                    cursor: "pointer",
                  }}
                >
                  Try again
                </button>
                <a
                  href="/billing"
                  style={{ alignSelf: "center", fontSize: 13, color: "var(--ink-3)" }}
                >
                  ← Back to plans
                </a>
              </div>
            </>
          ) : (
            "Preparing secure checkout…"
          )}
        </div>
      )}
      {!error && clientSecret && stripePromise && (
        <div
          style={{
            background: "var(--surface)",
            borderRadius: 16,
            padding: 8,
            border: "1px solid var(--line)",
            boxShadow: "var(--shadow-1)",
          }}
        >
          <EmbeddedCheckoutProvider stripe={stripePromise} options={{ clientSecret, appearance }}>
            <EmbeddedCheckout />
          </EmbeddedCheckoutProvider>
        </div>
      )}
    </Shell>
  );
}

// ----- /billing/return — post-redirect; polls until tier flips -----

export function BillingReturnPage() {
  const { user, loading } = useAuthBoot();
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("session_id") || "";
  const [tier, setTier] = React.useState(null);
  const [ticks, setTicks] = React.useState(0);

  React.useEffect(() => {
    if (loading || !user) return;
    let stop = false;
    let pending = null;
    // Track ticks in a ref so the recursive `poll` reads the live value
    // (the previous code closed over `ticks` from the effect-creation
    // moment, leaving the 30-tick safety cap permanently at 0 → the
    // /billing/subscription poll never stopped).
    let tickCount = 0;
    const poll = async () => {
      try {
        const sub = await getSubscription();
        if (stop) return;
        setTier(sub.tier);
        if (sub.tier && sub.tier !== "free") return;
      } catch {}
      if (stop) return;
      tickCount += 1;
      setTicks(tickCount);
      if (tickCount < 30) {
        pending = setTimeout(poll, 1500);
      }
    };
    poll();
    return () => {
      stop = true;
      if (pending) clearTimeout(pending);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, user]);

  React.useEffect(() => {
    if (!loading && !user) window.location.href = "/?next=/billing";
  }, [loading, user]);

  const ready = tier && tier !== "free";
  const stalled = ticks >= 30 && !ready;

  return (
    <Shell
      title={ready ? `You're on ${tier === "pro" ? "Pro" : tier === "business" ? "Business" : tier}` : "Confirming your subscription…"}
      subtitle={
        ready
          ? "Welcome aboard. The quota and rate limits on your account just went up."
          : "Stripe is sending us the receipt. This page will refresh as soon as it lands."
      }
    >
      <div
        style={{
          padding: 24,
          background: "var(--surface)",
          borderRadius: 16,
          border: "1px solid var(--line)",
          boxShadow: "var(--shadow-1)",
        }}
      >
        {ready ? (
          <>
            <p style={{ fontSize: 14, color: "var(--ink-2)" }}>
              Your invoice and receipt are in your Stripe account. Manage the
              subscription, swap cards, or cancel anytime from the customer
              portal — find it under <strong>Account → Plan</strong>.
            </p>
            <p style={{ marginTop: 20 }}>
              <a
                href="/"
                style={{
                  display: "inline-block",
                  padding: "10px 18px",
                  borderRadius: 10,
                  background: "var(--ink)",
                  color: "var(--surface)",
                  textDecoration: "none",
                  fontWeight: 600,
                  fontSize: 14,
                }}
              >
                Back to your library →
              </a>
            </p>
          </>
        ) : stalled ? (
          <>
            <p style={{ fontSize: 14, color: "var(--ink-2)" }}>
              The webhook hasn't arrived yet — this usually means the operator
              hasn't configured the Stripe webhook endpoint. Your payment did
              go through; reach out and they'll provision the upgrade manually.
            </p>
            <p style={{ marginTop: 12, fontSize: 12, color: "var(--ink-3)" }}>
              Session: {sessionId.slice(0, 24)}…
            </p>
          </>
        ) : (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              color: "var(--ink-3)",
            }}
          >
            <span
              style={{
                width: 14,
                height: 14,
                borderRadius: 999,
                border: "2px solid var(--ink-3)",
                borderTopColor: "transparent",
                animation: "spin 0.9s linear infinite",
                display: "inline-block",
              }}
            />
            <span>Waiting for Stripe… ({ticks}s)</span>
          </div>
        )}
      </div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </Shell>
  );
}
