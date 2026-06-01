/* Roadmap — plain-English summary of what's working today, what's
   being built, and what's planned. Pulled from todo.md + the weekly
   /updates draft. Each item is written so a non-developer can read it
   without a glossary. The three sections collapse so the page is a
   short overview on first load; tap to expand. */

import { usePageSeo, webPage, breadcrumbs } from "../seo";

type Item = { t: string; d: string };

// Sections collapsed by default. Numbers in brackets show item count.
const SHIPPED: Item[] = [
  // --- Privacy & security ---
  {
    t: "Encrypted, in transit and at rest",
    d: "Your files travel over HTTPS and live on disk encrypted — backups too. Encryption keys never leave the server you control.",
  },
  {
    t: "Locked-down access",
    d: "Every download link expires within 5 minutes. Repeated login failures trigger a lockout. Every sensitive action is recorded in a tamper-proof log.",
  },
  {
    t: "Delete means delete",
    d: "When you delete your account, every file, thumbnail, AI summary, face template, and audit row is purged in one go. A test runs on every commit to prove it.",
  },
  {
    t: "You agree before you sign up",
    d: "The consent prompt for AI features, face recognition, and location data appears at sign-up — not buried in settings later. Granular by feature; revocable anytime.",
  },
  {
    t: "Plain-English privacy & terms",
    d: "Privacy policy, terms of service, and data-processing agreement are written so a normal person can read them. Age-13 gate at signup; cookie banner that reflects we don't set cookies.",
  },
  {
    t: "Location data stripped by default",
    d: "GPS and camera serial-number metadata are removed before storage. You can opt in to keep location or camera data — your call.",
  },
  {
    t: "Take your library with you anytime",
    d: "One-click export downloads a ZIP of your whole library — files, AI summaries, face data, audit history. No vendor lock-in.",
  },
  {
    t: "Old data ages out automatically",
    d: "Quarantined uploads, anonymous feedback, anonymised audit rows, and scheduled-delete accounts all clean themselves up on documented schedules.",
  },
  {
    t: "No secrets in the codebase",
    d: "Every commit is scanned for accidentally-committed passwords or keys before it can merge. The repo ignores every secret-shaped filename people have leaked over the years.",
  },
  {
    t: "An end-to-end encrypted Vault the server can't read",
    d: "The Vault is a drive-like, end-to-end encrypted store: files of any type in nested folders, plus secure items — passwords, notes, crypto seed phrases, cards/IDs. Everything is encrypted in your browser with a key derived from your master password (PBKDF2-SHA256 → AES-256-GCM; files use a random per-file key, chunked) before upload. The server only ever stores ciphertext — your master password and keys never reach it, so even a full database compromise reveals nothing, and no AI ever touches Vault contents. New: drop a .vcf, a password export, a card, or a note and it's parsed client-side into a structured item. Share a single item by sealing it to another account's key, or via a public link scoped to just that item — both stay end-to-end encrypted and revocable anytime. Auto-locks on idle and sign-out.",
  },

  // --- Product & UX ---
  {
    t: "Find by what you remember",
    d: "Type \"snowy roof at sunset\" and the right photo comes back — even if you've never tagged it. Search fires as you type. Synonyms expand automatically (\"vibrant\" matches \"colorful\", \"vivid\", \"bright\").",
  },
  {
    t: "Search that handles vibes and exact filenames",
    d: "Looking for \"IMG_0420\"? Found. Looking for \"cozy\"? Also found. Two different ways of finding things, blended into one ranked list.",
  },
  {
    t: "A viewer for every file type",
    d: "50+ file types open in a fitted in-browser viewer — code in 100+ languages with find-in-file, GitHub-style Markdown, live spreadsheets (xlsx/xls/ods) with sheet tabs, interactive 3D models, an EPUB reader, Jupyter notebooks, JSON/YAML/XML trees, CSV grids, browsable archives, fonts, contact + calendar cards — alongside images, video, audio, and PDF. No download-just-to-see.",
  },
  {
    t: "Pick the best of a burst",
    d: "Multi-select 2–30 similar photos. AI ranks them on sharpness, exposure, face quality, and how well they match what you're looking for. Keep the winner; the rest move to Trash with one click.",
  },
  {
    t: "Mistakes are recoverable",
    d: "Deleted items sit in Trash for 30 days before permanent removal. Delete-forever button purges in one shot if you mean it.",
  },
  {
    t: "Smarter compression than \"JPEG q85\"",
    d: "Photos use one codec; screenshots and documents use a lossless one so text stays crisp. Files end up roughly 40-70% smaller than uniform compression.",
  },
  {
    t: "Every upload is validated before storage",
    d: "File type is confirmed from the content itself (not the extension). Polyglot exploits blocked. Archives unpacked safely with strict size caps.",
  },
  {
    t: "Real folders, real tags, real renaming",
    d: "Drag-and-drop reorganization, multi-select moves, AI-suggested smart filenames, per-user color-coded tags. Status and tags are unified — same chip everywhere.",
  },
  {
    t: "Bulk actions from the gallery",
    d: "Select multiple files at once and move them, delete them, tag them, or run Best Of — all in one click.",
  },
  {
    t: "Face grouping if you want it",
    d: "Off by default. When you turn it on, faces cluster into Me + people you tag — now with fewer false splits, merge + reassign to fix a mis-group, and a durable \"not a person\" (mark a poster or reflection and it stays marked). Embeddings stay on your server; revoking consent deletes them immediately.",
  },
  {
    t: "A faster, smoother photo map",
    d: "Geotagged photos plotted on a real map with pins virtualized and reused across pan/zoom instead of redrawn from scratch — clustering at low zoom, click-to-open at high zoom, smooth even on large libraries.",
  },
  {
    t: "Tags from your AI summaries",
    d: "Descriptive tags are auto-derived from each file's AI summary — \"whiteboard\", \"receipt\", \"golden retriever\" become clickable filters with no manual tagging.",
  },
  {
    t: "Pull from Google Drive (more connectors in progress)",
    d: "Google Drive sync is fully wired: read-only OAuth (drive.readonly — never writes back), encrypted refresh tokens, hourly background sync, and a conflict banner when local and remote diverge. AI features off by default; opt in per source. Connectors for Dropbox, iCloud (Apple-ID + HSA-2), Proton Drive, and MEGA are scaffolded in the same framework and being finished ahead of launch.",
  },
  {
    t: "Sign in with Google",
    d: "Use your Google account. Link an existing email account to Google later if you want.",
  },
  {
    t: "Sign in without a password",
    d: "Magic-link email plus a 6-digit code so Gmail's link-prefetcher can't burn the token. TOTP 2FA on top of either path; recovery codes if you lose the device.",
  },
  {
    t: "Two-factor + recovery codes + regenerate",
    d: "Authenticator-app codes, printable one-time recovery codes, and a regenerate-TOTP-secret flow for when you lose access. Email-change requires re-verification.",
  },
  {
    t: "Share specific files with specific people",
    d: "Email-pinned grants, signed links that expire, revocable at any time. Every share action is logged.",
  },

  // --- Operator & scale ---
  {
    t: "One user can't slow everyone down",
    d: "The AI processing queue serves every user in rotation. A bulk job from one account doesn't starve the others.",
  },
  {
    t: "A crash-safe job queue",
    d: "Failed jobs retry with backoff, terminal failures land in a dead-letter lane instead of wedging a slot, self-healing reapers re-queue work orphaned by a crashed worker, and a per-job watchdog kills anything that hangs past its time budget. One poison file can't stall the pipeline.",
  },
  {
    t: "Per-user storage quotas with backpressure",
    d: "A real ceiling per account. Hit it and work is refused cleanly with an \"over quota\" signal the UI acts on, so a runaway import can't fill the disk.",
  },
  {
    t: "AI features can't be abused",
    d: "Every heavy AI endpoint has a per-user hourly cap. Generous for everyday use; painful to script.",
  },
  {
    t: "Operator dashboard",
    d: "If you're hosting it yourself, the admin panel shows a live capacity calculator, queue depth per user, and rate-limit headroom — so you know what your hardware can take.",
  },
  {
    t: "Works with the GPU you have",
    d: "Detects NVIDIA CUDA, Intel XPU, and Apple Metal at boot and routes AI work to whichever is available. CPU fallback if none.",
  },
  {
    t: "Waitlist confirms your email",
    d: "Launch-day pings only go to people who actually meant to sign up — not to typos.",
  },
  {
    t: "Optional weekly newsletter",
    d: "Per-recipient unsubscribe, Gmail-compliant one-click unsubscribe headers. Sign up only if you want the recap.",
  },
  {
    t: "FAQ that AI engines can quote",
    d: "A comprehensive FAQ with structured data so ChatGPT, Perplexity, and Google can deep-link individual answers.",
  },
  {
    t: "Brand identity locked in",
    d: "Same wordmark in the app sidebar, favicon, and marketing site.",
  },
];

