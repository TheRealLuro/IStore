// Weekly update entries shown on /updates and /updates/:slug.
//
// Each entry is a self-contained article-ish summary of what shipped
// in a given week. The list is rendered newest-first on /updates;
// /updates/:slug renders the body markdown-ish content as a long-read
// page with an SEO + AI-crawler-friendly Article JSON-LD block.
//
// Adding an entry:
//   1. Append a new object at the top of UPDATES below.
//   2. `slug` must be unique and URL-safe (lowercased, dashes).
//   3. Keep `title` under ~70 chars so it fits in SERP previews.
//   4. `summary` shows on the index list AND becomes the meta
//      description on the detail page — write it for both humans
//      and Google AI Overview / Perplexity snippets.
//   5. `tags` group entries on the index page; the sitemap also
//      surfaces them as keywords.
//
// We deliberately keep the entries inline (not a CMS) so the marketing
// site stays fully static — Render / Cloudflare Pages can serve it as
// a CDN'd bundle with no DB lookup at request time.

export type UpdateSection =
  | { kind: "para"; text: string }
  | { kind: "heading"; text: string }
  | { kind: "bullets"; items: string[] }
  | { kind: "code"; language?: string; body: string };

export interface UpdateEntry {
  slug: string;            // URL slug, e.g. "2026-w20-search-and-faces"
  title: string;           // article title (under ~70 chars)
  published: string;       // ISO date, e.g. "2026-05-16"
  week: string;            // human label, e.g. "Week of May 16, 2026"
  summary: string;         // one-paragraph summary (SEO description)
  author?: string;
  tags: string[];          // e.g. ["search", "faces", "performance"]
  sections: UpdateSection[];
}

