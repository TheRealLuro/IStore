// /features — the durable inventory of what neuthek actually does.
//
// Structure:
//   1. Hero head
//   2. Hard-guarantees strip (5 non-negotiable promises, full width)
//   3. Four hero spotlights (Search / Preview / Best Of / Drive)
//      each with a small security-note line at the bottom
//   4. Nine-category features grid (~70 tiles) — each category gets
//      a footer security-note italic line
//   5. "Hard guarantees expanded" — three cards on the WHY behind
//      the top-strip promises
//   6. Coming next — four cards including the open-source self-host
//      future commitment (no timing claim)
//   7. Closing CTA — hosted launches first, self-host comes after
//
// Average-user copy, plain English, no jargon. Every line below
// describes a real shipped capability — the changelog in updates.ts
// is the source of truth.

import { Link } from "react-router-dom";

interface Tile {
  title: string;
  body: string;
}

interface CategorySection {
  id: string;
  eyebrow: string;
  heading: string;
  tiles: Tile[];
  securityNote: string;
}

const GUARANTEES: { eyebrow: string; body: string }[] = [
  {
    eyebrow: "No AI training on your library",
    body:
      "We never use your photos, videos, documents, embeddings, or " +
      "summaries to train any model. Not ours. Not anyone else's. Ever.",
  },
  {
    eyebrow: "No ads. Anywhere. Ever",
    body:
      "There will never be advertising on neuthek. Not in the gallery, " +
      "not in emails, not in search results, not in shared previews.",
  },
  {
    eyebrow: "No data sales",
    body:
      "Your data isn't a product. We don't sell it to brokers, " +
      "partners, ad networks, or anyone else.",
  },
  {
    eyebrow: "Every AI feature is opt-in",
    body:
      "Face recognition, location retention, summary generation, " +
      "semantic search — each is off until you turn it on. Granular " +
      "per-scope, revocable any time.",
  },
  {
    eyebrow: "Strictly user-first",
    body:
      "Every decision starts with \"is this better for the user\". " +
      "Always.",
  },
];

