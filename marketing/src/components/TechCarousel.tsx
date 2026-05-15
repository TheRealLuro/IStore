/* Tech carousel — credit to the open-source projects neuthek is
 * being built on. Each item links to that project's official docs
 * (opens in a new tab). The monochrome treatment is a deliberate
 * design choice; the marks belong to their respective owners and
 * are used nominatively as attribution. */

import { TECH_STACK } from "./Logos";

export default function TechCarousel() {
  const items = [...TECH_STACK, ...TECH_STACK]; // duplicate for seamless loop
  return (
    <div className="carousel" aria-label="Open-source projects neuthek is built on">
      <div className="carousel__track">
        {items.map(({ name, Icon, href }, i) => (
          <a
            key={`${name}-${i}`}
            className="carousel__item"
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            title={`${name} — open docs in new tab`}
          >
            <span className="carousel__logo" aria-hidden>
              <Icon size={28} />
            </span>
            <span className="carousel__name">{name}</span>
          </a>
        ))}
      </div>
    </div>
  );
}