export const UPDATES: UpdateEntry[] = [
  {
    slug: "2026-w20-google-link-marquee-perf",
    title: "Link Google to an existing account, faster marquee select, AI summary drain",
    published: "2026-05-16",
    week: "Week of May 16, 2026",
    summary:
      "Settings → Account now has a Link Google button so you can attach Google sign-in to an account you originally created with email + password. The gallery's drag-rectangle select is rewritten for smoother scroll on 200+ card grids, AI summaries now actually drain (cloud-synced files were stranded), and code-file previews open in a PDF-style modal with syntax highlighting.",
    author: "neuthek team",
    tags: ["accounts", "performance", "ai", "ui"],
    sections: [
      { kind: "heading", text: "Link your Google account from Settings" },
      {
        kind: "para",
        text:
          "If you signed up with email and a password, you couldn't use Sign-in-with-Google to land in the same account — the SSO flow would create a fresh user. New row in Settings → Account → Sign-in & security: 'Link Google'. Click → consent screen → your existing account picks up the Google identity. Future Sign-in-with-Google lands you back here. Unlink any time.",
      },
      { kind: "heading", text: "Marquee select, but smooth" },
      {
        kind: "para",
        text:
          "Drag a rectangle across the gallery to multi-select. Previously this measured every card on every pointermove (1000 Hz on some hardware) and forced React re-renders per id toggled — visibly lagged on 100+ card grids. The rewrite measures cards once at dragstart, throttles to one rAF tick, and paints the rubber-band via direct DOM transform. Per-frame cost dropped from ~12 ms to ~0.5 ms on a 200-card grid.",
      },
      { kind: "heading", text: "AI summaries finally drain" },
      {
        kind: "para",
        text:
          "Cloud-synced photos were marked pending_summary=true but nothing was pushing them into the ml-worker queue. The summarize-progress endpoint now drains up to 8 pending rows per poll (Redis dedupe keeps a stalled image from being re-enqueued forever), and the cloud-sync AI opt-in toggle enqueues every newly-eligible image at the moment you flip it on. The counter moves the moment you click.",
      },
      { kind: "heading", text: "Code previews as a PDF-style modal" },
      {
        kind: "para",
        text:
          "GitHub repos used to ingest images only. They now pull source code, configs, and markdown too — and when you open one in the preview, you get a dedicated viewer with syntax highlighting (Prism, ~40 grammars eagerly loaded), line numbers, and a 5 MB render cap so a 100 MB JSON dump doesn't freeze the tab.",
      },
      { kind: "heading", text: "Camera RAW + animated GIFs" },
      {
        kind: "para",
        text:
          "NEF / CR2 / ARW / DNG / RAF / ORF / RW2 / PEF now decode through rawpy (LibRaw) at quality 95 instead of Pillow's tiny embedded preview re-encoded at WebP 82. Original RAW lives in the originals bucket; served version is full sensor data. Animated GIFs are passthrough end-to-end — neither validation nor compression collapse them to single frames anymore.",
      },
      { kind: "heading", text: "Smaller fixes" },
      {
        kind: "bullets",
        items: [
          "Trash actually populates: DELETE /images/{id} soft-deletes by default; ?purge=true permanently removes.",
          "Photos / Videos / Documents sidebar tabs are now cross-folder, not folder-scoped. Counts updated to match.",
          "Face relabel no longer cascades: changing one detection clones the Face row instead of renaming the whole Person.",
          "Gallery cards request a max_dim=600 thumbnail variant (server-cached LRU) instead of the full 4 MB served WebP.",
        ],
      },
    ],
  },
  {
    slug: "2026-w19-drive-sync-and-rls",
    title: "Google Drive sync, row-level security, EXIF strip on upload",
    published: "2026-05-09",
    week: "Week of May 9, 2026",
    summary:
      "End-to-end Google Drive sync with OAuth 2.0, PKCE, encrypted refresh tokens, hourly background sweep, and conflict detection. Postgres row-level security now fences every per-user query at the database layer. EXIF metadata is stripped on upload by default (opt-in to keep camera GPS or make/model).",
    tags: ["cloud-sync", "security", "privacy"],
    sections: [
      { kind: "heading", text: "Pull-only Drive sync" },
      {
        kind: "para",
        text:
          "Connect a Google Drive in Settings → Cloud sync. We request only drive.readonly — we never write to your Drive. Files mirror into a Google Drive folder in neuthek; folder structure is preserved. Refresh tokens are encrypted with Fernet before they hit the database, conflict detection (local edited after sync) shows a banner instead of overwriting, and an hourly sweeper keeps the link warm.",
      },
      { kind: "heading", text: "Limited Use compliance" },
      {
        kind: "para",
        text:
          "Per Google's Limited Use policy, Drive content cannot be used to train AI models. The summarize + face-scan pipelines skip cloud-synced rows by default (skip_ai_training=true). The cloud-sync panel exposes a per-source AI Enable/Pause toggle so you can opt in explicitly — that flip both stamps the link's ai_opted_in column and re-queues every image.",
      },
      { kind: "heading", text: "Postgres RLS" },
      {
        kind: "para",
        text:
          "Migration 0027 turned on FORCE row-level security across the tables that hold user content: images, image_geo, image_tags, folders, folder_tags, tags, face_detections, faces, persons, audit_log. Every query runs under SET LOCAL app.current_user_id, and policies pin reads/writes to that user. A leaked query that forgot the user_id WHERE clause now returns zero rows instead of cross-tenant data.",
      },
      { kind: "heading", text: "EXIF privacy" },
      {
        kind: "para",
        text:
          "Uploads strip the EXIF block by default. We re-encode JPEG / WebP / TIFF through Pillow without the APP1 marker — no embedded GPS, no camera fingerprint. Two consent scopes let you opt back in: gps_retention (location stays for map view) and exif_retention (camera/lens stays for export). PNG and GIF don't carry EXIF, so nothing changes there.",
      },
    ],
  },
];

export function findUpdateBySlug(slug: string): UpdateEntry | undefined {
  return UPDATES.find((u) => u.slug === slug);
}

export function allTags(): string[] {
  const set = new Set<string>();
  UPDATES.forEach((u) => u.tags.forEach((t) => set.add(t)));
  return Array.from(set).sort();
}
