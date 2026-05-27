/* Shared SEO + AEO helper for every page on the marketing site.
 *
 * AEO = Answer-Engine Optimisation — getting ChatGPT, Claude (with
 * browse), Perplexity, Google AI Overview, and Bing Copilot to lift
 * neuthek's answers verbatim into their replies. The two big levers
 * are (1) clean per-page <title> + <meta name="description"> +
 * canonical URLs and (2) JSON-LD structured data so the AI parsers
 * can find facts without hallucinating from prose.
 *
 * Every page calls usePageSeo(...) inside a useEffect to mutate the
 * document <head>. There is no SSR — the SPA mounts and the crawlers
 * read the runtime DOM. GoogleBot, BingBot, OAI-SearchBot, and
 * PerplexityBot all execute JavaScript before reading the head
 * (verified through their public docs as of 2026). For the few
 * crawlers that don't, the index.html base tags act as a fallback.
 *
 * Keep titles under 60 chars and descriptions under 160 chars — the
 * SERP truncates beyond that. Both end with "— neuthek" so the
 * brand appears in every SERP card.
 */

import { useEffect } from "react";

const ORIGIN = "https://neuthek.com";
const SITE_NAME = "neuthek";
const DEFAULT_OG_IMAGE = `${ORIGIN}/og-cover.svg`;

export interface PageSeo {
  /** <title> — keep under 60 chars including " — neuthek" suffix. */
  title: string;
  /** <meta description> — keep under 160 chars. */
  description: string;
  /** Path-only canonical (no origin). Example: "/features". */
  path: string;
  /** og:type — "website" for normal pages, "article" for /updates/:slug. */
  ogType?: "website" | "article";
  /** Override OG image for hero pages. Default: /og-cover.svg. */
  ogImage?: string;
  /** Override Twitter card type. Default: "summary_large_image". */
  twitterCard?: "summary" | "summary_large_image";
  /** Optional JSON-LD schema.org blocks to inject. */
  jsonLd?: Record<string, unknown>[];
  /** Article meta (when ogType === "article"). */
  article?: {
    publishedTime?: string;
    modifiedTime?: string;
    author?: string;
    section?: string;
    tags?: string[];
  };
  /** Block indexing on this page (used by /admin, /unsubscribe). */
  noindex?: boolean;
}

/**
 * Set <head> tags for a page. Idempotent — running it twice produces
 * the same head state. Cleans up any JSON-LD blocks we added so
 * navigating between pages doesn't accumulate schema.
 */
export function usePageSeo(seo: PageSeo): void {
  useEffect(() => {
    const url = `${ORIGIN}${seo.path}`;
    const ogImage = seo.ogImage || DEFAULT_OG_IMAGE;

    document.title = seo.title;
    setMeta("description", seo.description);
    setMeta("robots", seo.noindex ? "noindex, nofollow" : "index, follow, max-image-preview:large");
    setLink("canonical", url);

    // Open Graph (Facebook, LinkedIn, Discord, iMessage, Slack, …)
    setMeta("og:title", seo.title, "property");
    setMeta("og:description", seo.description, "property");
    setMeta("og:url", url, "property");
    setMeta("og:type", seo.ogType || "website", "property");
    setMeta("og:site_name", SITE_NAME, "property");
    setMeta("og:image", ogImage, "property");
    setMeta("og:image:alt", `${SITE_NAME} — ${seo.title}`, "property");
    setMeta("og:locale", "en_US", "property");

    // Article-specific OG (for /updates/:slug).
    if (seo.ogType === "article" && seo.article) {
      const a = seo.article;
      if (a.publishedTime) setMeta("article:published_time", a.publishedTime, "property");
      if (a.modifiedTime) setMeta("article:modified_time", a.modifiedTime, "property");
      if (a.author) setMeta("article:author", a.author, "property");
      if (a.section) setMeta("article:section", a.section, "property");
      if (a.tags) {
        // Remove any previous article:tag elements before adding new.
        document.querySelectorAll('meta[property="article:tag"]').forEach((n) => n.remove());
        a.tags.forEach((tag) => {
          const m = document.createElement("meta");
          m.setAttribute("property", "article:tag");
          m.setAttribute("content", tag);
          document.head.appendChild(m);
        });
      }
    }

    // Twitter Cards (also picked up by some AI crawlers as fallback).
    setMeta("twitter:card", seo.twitterCard || "summary_large_image");
    setMeta("twitter:title", seo.title);
    setMeta("twitter:description", seo.description);
    setMeta("twitter:image", ogImage);
    setMeta("twitter:image:alt", `${SITE_NAME} — ${seo.title}`);

    // JSON-LD — remove our previous page-scoped blocks and re-inject.
    document.querySelectorAll('script[data-seo="page"]').forEach((n) => n.remove());
    (seo.jsonLd || []).forEach((block) => {
      const s = document.createElement("script");
      s.type = "application/ld+json";
      s.setAttribute("data-seo", "page");
      s.textContent = JSON.stringify(block);
      document.head.appendChild(s);
    });
  }, [seo.title, seo.description, seo.path, seo.ogType, seo.ogImage, seo.twitterCard, seo.noindex, JSON.stringify(seo.jsonLd), JSON.stringify(seo.article)]);
}