const ACTIVE: Item[] = [
  {
    t: "Key rotation without downtime",
    d: "Today, rotating the main encryption key would orphan existing data. We're shipping a tool that re-encrypts in place so operators can rotate keys on a schedule.",
  },
  {
    t: "Filter by anything, not just file type",
    d: "Today the gallery filters by photo / video / document only. We're adding chips for location, scene (indoor / outdoor), person, and tag — all combinable in one query.",
  },
  {
    t: "Power-user search prefixes",
    d: "Type /find or /show people: <name> or /best photo of <subject> to scope what you're searching. Plain typing still works exactly as before.",
  },
  {
    t: "Summaries that read like a person wrote them",
    d: "Replacing generic AI-speak (\"person near a whiteboard\") with something specific (\"auth-flow review on a whiteboard\") using a richer scene/object recognition pass.",
  },
  {
    t: "Document summaries with jump-to-section",
    d: "Today we summarize the whole document. Adding per-section embeddings so search jumps you straight to the passage you wanted, plus OCR for scanned PDFs.",
  },
  {
    t: "Use your name, not your email",
    d: "Display-name on signup; AI summaries reference your real name instead of \"Me\"; staged-email banner when you change your account email.",
  },
  {
    t: "Tell us \"there's a person here\"",
    d: "If face detection misses someone in a photo, you flag it and we re-run with looser thresholds. If we still miss, you draw a box and that crop becomes a labeled face.",
  },
  {
    t: "AMD and Intel acceleration",
    d: "AMD GPU support for Linux self-hosters and Intel iGPU/NPU paths for laptop-class hosts. NVIDIA, Intel XPU, and Apple Metal already work.",
  },
  {
    t: "One-command self-host setup",
    d: "Detects your hardware, generates fresh secrets, brings the whole stack up. No .env wrangling — a single command for first-run.",
  },
];

