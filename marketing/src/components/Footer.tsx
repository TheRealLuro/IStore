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
            The next best cloud storage solution. AI-aware, privacy-first,
            in active development. Nothing publicly released yet — join
            the waitlist for the launch ping.
          </p>
        </div>
        <div>
          <h5>Product</h5>
          <ul className="footer__links">
            <li><Link to="/features">Features</Link></li>
            <li><Link to="/hosting">Hosting</Link></li>
            <li><Link to="/pricing">Pricing</Link></li>
            <li><Link to="/roadmap">Roadmap</Link></li>
          </ul>
        </div>
        <div>
          <h5>Build</h5>
          <ul className="footer__links">
            <li><Link to="/developers">Developers</Link></li>
            <li><Link to="/compare">Compare</Link></li>
            <li><Link to="/waitlist">Waitlist</Link></li>
          </ul>
        </div>
        <div>
          <h5>Legal</h5>
          <ul className="footer__links">
            <li><Link to="/privacy">Privacy</Link></li>
            <li><Link to="/terms">Terms</Link></li>
            <li><a href="mailto:hello@neuthek.example">Contact</a></li>
          </ul>
        </div>
      </div>
      <div className="container footer__bottom">
        <span>(c) {year} neuthek. Source release planned under an open-source license.</span>
        <span>Nothing released yet — coming soon</span>
      </div>
    </footer>
  );
}
