// /updates/:slug — single weekly-update article.
//
// Renders the body sections + an Article-shape JSON-LD block so search
// engines and AI answer engines can pick up the headline, summary,
// publish date, and full body without needing to render JS. The
// BreadcrumbList graph entry also helps Google show the canonical
// "neuthek → Updates → <article>" trail in SERP cards.
import { useEffect } from "react";
import { Link, useParams } from "react-router-dom";
import { findUpdateBySlug, UPDATES } from "../data/updates";

export default function UpdateDetail() {
  const { slug } = useParams<{ slug: string }>();
  const entry = slug ? findUpdateBySlug(slug) : undefined;

  // Find the previous + next entries for "← prev / next →" footer nav.
  // Sorted by `published` descending in the data file, so the array
  // index is already chronological.
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
    // OG / Twitter share previews — share-rich snippet for each
    // individual article when someone posts it on social.
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
            We couldn't find that update. <Link to="/updates">Browse the changelog →</Link>
          </p>
        </section>
      </div>
    );
  }

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

        <div className="update-article__body">
          {entry.sections.map((s, i) => {
            if (s.kind === "heading") {
              return <h2 key={i} className="update-article__h2">{s.text}</h2>;
            }
            if (s.kind === "para") {
              return <p key={i} className="update-article__para">{s.text}</p>;
            }
            if (s.kind === "bullets") {
              return (
                <ul key={i} className="update-article__bullets">
                  {s.items.map((it, j) => <li key={j}>{it}</li>)}
                </ul>
              );
            }
            if (s.kind === "code") {
              return (
                <pre key={i} className="update-article__code mono"><code>{s.body}</code></pre>
              );
            }
            return null;
          })}
        </div>

        <footer className="update-article__foot">
          {newer && (
            <Link to={`/updates/${newer.slug}`} className="update-article__nav">
              ← Newer: {newer.title}
            </Link>
          )}
          {older && (
            <Link to={`/updates/${older.slug}`} className="update-article__nav update-article__nav--right">
              Older: {older.title} →
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