const PLANNED: Item[] = [
  {
    t: "Not just photos",
    d: "Same engine and same privacy guarantees for documents, contacts, passwords, game saves, and IoT data. Search works across every type from day one.",
  },
  {
    t: "Contacts",
    d: "Import vCard / CSV. Contact photos can opt in as a face source for the existing People feature.",
  },
  {
    t: "Game saves + IoT data",
    d: "Versioned game saves with per-game retention. Time-series IoT data per device with a per-device timeline + simple chart.",
  },
  {
    t: "Comments anchored to a region",
    d: "Pin a comment to a specific page+rect on a PDF, a slide on a deck, or a time range on a video. Layers on top of the existing sharing primitive.",
  },
  {
    t: "Multi-cursor document editing",
    d: "Real-time team editing for documents (not images at first). Snapshots persisted regularly so nothing is lost.",
  },
  {
    t: "Folder-level semantic search",
    d: "Type \"the trip to Mexico\" and the folder comes up — not just the individual photos. Folder summaries roll up from child summaries.",
  },
  {
    t: "Summaries that learn how YOU phrase things",
    d: "Opt-in. A small per-user adapter learns from your search behavior so summaries match the way you talk about your photos. We never train a shared model on your library.",
  },
  {
    t: "Smaller-footprint AI models",
    d: "8-bit and 4-bit versions of the vision models so they run on smaller GPUs and even CPUs. Pick the quantisation level in a config option.",
  },
  {
    t: "A no-AI mode",
    d: "Disables all AI inference. Useful for Raspberry Pi-class hosts or users who want zero model inference on their library.",
  },
  {
    t: "Forgot-password emails",
    d: "Account recovery via email. Needs the operator to pick an email provider (Resend or SMTP) and bake it into the deployment.",
  },
  {
    t: "End-to-end encryption beyond the Vault",
    d: "The zero-knowledge Vault (Mega/Proton-class — client-side keys, we hold only ciphertext) has shipped for files + secure items, with key-wrapped sharing. What's still ahead is letting the AI-readable Drive go end-to-end too, feature-by-feature, with very clear \"this feature loses AI capability when you turn on E2E\" trade-offs. Until that lands we describe the Drive as encrypted in transit + at rest, not end-to-end — only the Vault is end-to-end today.",
  },
  {
    t: "Easy migration for teams",
    d: "Bulk import from SMB / NAS plus the cloud connectors (Google Drive today; Dropbox, iCloud, Proton, MEGA as they land). Per-source consent scopes so legal can sign off per dataset. Migration dry-run before you commit.",
  },
  {
    t: "Pricing announced with launch",
    d: "Nothing announced yet. When hosted opens, plans + pricing land on /hosting. Self-host build will be free + open-source forever.",
  },
  {
    t: "Public source release",
    d: "We're cleaning up the codebase for a public open-source drop. No committed date — hosted launches first; the open-source self-host follows when the cleanup lands.",
  },
];

