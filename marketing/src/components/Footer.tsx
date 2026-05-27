/* Site footer. Restructured May 2026:
   - Trust tagline (no collect / no train / no sell) is the brand
     message everywhere — surface it in the footer too.
   - Legal column expanded with the new compliance pages (Cookies,
     DMCA, AUP, Subprocessors, Accessibility, Legal Notice).
   - CCPA "Do Not Sell or Share My Personal Information" link is
     required in the footer per CPRA §1798.135 even though we do
     not sell or share — we still attest to it as the footer link.
   - Physical mailing address placeholder for CAN-SPAM compliance
     (we'll fill it once the legal entity is formed). */

import { Link } from "react-router-dom";
import WordMark from "./WordMark";

export default function Footer() {
  const year = new Date().getFullYear();
  return (
    <footer className="footer">
      <div className="container footer__grid">
        <div className="footer__brand">
          <WordMark />
          <p>
            The next best personal cloud. AI-aware, privacy-first,
            in active development.
          </p>
          <p style={{ marginTop: 12, fontSize: 13, color: "var(--ink-3)" }}>
            <strong style={{ color: "var(--ink-2)" }}>We don't collect data we don't need.</strong>
            <br />
            <strong style={{ color: "var(--ink-2)" }}>We don't train AI on your content.</strong>
            <br />
            <strong style={{ color: "var(--ink-2)" }}>We don't sell or share your data.</strong>
          </p>
        </div>

        <div>
          <h5>Product</h5>
          <ul className="footer__links">
            <li><Link to="/features">Features</Link></li>
            <li><Link to="/hosting">Hosting</Link></li>
            <li><Link to="/roadmap">Roadmap</Link></li>
            <li><Link to="/updates">Updates</Link></li>
          </ul>
        </div>

        <div>
          <h5>Build</h5>
          <ul className="footer__links">
            <li><Link to="/developers">Developers</Link></li>
            <li><Link to="/compare">Compare</Link></li>
            <li><Link to="/faq">FAQ</Link></li>
            <li><Link to="/waitlist">Waitlist</Link></li>
          </ul>
        </div>

        <div>
          <h5>Legal</h5>
          <ul className="footer__links">
            <li><Link to="/privacy">Privacy</Link></li>
            <li><Link to="/terms">Terms</Link></li>
            <li><Link to="/aup">Acceptable Use</Link></li>
            <li><Link to="/cookies">Cookies</Link></li>
            <li><Link to="/dmca">DMCA</Link></li>
            <li><Link to="/subprocessors">Subprocessors</Link></li>
            <li><Link to="/accessibility">Accessibility</Link></li>
            <li><Link to="/legal-notice">Legal notice</Link></li>
          </ul>
        </div>
      </div>

      <div className="container footer__bottom">
        <span>(c) {year} neuthek. Source release planned under an open-source license.</span>
        <span>
          {/* CPRA §1798.135 footer link. We do not sell or share —
              the link goes to the Privacy section that explains it. */}
          <Link to="/privacy#california-rights">
            Do Not Sell or Share My Personal Information
          </Link>
        </span>
      </div>

      <div className="container" style={{ paddingTop: 16, paddingBottom: 24, fontSize: 12, color: "var(--ink-3)" }}>
        {/* CAN-SPAM Act 15 U.S.C. §7704(a)(5) requires a physical
            postal address. We use the privacy contact until the
            entity is formed with a registered address. */}
        neuthek · operated by an individual developer in the United
        States · postal address published when the legal entity is
        formed · privacy@neuthek.com
      </div>
    </footer>
  );
}
