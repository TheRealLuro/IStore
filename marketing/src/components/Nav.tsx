import { NavLink, Link } from "react-router-dom";
import { useEffect, useState } from "react";
import WordMark from "./WordMark";

const links = [
  { to: "/features",   label: "Features" },
  { to: "/hosting",    label: "Hosting" },
  { to: "/developers", label: "Developers" },
  { to: "/roadmap",    label: "Roadmap" },
  { to: "/updates",    label: "Updates" },
  { to: "/compare",    label: "Compare" },
  { to: "/faq",        label: "FAQ" },
];

export default function Nav() {
  const [open, setOpen] = useState(false);

  // Close the menu on viewport changes — the desktop nav becomes
  // visible at the breakpoint and an open mobile drawer would shadow
  // it. Also close on outside-click + Escape so the dropdown behaves
  // like every other lightweight menu the user has used.
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onClickOutside = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t) return;
      if (t.closest(".nav__drawer") || t.closest(".nav__burger")) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("resize", close);
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", close);
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <header className="nav">
      <div className="nav__inner">
        <Link to="/" className="nav__brand" onClick={() => setOpen(false)}>
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
        <button
          type="button"
          className="nav__burger"
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
          aria-controls="nav-drawer"
          onClick={() => setOpen((v) => !v)}
        >
          <span className={`nav__burger-bar${open ? " is-x1" : ""}`} />
          <span className={`nav__burger-bar${open ? " is-x2" : ""}`} />
          <span className={`nav__burger-bar${open ? " is-x3" : ""}`} />
        </button>
      </div>

      {open && (
        <div
          id="nav-drawer"
          className="nav__drawer"
          role="dialog"
          aria-modal="true"
          aria-label="Site navigation"
        >
          <nav className="nav__drawer-links" onClick={() => setOpen(false)}>
            {links.map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                className={({ isActive }) =>
                  `nav__drawer-link${isActive ? " nav__drawer-link--active" : ""}`
                }
              >
                {l.label}
              </NavLink>
            ))}
            <Link to="/waitlist" className="nav__drawer-cta">
              Join waitlist
            </Link>
          </nav>
        </div>
      )}
    </header>
  );
}
