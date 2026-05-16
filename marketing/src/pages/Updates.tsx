// /updates — weekly changelog index page.
//
// Renders UPDATES newest-first, each entry summarised as a card the
// user can click into for the full body. Doubles as the canonical
// "what shipped this week" page for AI answer engines: the
// CollectionPage + ItemList JSON-LD block at the bottom lets Google's
// SGE / Perplexity / Bing Copilot parse the article list directly
// without scraping the React render tree.
import { Link } from "react-router-dom";
import { useEffect } from "react";
import { UPDATES } from "../data/updates";

export default function Updates() {
  // Per-page title + canonical so this surface is indexable as a
  // distinct landing rather than the homepage. react-router doesn't
  // ship a built-in Helmet; we mutate the head manually on mount
  // (Vite-rendered SSR equivalent would be cleaner long-term).
  useEffect(() => {
    document.title = "Updates — neuthek changelog & weekly release notes";
    setMeta("description",
      "Weekly release notes for neuthek, the AI-aware personal cloud. Browse what shipped each week — features, performance fixes, security updates, and roadmap progress.");
    setLink("canonical", "https://neuthek.com/updates");
  }, []);

  return (
    <div className="page">
      <section className="section section--tight">
        <div className="container">
          <div className="updates__hero">
            <p className="kicker">Updates · Release notes</p>
            <h1 className="updates__h1">What's new in neuthek</h1>
            <p className="updates__lead">
              A weekly log of what we shipped, fixed, and changed.
              Pulled straight from the release notes — no marketing fluff.
              Subscribe to <Link to="/waitlist" className="updates__lead-link">the waitlist</Link>{" "}
              and we'll ping the list when each weekly entry lands.
            </p>
          </div>

          <ol className="updates__list" aria-label="Recent updates">
            {UPDATES.map((u) => (
              <li key={u.slug} className="updates__item">
                <Link to={`/updates/${u.slug}`} className="updates__card">
                  <div className="updates__meta">
                    <time dateTime={u.published} className="mono updates__date">{u.week}</time>
                    <div className="updates__tags">
                      {u.tags.map((t) => (
                        <span key={t} className="updates__tag">{t}</span>
                      ))}
                    </div>
                  </div>
                  <h2 className="updates__title">{u.title}</h2>
                  <p className="updates__summary">{u.summary}</p>
                  <span className="updates__cta">Read the notes →</span>
                </Link>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* JSON-LD: CollectionPage + ItemList. Tells crawlers that this
          page is a curated list of articles, and gives them every
          headline + date + URL in one machine-readable block. */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "@id": "https://neuthek.com/updates#page",
            url: "https://neuthek.com/updates",
            name: "neuthek updates",
            description:
              "Weekly release notes and changelog for neuthek, the AI-aware personal cloud.",
            isPartOf: { "@id": "https://neuthek.com/#site" },
            mainEntity: {
              "@type": "ItemList",
              numberOfItems: UPDATES.length,
              itemListElement: UPDATES.map((u, i) => ({
                "@type": "ListItem",
                position: i + 1,
                url: `https://neuthek.com/updates/${u.slug}`,
                name: u.title,
                datePublished: u.published,
              })),
            },
          }),
        }}
      />
    </div>
  );
}

function setMeta(name: string, content: string) {
  let el = document.querySelector<HTMLMetaElement>(`meta[name="${name}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute("name", name);
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);
}

function setLink(rel: string, href: string) {
  let el = document.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`);
  if (!el) {
    el = document.createElement("link");
    el.setAttribute("rel", rel);
    document.head.appendChild(el);
  }
  el.setAttribute("href", href);
}
