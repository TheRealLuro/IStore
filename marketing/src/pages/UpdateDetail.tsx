// /updates/:slug — single weekly-update article.
//
// Renders the five-bucket body (Found / Fixed / New / Why / What
// they do) plus rich JSON-LD so search engines and AI answer engines
// can lift the summary, publish date, and full body without rendering
// JS. The BreadcrumbList also gives Google the canonical
// "neuthek → Updates → <article>" trail for SERP cards.
//
// Older/newer footer nav follows reading-order intuition: older
// sits on the LEFT, newer on the RIGHT (back/forward across time).
import { useEffect } from "react";
import { Link, useParams } from "react-router-dom";
import { findUpdateBySlug, UPDATES } from "../data/updates";

export default function UpdateDetail() {
  const { slug } = useParams<{ slug: string }>();
  const entry = slug ? findUpdateBySlug(slug) : undefined;

  // UPDATES is sorted newest-first, so:
  //   - index - 1 → the entry AFTER this one (newer)
  //   - index + 1 → the entry BEFORE this one (older)
  const idx = entry ? UPDATES.findIndex((u) => u.slug === entry.slug) : -1;
  const newer = idx > 0 ? UPDATES[idx - 1] : null;
  const older = idx >= 0 && idx < UPDATES.length - 1 ? UPDATES[idx + 1] : null;

  useEffect(() => {
    if (!entry) {
      document.title = "Update not found — neuthek";
      return;
    }
    document.title = `${entry.title} — neuthek updates`;
    setMeta("description", entry.summary);
    setLink("canonical", `https://neuthek.com/updates/${entry.slug}`);
    setMeta("og:title", entry.title, "property");
    setMeta("og:description", entry.summary, "property");
    setMeta("og:type", "article", "property");
    setMeta("og:url", `https://neuthek.com/updates/${entry.slug}`, "property");
    setMeta("twitter:title", entry.title);
    setMeta("twitter:description", entry.summary);
  }, [entry]);

  if (!entry) {
    return (
      <div className="page">
        <section className="section">
          <h1>Update not found</h1>
          <p>
            We couldn't find that update.{" "}
            <Link to="/updates">Browse the changelog →</Link>
          </p>
        </section>
      </div>
    );
  }

  // Flatten the body buckets into one long text string for the
  // Article JSON-LD `articleBody` field. AI crawlers and answer
  // engines can read this without executing JS — important because
  // many of them (including some of ChatGPT's browsing modes) skip
  // pages that are pure client-side rendered until they've parsed
  // the HTML / JSON-LD.
  const articleText = [
    entry.summary,
    "",
    "Problems we found:",
    ...entry.body.found.map((s) => `- ${s}`),
    "",
    "How we fixed them:",
    ...entry.body.fixed.map((s) => `- ${s}`),
    "",
    "New features in this release:",
    ...entry.body.newFeatures.map((s) => `- ${s}`),
    "",
    "Why this work matters:",
    entry.body.why,
    "",
    "What this means for you:",
    ...entry.body.whatTheyDo.map((s) => `- ${s}`),
  ].join("\n");

  return (
    <div className="page">
      <article className="section update-article">
        <p className="kicker">
          <Link to="/updates">← All updates</Link>
        </p>
        <header className="update-article__head">
          <time dateTime={entry.published} className="mono update-article__date">
            {entry.week}
          </time>
          <h1 className="update-article__title">{entry.title}</h1>
          <p className="update-article__summary">{entry.summary}</p>
          <div className="updates__tags">
            {entry.tags.map((t) => (
              <span key={t} className="updates__tag">{t}</span>
            ))}
          </div>
        </header>

        <BucketSection
          tone="warn"
          title="Problems we found"
          icon="!"
          intro="What wasn't working as well as it should have."
          items={entry.body.found}
        />
        <BucketSection
          tone="ok"
          title="How we fixed them"
          icon="✓"
          intro="What changed in this release to fix the issues above."
          items={entry.body.fixed}
        />
        <BucketSection
          tone="new"
          title="New features"
          icon="+"
          intro="Brand-new capabilities that weren't there last week."
          items={entry.body.newFeatures}
        />
        <div className="update-bucket update-bucket--why">
          <h2 className="update-bucket__title">Why this matters</h2>
          <p className="update-bucket__intro">
            The reason this work mattered, in plain English.
          </p>
          <p className="update-bucket__why-body">{entry.body.why}</p>
        </div>
        <BucketSection
          tone="info"
          title="What this means for you"
          icon="→"
          intro="How the changes actually show up when you use neuthek."
          items={entry.body.whatTheyDo}
        />

        <footer className="update-article__foot">
          {/* Older on the LEFT, newer on the RIGHT — matches book /
              browser reading order: "earlier → later" flows
              left-to-right. */}
          {older ? (
            <Link to={`/updates/${older.slug}`} className="update-article__nav">
              ← Older: {older.title}
            </Link>
          ) : (
            <span/>
          )}
          {newer && (
            <Link to={`/updates/${newer.slug}`} className="update-article__nav update-article__nav--right">
              Newer: {newer.title} →
            </Link>
          )}
        </footer>
      </article>

      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            "@context": "https://schema.org",
            "@graph": [
              {
                "@type": "BlogPosting",
                "@id": `https://neuthek.com/updates/${entry.slug}#article`,
                headline: entry.title,
                description: entry.summary,
                articleBody: articleText,
                datePublished: entry.published,
                dateModified: entry.published,
                author: {
                  "@type": "Organization",
                  name: entry.author || "neuthek",
                  url: "https://neuthek.com/",
                },
                publisher: { "@id": "https://neuthek.com/#org" },
                isPartOf: { "@id": "https://neuthek.com/updates#page" },
                mainEntityOfPage: `https://neuthek.com/updates/${entry.slug}`,
                keywords: entry.tags.join(", "),
                inLanguage: "en",
              },
              {
                "@type": "BreadcrumbList",
                itemListElement: [
                  {
                    "@type": "ListItem",
                    position: 1,
                    name: "neuthek",
                    item: "https://neuthek.com/",
                  },
                  {
                    "@type": "ListItem",
                    position: 2,
                    name: "Updates",
                    item: "https://neuthek.com/updates",
                  },
                  {
                    "@type": "ListItem",
                    position: 3,
                    name: entry.title,
                    item: `https://neuthek.com/updates/${entry.slug}`,
                  },
                ],
              },
            ],
          }),
        }}
      />
    </div>
  );
}

function BucketSection({
  title, items, tone, icon, intro,
}: {
  title: string;
  items: string[];
  tone: "ok" | "warn" | "new" | "info";
  icon: string;
  intro: string;
}) {
  if (!items?.length) return null;
  return (
    <section className={`update-bucket update-bucket--${tone}`}>
      <h2 className="update-bucket__title">
        <span className="update-bucket__icon" aria-hidden="true">{icon}</span>
        {title}
      </h2>
      <p className="update-bucket__intro">{intro}</p>
      <ul className="update-bucket__list">
        {items.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ul>
    </section>
  );
}

function setMeta(name: string, content: string, attr: "name" | "property" = "name") {
  let el = document.querySelector<HTMLMetaElement>(`meta[${attr}="${name}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, name);
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