const CHECKLIST: { status: "done" | "in-progress" | "owed"; title: string; body: string }[] = [
  {
    status: "done",
    title: "Security audit",
    body: "Authentication, authorisation, upload validation, storage, secret management, rate limits, and dependencies have all been reviewed. Findings documented in AUDIT.md.",
  },
  {
    status: "done",
    title: "Privacy audit",
    body: "Every stored field has a documented reason, retention, and deletion path. The account-delete integration test asserts 0 rows + 0 objects after deletion.",
  },
  {
    status: "done",
    title: "Compliance scaffolding",
    body: "Privacy, Terms, deletion, export, consent-before-signup, age gate, cookie-banner accuracy, biometric opt-in — all closed.",
  },
  {
    status: "done",
    title: "AI / ML review",
    body: "No data leaks across user accounts. No hidden biometric processing. No accidental training on your library — every model uses frozen pre-trained weights.",
  },
  {
    status: "done",
    title: "Deletion testing",
    body: "Every database table and storage bucket is covered by an integration test that uploads + deletes + asserts a clean state. Backup retention follows GDPR Article 17.",
  },
  {
    status: "done",
    title: "Encrypted backups",
    body: "Database backups are encrypted before being written to disk or shipped offsite. The admin dashboard surfaces the timestamp and size of the most recent backup.",
  },
  {
    status: "in-progress",
    title: "Infra hardening",
    body: "HTTPS, encrypted backups, and private storage buckets are all in. Operator-specific bits still owed: firewall rules, monitoring/alerting wiring, anti-malware integration.",
  },
  {
    status: "in-progress",
    title: "Secret rotation worker",
    body: "Tooling for rotating the JWT secret, database password, and storage credentials without downtime — plus a re-encrypt migration tool for the main encryption key.",
  },
  {
    status: "owed",
    title: "Threat model + external review",
    body: "A formal STRIDE-style threat-model document is owed. An external review (an outside developer + a security person + a privacy person) is booked before public launch.",
  },
];

