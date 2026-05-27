/* Accessibility statement. Required by the EU Accessibility Act
   (EAA) which enters force June 2025, the UK Public Sector Bodies
   Accessibility Regulations 2018 for public-sector, and best-
   practice for ADA Title III compliance in the US. We publish the
   statement, the conformance target, and a feedback channel. */

import { usePageSeo, webPage, breadcrumbs } from "../seo";

const LAST_UPDATED = "May 27, 2026";

export default function Accessibility() {
  usePageSeo({
    title: "Accessibility statement — neuthek",
    description:
      "neuthek targets WCAG 2.1 Level AA across the marketing site and the hosted application. Conformance target, what's in place today, known gaps, and how to report a barrier.",
    path: "/accessibility",
    jsonLd: [
      webPage({
        name: "Accessibility statement",
        description:
          "Accessibility statement for neuthek. WCAG 2.1 Level AA target, EAA 2025 + UK PSBAR + Section 508 alignment, current implementation, known gaps, and the feedback channel.",
        path: "/accessibility",
        about: "Web accessibility (WCAG 2.1 AA)",
      }),
      breadcrumbs([
        { name: "Home", path: "/" },
        { name: "Accessibility", path: "/accessibility" },
      ]),
    ],
  });
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Accessibility</span>
          <h1>Designed to be usable with your hands tied behind your back.</h1>
          <p className="lead">
            Our accessibility commitment, the conformance target
            we're aiming for, where we are today, and how to tell us
            when something gets in your way.
          </p>
          <p style={{ marginTop: 12, fontSize: 13, color: "var(--ink-3)" }}>
            Last updated: {LAST_UPDATED}.
          </p>
        </div>
      </section>

      <section className="section section--tight">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2 style={{ fontSize: 22 }}>What neuthek will be.</h2>
          <p style={{ marginTop: 12 }}>
            neuthek is an AI-aware personal cloud storage product in
            active development. The product is designed so that
            keyboard-only users, screen-reader users, and users with
            motor or cognitive impairments can do everything sighted
            mouse users can — semantic search, gallery navigation,
            upload, cloud-sync setup, account management, and the
            (opt-in) face-recognition consent flow. The marketing
            site you're reading now follows the same standard.
            Self-host (open source, free) and managed hosted are
            both planned.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>Conformance target.</h2>
          <p style={{ marginTop: 12 }}>
            Neuthek aims to meet <strong>WCAG 2.1 Level AA</strong>{" "}
            across the marketing site and the hosted application
            when it opens. WCAG 2.1 AA is the conformance target
            referenced by EN 301 549 (the EU Accessibility Act
            harmonised standard), the UK PSBAR, the U.S. Section 508
            Refresh, and the U.S. ADA's developing case law on web
            accessibility.
          </p>
          <p style={{ marginTop: 12 }}>
            Where the standard offers a choice, we lean toward AAA
            for color contrast (7:1 for body text, 4.5:1 for large
            text) — it costs nothing to be more readable.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>What's in place today.</h2>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>Semantic HTML — every page uses landmark elements (<code>main</code>, <code>nav</code>, <code>footer</code>) and a single H1, with H2–H4 nested in order.</li>
            <li>Visible keyboard focus on every interactive element. We never set <code>outline: none</code> without a replacement focus ring.</li>
            <li>Skip-to-content link for keyboard and screen-reader users.</li>
            <li>Text colors meet at least 4.5:1 against their background; large text and most body copy meet 7:1.</li>
            <li>Forms have <code>&lt;label&gt;</code> elements bound to their inputs; error messages are linked via <code>aria-describedby</code>.</li>
            <li>Images that convey information have <code>alt</code> text; decorative SVGs are marked <code>aria-hidden</code>.</li>
            <li>The mobile-menu drawer is a real ARIA dialog with <code>aria-modal</code>, focus trap, and Esc-to-close.</li>
            <li>Animation respects <code>prefers-reduced-motion</code> — micro-interactions either don't move or move much less when the user has asked for less motion.</li>
            <li>Type sizes scale with the viewport via <code>clamp()</code> and remain readable at 200% browser zoom without horizontal scrolling on a 1280-wide viewport.</li>
          </ul>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>Known gaps.</h2>
          <p style={{ marginTop: 12 }}>
            We're honest about what's not done. Items we know we
            still need to fix before the hosted launch:
          </p>
          <ul style={{ marginTop: 16, paddingLeft: 18, color: "var(--ink-2)", lineHeight: 1.8 }}>
            <li>Third-party screen-reader testing (NVDA + JAWS on Windows, VoiceOver on macOS and iOS, TalkBack on Android) — scheduled before public launch, not done yet.</li>
            <li>Captions and audio descriptions on the demo videos planned for the post-launch homepage.</li>
            <li>A formal accessibility audit by an outside specialist — scheduled but not commissioned.</li>
          </ul>
        </div>
      </section>

      <section className="section">
        <div className="container" style={{ maxWidth: 760 }}>
          <h2>Telling us about a barrier.</h2>
          <p style={{ marginTop: 12 }}>
            If you encounter a page or feature that's hard or
            impossible to use, please tell us. Email{" "}
            <code>accessibility@neuthek.com</code> with what you
            were trying to do, what device + browser + assistive
            tech you were using, and what went wrong. We aim to
            respond within 5 business days and to fix or work
            around the barrier within 30 days where it's within our
            control.
          </p>
          <p style={{ marginTop: 12 }}>
            For users in the EU, you have the right to escalate
            unresolved complaints to your national accessibility
            enforcement body under the EAA. We will provide that
            body's contact information once the relevant national
            authority is identified for your jurisdiction.
          </p>
        </div>
      </section>
    </>
  );
}