const CATEGORIES: CategorySection[] = [
  {
    id: "find",
    eyebrow: "Search",
    heading: "Find anything in seconds",
    tiles: [
      { title: "Semantic search", body: "Describe what you remember — \"snowy roof at sunset\" — and get matches even if the filename says IMG_0420." },
      { title: "Search as you type", body: "Results refresh while you're typing. No \"press Enter and wait\" — just keep typing as you narrow down." },
      { title: "Synonym-aware", body: "\"Vibrant\" finds photos tagged \"colorful\" or \"vivid\". \"Sunset\" matches \"golden hour\" and \"dusk\"." },
      { title: "Filter by scene", body: "Indoor, outdoor, beach, classroom, whiteboard, restaurant — one click each." },
      { title: "Filter by content type", body: "Photos, screenshots, documents, illustrations, video. Mix and match." },
      { title: "Filter by people", body: "Once you've named someone, every future photo of them is one click away." },
      { title: "Filter by location", body: "On a real Leaflet map. Click a pin to open the photo." },
      { title: "Star the keepers", body: "Hit ★ on anything you want to find later. \"Starred\" view is one click in the sidebar." },
      { title: "Reverse-geocoded places", body: "Photos show \"Big Sur, CA\" not raw lat/lng — names looked up from coordinates automatically." },
      { title: "Recent searches dropdown", body: "Past queries one click away. Clears on demand." },
      { title: "Cross-folder type tabs", body: "Photos / Videos / Documents in the sidebar count your whole library, not just the current folder." },
    ],
    securityNote:
      "Each query fenced to your user_id by Postgres FORCE Row-Level " +
      "Security — there is no shared global index. Face + location " +
      "filters require explicit per-scope consent.",
  },
  {
    id: "preview",
    eyebrow: "Inline preview",
    heading: "Open anything without downloading",
    tiles: [
      { title: "Image lightbox", body: "Click any photo for full-resolution view with zoom, pan, and arrow-key next/prev." },
      { title: "Multi-page PDF stack", body: "Every page rendered server-side, lazy-loaded as you scroll. Retina-crisp, no flashing on fast scroll." },
      { title: "Syntax-highlighted code", body: "Open .py / .js / .rs / Dockerfile and ~40 more languages with proper color coding — Prism under the hood." },
      { title: "Inline GPS pin", body: "Photos with EXIF coordinates show their place name right in the preview panel." },
      { title: "Tag + star + share inline", body: "Manage everything about a file from the same panel — no second modal." },
      { title: "Esc-stacked modals", body: "Open a PDF inside the preview pane; Esc closes the PDF first, then the preview." },
      { title: "Drag-and-drop to folders", body: "Drag a file onto any folder card to move it. No menus, no clicks." },
      { title: "Folder breadcrumbs", body: "Click any ancestor folder in the trail to jump back. Works with browser back/forward too." },
    ],
    securityNote:
      "Every preview fetches through a JWT-gated route — no public " +
      "links unless you create them. Even shared previews go through " +
      "signed URLs with a TTL you control.",
  },
  {
    id: "organize",
    eyebrow: "Organise",
    heading: "Stay organized without effort",
    tiles: [
      { title: "Drag-rectangle select", body: "Click empty space and drag across the gallery to select a batch. Faster than ctrl-clicking each card." },
      { title: "Create a folder from selection", body: "Pick files, click \"New folder from selection\", and they're moved into it in one atomic operation." },
      { title: "Bulk move + delete + restore", body: "Same multi-select bar handles every bulk operation. Confirms before anything destructive." },
      { title: "Soft Trash, 30-day restore", body: "Deletes go to Trash for 30 days. Restore individually or bulk; permanent purge when you mean it." },
      { title: "Folder hierarchy", body: "Nest folders as deep as you want. Breadcrumbs always show the way back." },
      { title: "Tags with color chips", body: "Per-user tags with 18 named colors. Filter by tag, attach via popover, recolor anytime." },
      { title: "Archive uploads extracted", body: "Drop a .zip, .tar, .7z, or .rar and we extract it into a folder for you. Path traversal blocked at the gate." },
      { title: "Smart rename", body: "Path separators rejected, Windows-reserved names rejected, 255-byte cap. Safe filenames every time." },
      { title: "Star favorites", body: "Quick ★ for anything you want pinned. Starred view is library-wide." },
      { title: "Detect Person batch flow", body: "Multi-select photos, type a name, and we auto-cluster every detected face into that person." },
    ],
    securityNote:
      "Archive uploads inspected before extraction — path traversal " +
      "blocked, depth + expansion ratio capped. Soft Trash keeps a " +
      "30-day recovery window before anything is truly gone.",
  },
  {
    id: "ai",
    eyebrow: "AI",
    heading: "AI that does the busy work",
    tiles: [
      { title: "Auto-summaries", body: "Every photo gets a caption + topic tags + bullet points. Lives in the search index too." },
      { title: "AI filename suggestions", body: "Click \"Suggest names\" to get three rename options built from the photo's actual content." },
      { title: "Best Of picker", body: "Multi-select 2-30 photos, click Pick Best Of — AI ranks them on sharpness, exposure, face quality, and your chosen use case." },
      { title: "Use-case scoring", body: "25 preset categories (Portrait, Sunset, Document, Pet, ...) plus a free-text input — type \"vintage car\" and we'll match." },
      { title: "Burst clustering", body: "Best Of in \"burst\" mode groups visually similar shots and picks one keeper per cluster." },
      { title: "Scene + content classification", body: "Indoor/outdoor, screenshot vs photo, scene labels — all populated at upload, all filter-chip ready." },
      { title: "Content-aware compression", body: "Lossless WebP on screenshots and documents (text matters); high-quality WebP on photos." },
      { title: "Reclassify on demand", body: "Library Maintenance → Reclassify re-runs the classifier on photos that skipped vision at upload." },
      { title: "Re-summarize the library", body: "After a model upgrade, regenerate every summary in one click." },
      { title: "Summarising banner with self-heal", body: "Live \"Summarising N of M\" counter at the top while AI catches up — auto-clears stuck rows." },
    ],
    securityNote:
      "Every AI feature is independently opt-in. Work runs on the " +
      "server you control (self-host) or your single-tenant fence " +
      "(hosted). Models are pre-trained, frozen weights from public " +
      "datasets — we never fine-tune anything on your library.",
  },
  {
    id: "formats",
    eyebrow: "Formats",
    heading: "Every format just works",
    tiles: [
      { title: "HEIC, AVIF, JPEG, PNG, WebP", body: "All the modern photo formats decoded server-side and served as compressed WebP for delivery." },
      { title: "Camera RAW", body: "NEF, CR2, ARW, DNG, RAF, ORF, RW2, PEF — decoded via LibRaw into the full sensor image, not the embedded preview." },
      { title: "Animated GIFs preserved", body: "Frame-by-frame passthrough. No flattening to a single frame, ever." },
      { title: "Video", body: "MP4, MOV, WebM, MKV with frame-grab thumbnails so you can see what you've got without playback." },
      { title: "PDFs", body: "Every page rasterised + the text extracted into the search index. Search \"lease agreement\" and the PDF surfaces." },
      { title: "Source code", body: "40+ languages with inline syntax highlighting in a PDF-style modal. Python, JS, Rust, Go, Dockerfile, Markdown, more." },
      { title: "Archives extracted", body: ".zip, .tar, .7z, .rar all inspected and unpacked into folders at upload time." },
    ],
    securityNote:
      "Every upload validated by MIME + magic-byte + re-decode " +
      "before storage. EXIF stripped on the way in unless you opt " +
      "to keep GPS or camera data.",
  },
  {
    id: "connect",
    eyebrow: "Cloud sync",
    heading: "Connect what you already have",
    tiles: [
      { title: "Google Drive sync", body: "Read-only OAuth with PKCE. We can never write, move, or delete files on your Drive." },
      { title: "One-click connect", body: "Click Connect, approve on Google's screen, done. Refresh token encrypted at rest with Fernet." },
      { title: "Auto folder mirroring", body: "Your Drive folder tree appears under a top-level \"Google Drive\" folder, preserving the hierarchy." },
      { title: "Hourly auto-sync", body: "Background sweep pulls new files every hour. \"Sync now\" button for impatient moments." },
      { title: "Per-source AI opt-in", body: "Drive content stays out of AI training by default (Google Limited Use). Flip the toggle per source." },
      { title: "Conflict banner", body: "Edit something locally after Drive last synced? Banner shows the affected files so you can resolve." },
      { title: "Sign in with Google", body: "Authenticate with your Google account — no separate password to remember." },
      { title: "Link Google to existing account", body: "Already signed up with email + password? Link your Google account from Settings; both sign-in paths land in the same library." },
      { title: "OneDrive, iCloud, Dropbox", body: "On the roadmap. Drive is first because it's where most users start." },
    ],
    securityNote:
      "Drive scope is `drive.readonly` only — we cannot write, move, " +
      "or delete files on your Drive. Refresh tokens encrypted with " +
      "Fernet at rest. Revoking neuthek from your Google account " +
      "terminates access immediately.",
  },
  {
    id: "share",
    eyebrow: "Sharing",
    heading: "Share when you mean to",
    tiles: [
      { title: "Per-image share grants", body: "Pick exactly who gets the link. Per-recipient, not a global \"anyone with the URL\"." },
      { title: "Pick a TTL", body: "1 hour, 1 day, 7 days, or 30 days. After expiry the link 404s automatically." },
      { title: "Recipients don't need an account first", body: "They see a preview page with the file and a \"Create account to keep it\" button." },
      { title: "Public preview pages", body: "Minimal landing for the unauthenticated. No gallery walk, no profile exposure." },
      { title: "Recipient claim flow", body: "Once they sign in with the matching email, the share binds to their account permanently." },
      { title: "Revoke any time", body: "One click. The recipient's next access 404s — nothing in their browser cache can recover it." },
      { title: "Active-shares dashboard", body: "See every share you've created, who got it, when it expires, and revoke per row." },
      { title: "Full audit trail", body: "Every grant + revoke logged with IP + user-agent. Append-only log; can't be tampered with." },
    ],
    securityNote:
      "Every share link carries a signed token with a fixed expiry. " +
      "Revoked grants 404 on the next access. New recipients are " +
      "clamped to a 1-day window until they claim the share into an " +
      "account, regardless of what TTL the sharer picked.",
  },
  {
    id: "account",
    eyebrow: "Account & security",
    heading: "Account, sign-in & security",
    tiles: [
      { title: "Email + password", body: "Argon2id password hashing — the modern, memory-hard standard. Brute force is computationally infeasible." },
      { title: "Sign in with Google (SSO)", body: "One-click sign-in. No second password to manage, same library either way." },
      { title: "Link Google to existing account", body: "Settings → Account → Sign-in & security → Link Google. Both paths land in the same account." },
      { title: "TOTP 2-factor authentication", body: "Scan a QR code in any authenticator app, then 2FA gates every future login." },
      { title: "Recovery codes", body: "Eight single-use codes emailed once at 2FA setup. Stash them somewhere safe — they unlock the account if your authenticator is lost." },
      { title: "Password reset", body: "Forgot it? Reset via a signed email link, no old password required." },
      { title: "Email-change re-verification", body: "Both the old and new email get pinged before the change takes effect." },
      { title: "Brute-force lockout", body: "Failed login attempts rate-limited per IP with exponential backoff. Real users never notice; bots get pushed back to minutes between tries." },
      { title: "Activity log", body: "Every action on your account, with timestamp + IP. Spot anything weird, hit \"Sign out everywhere\"." },
    ],
    securityNote:
      "Passwords hashed with Argon2id. TOTP secrets encrypted with " +
      "a Fernet key the API container never logs. Failed auth " +
      "attempts rate-limited per IP with exponential lockout windows.",
  },
  {
    id: "rules",
    eyebrow: "Control",
    heading: "Your library, your rules",
    tiles: [
      { title: "Per-feature consent toggles", body: "AI summary / semantic search / face recognition / GPS retention / EXIF retention / telemetry — each independent, each revocable." },
      { title: "Real face-data counts", body: "Settings shows the actual number of face templates, persons named, faces detected. No marketing-speak placeholders." },
      { title: "Real location-data counts", body: "Same for GPS — see exactly how many points are stored before you decide whether to keep the consent on." },
      { title: "Storage breakdown", body: "Sidebar shows usage by Photos / Videos / Documents / Other. Know what's eating your quota at a glance." },
      { title: "Account export ZIP", body: "Download every file + every embedding + every summary + your audit log in one ZIP. GDPR Article 20 by default." },
      { title: "Account deletion — 30-day grace", body: "Schedule deletion and you have 30 days to cancel. After the window, every byte is hard-deleted: blobs, embeddings, face templates, share grants." },
      { title: "Account deletion — instant purge", body: "Don't want to wait? Instant purge wipes everything in one transaction." },
      { title: "EXIF stripped by default", body: "Upload a photo and EXIF (including GPS) is removed unless you opted to keep it. You opt in; we don't default-keep." },
    ],
    securityNote:
      "Every per-user table fenced by FORCE Row-Level Security at " +
      "the database layer — even a bug in app code can't cross-leak. " +
      "Deleting your account hard-deletes every blob, embedding, " +
      "face template, audit row, and share grant.",
  },
];

