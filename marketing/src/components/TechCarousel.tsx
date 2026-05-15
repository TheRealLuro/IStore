/* Tech carousel — what neuthek is being built on.
 *
 * Each item is a real dependency in our development tree (see
 * marketing/README.md for the full stack). We render a uniform
 * monochrome logo next to each name; the logos belong to their
 * respective owners and are used here nominatively to describe
 * the technologies, not to imply endorsement or affiliation. */

import { TECH_STACK } from "./Logos";

export default function TechCarousel() {
  const items = [...TECH_STACK, ...TECH_STACK]; // duplicate for seamless loop
  return (
    <div className="carousel" aria-label="Technologies neuthek is being built on">
      <div className="carousel__track">
        {items.map(({ name, Icon }, i) => (
          <span className="carousel__item" key={`${name}-${i}`}>
            <span className="carousel__logo" aria-hidden>
              <Icon size={26} />
            </span>
            <span className="carousel__name">{name}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