export default function Roadmap() {
  usePageSeo({
    title: "Roadmap — what's shipped, what's coming — neuthek",
    description:
      "Plain-English roadmap: shipped capabilities (semantic search, a viewer for 50+ file types, a zero-knowledge encrypted vault with auto-import, content-aware compression, Google Drive sync, BIPA-compliant face recognition, a crash-safe job queue, per-user quotas), several in active development, more planned including end-to-end encryption beyond the vault.",
    path: "/roadmap",
    jsonLd: [
      webPage({
        name: "Roadmap",
        description:
          "Shipped, in-progress, and planned features for neuthek. Pre-launch checklist with status badges. Hosted launches first; open-source self-host follows.",
        path: "/roadmap",
        about: "Product roadmap and pre-launch checklist",
      }),
      breadcrumbs([
        { name: "Home", path: "/" },
        { name: "Roadmap", path: "/roadmap" },
      ]),
    ],
  });
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Roadmap</span>
          <h1>What's working today, what's being built, what's planned.</h1>
          <p className="lead">
            Plain English, no jargon. The product isn't released yet
            — neither the hosted version nor the open-source self-host
            build. Tap any section to expand. Hosted launches first;
            open-source self-host follows with no committed date.
          </p>
        </div>
      </section>

      <section className="section section--tight">
        <div className="container">
          <div className="roadmap-stats">
            <div className="roadmap-stat">
              <span className="roadmap-stat__num">{SHIPPED.length}</span>
              <span className="roadmap-stat__label">Working today</span>
              <span className="roadmap-stat__sub">In the engine, with tests</span>
            </div>
            <div className="roadmap-stat">
              <span className="roadmap-stat__num">{ACTIVE.length}</span>
              <span className="roadmap-stat__label">Building now</span>
              <span className="roadmap-stat__sub">In active development</span>
            </div>
            <div className="roadmap-stat">
              <span className="roadmap-stat__num">{PLANNED.length}</span>
              <span className="roadmap-stat__label">On the roadmap</span>
              <span className="roadmap-stat__sub">Designed, not yet started</span>
            </div>
          </div>
          <div
            className="roadmap-progress"
            role="img"
            aria-label={`${SHIPPED.length} shipped, ${ACTIVE.length} building, ${PLANNED.length} planned`}
          >
            <span
              className="roadmap-progress__seg roadmap-progress__seg--done"
              style={{ flexGrow: SHIPPED.length }}
            />
            <span
              className="roadmap-progress__seg roadmap-progress__seg--active"
              style={{ flexGrow: ACTIVE.length }}
            />
            <span
              className="roadmap-progress__seg roadmap-progress__seg--planned"
              style={{ flexGrow: PLANNED.length }}
            />
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <details className="cat-disclosure" open>
            <summary className="cat-disclosure__summary">
              <span className="cat-disclosure__eyebrow">Working today</span>
              <span className="cat-disclosure__heading">
                In the engine right now, with tests
              </span>
              <span className="cat-disclosure__count">{SHIPPED.length}</span>
              <span className="cat-disclosure__chevron" aria-hidden="true" />
            </summary>
            <div className="cat-disclosure__body">
              <ul className="roadmap__list">
                {SHIPPED.map((x) => (
                  <li key={x.t}><strong>{x.t}.</strong> {x.d}</li>
                ))}
              </ul>
            </div>
          </details>

          <details className="cat-disclosure">
            <summary className="cat-disclosure__summary">
              <span className="cat-disclosure__eyebrow">Building now</span>
              <span className="cat-disclosure__heading">
                In active development this sprint
              </span>
              <span className="cat-disclosure__count">{ACTIVE.length}</span>
              <span className="cat-disclosure__chevron" aria-hidden="true" />
            </summary>
            <div className="cat-disclosure__body">
              <ul className="roadmap__list">
                {ACTIVE.map((x) => (
                  <li key={x.t}><strong>{x.t}.</strong> {x.d}</li>
                ))}
              </ul>
            </div>
          </details>

          <details className="cat-disclosure">
            <summary className="cat-disclosure__summary">
              <span className="cat-disclosure__eyebrow">On the roadmap</span>
              <span className="cat-disclosure__heading">
                Designed but not yet started
              </span>
              <span className="cat-disclosure__count">{PLANNED.length}</span>
              <span className="cat-disclosure__chevron" aria-hidden="true" />
            </summary>
            <div className="cat-disclosure__body">
              <ul className="roadmap__list">
                {PLANNED.map((x) => (
                  <li key={x.t}><strong>{x.t}.</strong> {x.d}</li>
                ))}
              </ul>
            </div>
          </details>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>Pre-launch checklist.</h2>
          <p className="lead" style={{ marginTop: 12, maxWidth: 720 }}>
            We won't release publicly — open-source or hosted — until
            every item below is closed. Most are in; the remaining
            ones are mostly operator-specific or a final outside
            review.
          </p>
          <div className="checklist" style={{ marginTop: 32 }}>
            {CHECKLIST.map((c) => (
              <div key={c.title} className="checklist__card">
                <StatusPill status={c.status} />
                <h3 className="checklist__title">{c.title}</h3>
                <p className="checklist__body">{c.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section section--ink">
        <div className="container">
          <h2>About open source.</h2>
          <p style={{ maxWidth: 720, marginTop: 12 }}>
            The self-host build will be released under an open-source
            license. The same engine powers both the self-host
            distribution and the managed hosted version, so there's
            no "open core" lockout. Self-host will be free and run
            via docker-compose; hosted exists for users who'd rather
            not run their own server.
          </p>
          <p style={{ maxWidth: 720, marginTop: 12 }}>
            <strong>No committed date for the public source drop yet.</strong>{" "}
            The codebase isn't fully cleaned up for public release —
            README rewrite, comment-balance sweep, GitHub-ready docs,
            and CI lint tightening are tracked under "Public source
            release" above. Hosted launches first; the source drop
            follows when the cleanup lands.
          </p>
        </div>
      </section>
    </>
  );
}

function StatusPill({ status }: { status: "done" | "in-progress" | "owed" }) {
  const label = status === "done" ? "Done" : status === "in-progress" ? "In progress" : "Owed";
  return (
    <span className={`status-pill status-pill--${status}`}>
      <span className="status-pill__icon" aria-hidden="true">
        {status === "done" && (
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="7" fill="currentColor" />
            <path d="M4.5 8.5 L7 11 L11.5 5.5" stroke="white" strokeWidth="1.75" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
        {status === "in-progress" && (
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" fill="none" />
            <rect x="4.5" y="7.25" width="7" height="1.5" rx="0.75" fill="currentColor" />
          </svg>
        )}
        {status === "owed" && (
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" fill="none" strokeDasharray="2 2" />
          </svg>
        )}
      </span>
      <span className="status-pill__label">{label}</span>
    </span>
  );
}