export default function Features() {
  return (
    <>
      {/* ===== Hero head ===== */}
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Features</span>
          <h1>Everything you can do, in one place.</h1>
          <p className="lead">
            Not a wishlist — every capability below is shipped in the
            engine today. Self-host opens after the hosted launch.
          </p>
        </div>
      </section>

      {/* ===== Hard guarantees strip ===== */}
      <section className="section section--tight">
        <div className="container">
          <span className="eyebrow">Our promises</span>
          <h2 style={{ marginTop: 4 }}>Five lines we will not bend.</h2>
          <div className="guarantees-strip">
            {GUARANTEES.map((g) => (
              <div key={g.eyebrow} className="guarantee">
                <div className="guarantee__eyebrow">{g.eyebrow}</div>
                <div className="guarantee__body">{g.body}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ===== Spotlight 1: Semantic search ===== */}
      <section className="section">
        <div className="container split">
          <div>
            <span className="eyebrow">Search</span>
            <h2>Search by what you remember.</h2>
            <p style={{ marginTop: 16 }}>
              Ask in the words you'd actually use. <code>"snowy roof
              at sunset"</code>. <code>"receipt with a coffee
              stain"</code>. <code>"the trail with the red
              bridge"</code>. We embed your query into the same
              visual space as your photos and return the nearest
              matches — no filenames or tags required.
            </p>
            <p style={{ marginTop: 12 }}>
              Results update while you type. Synonyms work
              automatically — searching <code>"vibrant"</code> matches
              photos whose captions say <code>"colorful"</code> or{" "}
              <code>"vivid"</code>. For diligence readers: CLIP image
              embeddings + Postgres full-text search, blended.
            </p>
            <p className="security-note">
              Search is per-user — your queries never see another
              tenant's data. There is no shared global index. Search
              history lives in your browser's localStorage and clears
              with one click.
            </p>
          </div>
          <div className="code-card">
            <div className="code-card__chrome">
              <span className="code-card__dots"><span/><span/><span/></span>
              <span className="code-card__title">search.flow</span>
              <span className="code-card__lang">flow</span>
            </div>
            <pre className="code"><span className="tok-c"># What you type → what comes back</span>{`
`}<span className="tok-p">you&gt;</span> <span className="tok-s">"snowy roof at sunset"</span>{`
       ↓
`}<span className="tok-n">visual + text understanding</span>{`
       ↓
`}<span className="tok-p">app&gt;</span> top matches sorted by relevance{`

`}<span className="tok-c"># Synonym-aware too</span>{`
`}<span className="tok-p">you&gt;</span> <span className="tok-s">"vibrant"</span>{`
       → matches `}<span className="tok-s">"colorful"</span>, <span className="tok-s">"vivid"</span>, <span className="tok-s">"bright"</span>
            </pre>
          </div>
        </div>
      </section>

      {/* ===== Spotlight 2: Inline preview ===== */}
      <section className="section">
        <div className="container split">
          <div className="code-card">
            <div className="code-card__chrome">
              <span className="code-card__dots"><span/><span/><span/></span>
              <span className="code-card__title">preview.types</span>
              <span className="code-card__lang">supported</span>
            </div>
            <pre className="code"><span className="tok-c"># Everything previews in your browser:</span>{`
`}<span className="tok-k">images</span>      <span className="tok-p">→</span> zoom, pan, arrow-key navigation{`
`}<span className="tok-k">PDFs</span>        <span className="tok-p">→</span> multi-page stack, lazy-loaded{`
`}<span className="tok-k">code</span>        <span className="tok-p">→</span> 40+ languages syntax-highlighted{`
`}<span className="tok-k">RAW</span>         <span className="tok-p">→</span> NEF / CR2 / ARW / DNG decoded{`
`}<span className="tok-k">animated GIF</span> <span className="tok-p">→</span> every frame preserved{`
`}<span className="tok-k">video</span>       <span className="tok-p">→</span> frame-grab thumbnails{`
`}<span className="tok-k">archives</span>    <span className="tok-p">→</span> .zip / .tar / .7z extracted{`

`}<span className="tok-c"># All from the same panel.</span>
            </pre>
          </div>
          <div>
            <span className="eyebrow">Inline preview</span>
            <h2>Open it, don't download it.</h2>
            <p style={{ marginTop: 16 }}>
              Click any file in your library and it opens — in your
              browser, full quality, no download required. Images
              get a lightbox with zoom and arrow-key navigation.
              PDFs get a multi-page stack that lazy-loads as you
              scroll. Source code opens with syntax highlighting
              for ~40 languages. Geotagged photos show their place
              name (\"Big Sur, CA\") right in the panel.
            </p>
            <p style={{ marginTop: 12 }}>
              Tag, star, rename, share — all from the same panel.
              Esc closes nested modals in the right order so a PDF
              inside the preview pane doesn't trap you.
            </p>
            <p className="security-note">
              Every preview fetches through a JWT-gated route — no
              public links unless you explicitly create them. Even
              shared previews go through signed URLs with a TTL you
              control.
            </p>
          </div>
        </div>
      </section>

      {/* ===== Spotlight 3: Best Of ===== */}
      <section className="section">
        <div className="container split">
          <div>
            <span className="eyebrow">Best Of</span>
            <h2>Pick the best, trash the rest.</h2>
            <p style={{ marginTop: 16 }}>
              Multi-select 2 to 30 similar photos, click{" "}
              <strong>Pick best of burst</strong>, and we score every
              frame on sharpness, exposure, and face quality — plus an
              optional use case ("vintage car", "sunset", "portrait")
              that biases the scoring toward what you're looking for.
            </p>
            <p style={{ marginTop: 12 }}>
              25 preset use cases (Portrait, Sunset, Document, Pet,
              ...) or type your own. Override the AI's pick with any
              other frame. One click on{" "}
              <strong>Keep this one</strong> moves the rest to Trash
              (restorable for 30 days).
            </p>
            <p className="security-note">
              Scoring runs against your library only. No embeddings,
              scores, or selections leave your tenant. Losers move to
              Trash (soft-delete) so a misclick is recoverable for 30
              days.
            </p>
          </div>
          <div className="code-card">
            <div className="code-card__chrome">
              <span className="code-card__dots"><span/><span/><span/></span>
              <span className="code-card__title">best-of.scoring</span>
              <span className="code-card__lang">live</span>
            </div>
            <pre className="code"><span className="tok-c"># For each frame you selected:</span>{"\n"}<span className="tok-k">sharpness</span>   <span className="tok-n">▓▓▓▓▓▓▓▓▓░</span>  <span className="tok-n">93</span>{"\n"}<span className="tok-k">exposure</span>    <span className="tok-n">▓▓▓▓▓▓▓░░░</span>  <span className="tok-n">71</span>{"\n"}<span className="tok-k">face quality</span> <span className="tok-n">▓▓▓▓▓▓▓▓▓▓</span>  <span className="tok-n">98</span>{"\n"}<span className="tok-k">use case</span>    <span className="tok-n">▓▓▓▓▓▓▓▓░░</span>  <span className="tok-n">82</span>  <span className="tok-c"># "portrait"</span>{"\n                       ─────\n"}<span className="tok-k">overall</span>     <span className="tok-n">▓▓▓▓▓▓▓▓▓░</span>  <span className="tok-n">88</span>  <span className="tok-c"># top pick</span>
            </pre>
          </div>
        </div>
      </section>

      {/* ===== Spotlight 4: Google Drive ===== */}
      <section className="section">
        <div className="container split">
          <div className="code-card">
            <div className="code-card__chrome">
              <span className="code-card__dots"><span/><span/><span/></span>
              <span className="code-card__title">drive.sync</span>
              <span className="code-card__lang">setup</span>
            </div>
            <pre className="code"><span className="tok-c"># One-time setup:</span>{`
`}<span className="tok-p">1.</span> Settings → Cloud sync → Connect Google Drive{`
`}<span className="tok-p">2.</span> Approve on Google's screen{`
`}<span className="tok-p">3.</span> Done — your Drive appears as a folder{`

`}<span className="tok-c"># From then on:</span>{`
`}<span className="tok-k">hourly</span>   auto-sync pulls new files{`
`}<span className="tok-k">manual</span>   "Sync now" button for impatient moments{`
`}<span className="tok-k">conflicts</span> banner lists anything diverged{`
`}<span className="tok-k">AI</span>        off by default per source{`

`}<span className="tok-c"># Scope: drive.readonly — we cannot write</span>{`
`}<span className="tok-c"># to your Drive. Ever.</span>
            </pre>
          </div>
          <div>
            <span className="eyebrow">Cloud sync</span>
            <h2>Connect what you already have — without giving it away.</h2>
            <p style={{ marginTop: 16 }}>
              Google Drive sync brings your existing library in
              without manual upload. One-click OAuth, read-only
              scope (we literally cannot write back to your Drive),
              encrypted refresh tokens, an hourly background sweep,
              and a conflict banner when local and remote diverge.
              Your Drive folder tree appears as a "Google Drive"
              folder inside neuthek with the same hierarchy.
            </p>
            <p style={{ marginTop: 12 }}>
              Per-source AI toggle: Drive content is fenced out of
              AI training by default (Google Limited Use compliance).
              Flip it on per source if you want summaries and face
              detection on synced files. OneDrive, iCloud, and
              Dropbox are on the roadmap.
            </p>
            <p className="security-note">
              Drive scope is <code>drive.readonly</code> only — we
              cannot write, move, or delete files on your Drive. The
              refresh token is encrypted with Fernet at rest with a
              key never shipped to the browser. Revoking neuthek from
              your Google account drops sync immediately.
            </p>
          </div>
        </div>
      </section>

      {/* ===== Nine-category features grid ===== */}
      <section className="section">
        <div className="container">
          <span className="eyebrow">Full inventory</span>
          <h2>Everything else, organized.</h2>
          <p className="lead" style={{ marginTop: 12, marginBottom: 24 }}>
            Tap a category to expand. Each one ends with the security
            stance specific to those features — we don't bury this.
          </p>

          {CATEGORIES.map((cat) => (
            <details key={cat.id} id={cat.id} className="cat-disclosure">
              <summary className="cat-disclosure__summary">
                <span className="cat-disclosure__eyebrow">{cat.eyebrow}</span>
                <span className="cat-disclosure__heading">{cat.heading}</span>
                <span className="cat-disclosure__count">{cat.tiles.length}</span>
                <span className="cat-disclosure__chevron" aria-hidden="true">
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="4 6 7 9 10 6"/></svg>
                </span>
              </summary>
              <div className="cat-disclosure__body">
                <div className="features-grid">
                  {cat.tiles.map((t) => (
                    <div key={t.title} className="feature-tile">
                      <div className="feature-tile__title">{t.title}</div>
                      <div className="feature-tile__body">{t.body}</div>
                    </div>
                  ))}
                </div>
                <p className="security-note">{cat.securityNote}</p>
              </div>
            </details>
          ))}
        </div>
      </section>

      {/* ===== Hard guarantees expanded ===== */}
      <section className="section">
        <div className="container">
          <span className="eyebrow">Why these promises matter</span>
          <h2>The business model that lets us mean it.</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            We make these promises not because we're noble, but
            because they're structurally compatible with how we
            actually make money.
          </p>
          <div className="cards">
            <div className="card">
              <h3>Why "no AI training on your library" matters</h3>
              <p>
                The industry default is "we may use your content to
                improve our services". We don't, because that turns
                your private library into someone else's training
                data. Models we run are pre-trained, frozen weights
                from public datasets. Nothing about your library
                updates any model — ours or third-party.
              </p>
            </div>
            <div className="card">
              <h3>Why "no ads. anywhere. ever." matters</h3>
              <p>
                Ads need profiling. Profiling needs your data. The
                whole "user-as-product" business model is incompatible
                with private storage. We charge for the hosted product
                and self-host is yours to run; that's the business
                model.
              </p>
            </div>
            <div className="card">
              <h3>Why "no data sales" matters</h3>
              <p>
                The big breaches that dominate headlines are usually
                data brokers exfiltrating ad-network data. By never
                selling, never sharing, never enriching against
                third-party datasets, we shrink the attack surface
                an attacker could even target.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ===== Coming next ===== */}
      <section className="section">
        <div className="container">
          <span className="eyebrow">Coming next</span>
          <h2>On the roadmap.</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            What's still on the way. None of these are in the
            engine yet.
          </p>
          <div className="cards">
            <div className="card">
              <h3>Real-time collaboration</h3>
              <p>
                Multi-cursor document editing, comments pinned to
                specific regions of a PDF or slide. Ideal for team
                accounts; planned via y.js + a relay WebSocket.
              </p>
            </div>
            <div className="card">
              <h3>Multi-data-type platform</h3>
              <p>
                Passwords vault (end-to-end encrypted with a key the
                server never sees), contacts (vCard import), game
                saves, IoT time-series. Search will work across types
                so "Jason" finds the contact, the photos of him, and
                any document that names him.
              </p>
            </div>
            <div className="card">
              <h3>More hardware paths</h3>
              <p>
                AMD ROCm wheels for Linux self-hosters, OpenVINO
                inference for Intel NPU + iGPU, 4-bit quantization so
                the engine runs on smaller GPUs. Detection + dispatch
                are in today; the model conversion work is next.
              </p>
            </div>
            <div className="card">
              <h3>Open-source self-host</h3>
              <p>
                We're working toward publishing source for the
                self-host build. No committed date yet — we'd rather
                get the cleanup right than rush a half-finished tree
                out the door. Hosted launches first.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ===== Closing CTA ===== */}
      <section className="section section--ink">
        <div className="container">
          <h2>Hosted launches first.</h2>
          <p style={{ marginTop: 12, maxWidth: 640 }}>
            Join the waitlist to get the early-access ping. Self-host
            comes after — same engine, you run the hardware.
          </p>
          <p style={{ marginTop: 24 }}>
            <Link
              to="/waitlist"
              className="btn btn--ghost btn--lg"
              style={{ borderColor: "rgba(255,255,255,0.3)", color: "var(--surface)" }}
            >
              Join the waitlist
            </Link>
          </p>
        </div>
      </section>
    </>
  );
}
