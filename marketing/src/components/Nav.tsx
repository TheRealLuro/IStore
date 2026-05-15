import { NavLink, Link } from "react-router-dom";
import WordMark from "./WordMark";

const links = [
  { to: "/features",   label: "Features" },
  { to: "/hosting",    label: "Hosting" },
  { to: "/developers", label: "Developers" },
  { to: "/roadmap",    label: "Roadmap" },
  { to: "/compare",    label: "Compare" },
  { to: "/pricing",    label: "Pricing" },
];

export default function Nav() {
  return (
    <header className="nav">
      <div className="nav__inner">
        <Link to="/" className="nav__brand">
          <WordMark />
        </Link>
        <nav className="nav__links" aria-label="Primary">
          {links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              className={({ isActive }) =>
                `nav__link${isActive ? " nav__link--active" : ""}`
              }
            >
              {l.label}
            </NavLink>
          ))}
        </nav>
        <Link to="/waitlist" className="nav__cta">Join waitlist</Link>
      </div>
    </header>
  );
}