/**
 * BreadcrumbList JSON-LD generator. Pass the human-readable path
 * (["Home", "Legal", "Privacy"]) and the URL path (["/", "/privacy"])
 * — items are zipped 1:1. Google + Bing both render breadcrumbs
 * from this in SERPs.
 */
export function breadcrumbs(items: { name: string; path: string }[]): Record<string, unknown> {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: item.name,
      item: `${ORIGIN}${item.path}`,
    })),
  };
}

/**
 * WebPage JSON-LD. Use on every page for baseline indexing. AI
 * engines (especially Perplexity + Google AI Overview) prefer pages
 * with WebPage + a clear `about` over pure prose.
 */
export function webPage(args: {
  name: string;
  description: string;
  path: string;
  inLanguage?: string;
  about?: string;
}): Record<string, unknown> {
  return {
    "@context": "https://schema.org",
    "@type": "WebPage",
    name: args.name,
    description: args.description,
    url: `${ORIGIN}${args.path}`,
    inLanguage: args.inLanguage || "en-US",
    isPartOf: {
      "@type": "WebSite",
      name: SITE_NAME,
      url: ORIGIN,
    },
    ...(args.about ? { about: { "@type": "Thing", name: args.about } } : {}),
  };
}

/** Article JSON-LD for /updates/:slug. */
export function article(args: {
  headline: string;
  description: string;
  path: string;
  datePublished: string;
  dateModified?: string;
  author?: string;
}): Record<string, unknown> {
  return {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: args.headline,
    description: args.description,
    url: `${ORIGIN}${args.path}`,
    datePublished: args.datePublished,
    dateModified: args.dateModified || args.datePublished,
    author: {
      "@type": "Organization",
      name: SITE_NAME,
      url: ORIGIN,
    },
    publisher: {
      "@type": "Organization",
      name: SITE_NAME,
      url: ORIGIN,
    },
    mainEntityOfPage: {
      "@type": "WebPage",
      "@id": `${ORIGIN}${args.path}`,
    },
  };
}

// ---------- low-level helpers ----------

function setMeta(name: string, content: string, attr: "name" | "property" = "name") {
  if (!content) return;
  let el = document.querySelector(`meta[${attr}="${name}"]`) as HTMLMetaElement | null;
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, name);
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);
}

function setLink(rel: string, href: string) {
  let el = document.querySelector(`link[rel="${rel}"]`) as HTMLLinkElement | null;
  if (!el) {
    el = document.createElement("link");
    el.rel = rel;
    document.head.appendChild(el);
  }
  el.href = href;
}
